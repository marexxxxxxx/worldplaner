/**
 * Fallback-Pfad der eingebauten Widgets (MARAAA-42, Befund Gate-b 1).
 *
 * Der Backend-Seed (`backend/app/db.py` `seed_builtin_widgets`) legt
 * `dashboard_layout`-Zeilen nur an, wenn die Tabelle leer ist. Auf einer bereits
 * geseedeten Installation erscheinen neu hinzugekommene eingebaute Widgets
 * daher nur über den Fallback in `resolveInstances` — die drei
 * Phase-2-Widgets müssen dort aus `BUILTIN_WIDGETS` nachgezogen werden.
 *
 * Lauf: `npm test` im Ordner `frontend/` (`node --test tests/`).
 */
import test from "node:test";
import assert from "node:assert/strict";

// `app.js` ruft beim Import `main()` auf. Ein minimales `document` und ein
// `fetch`-Stub lassen den Startpfad sauber abbrechen; der abgefangene Startfehler
// wird unterdrückt (steuert den Test nicht).
globalThis.document = { createElement: () => ({}), getElementById: () => null };
console.error = () => {};

/** Response-Stub für `api.request` (`text()`) und `fetchStaticManifest` (`json()`). */
function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    text: async () => JSON.stringify(body),
    json: async () => body,
  };
}

const PHASE2_IDS = ["baikal-calendar", "grocy-stock", "mealie-mealplan"];
const SEEDED_LAYOUT_IDS = [
  "weather",
  "garmin-sleep",
  "garmin-readiness",
  "garmin-vitals",
  "garmin-workout",
  "hermes-todos",
];

/** Nicht-leeres `dashboard_layout` einer bereits geseedeten Installation. */
const SEEDED_LAYOUT = [
  { id: "layout-weather", widget_id: "weather", tab_id: "dashboard", x: 0, y: 0, w: 4, h: 4, config_override: {} },
];

function staticManifest(widgetId) {
  return {
    id: widgetId,
    name: `Widget ${widgetId}`,
    version: "1.0.0",
    category: "general",
    permissions: [],
    default_size: { w: 4, h: 4 },
  };
}

globalThis.fetch = async (path) => {
  const url = String(path);
  if (url.startsWith("/api/layout")) {
    return jsonResponse(200, SEEDED_LAYOUT);
  }
  if (url.startsWith("/api/widgets/")) {
    return jsonResponse(404, { error: { code: "NOT_FOUND", message: "nur statisch" } });
  }
  const match = url.match(/^\/widgets\/([a-z0-9-]+)\/manifest\.json$/);
  if (match) {
    return jsonResponse(200, staticManifest(match[1]));
  }
  // Kein /api/config -> `main()` beim Import bricht ab.
  return jsonResponse(500, { error: { code: "TEST", message: `unerwartet: ${url}` } });
};

const { BUILTIN_WIDGETS, DashboardApp, pendingBuiltins } = await import("../js/app.js");

test("BUILTIN_WIDGETS führt die Phase-2-Widgets auf dem dashboard-Tab mit Takt", () => {
  for (const id of PHASE2_IDS) {
    const def = BUILTIN_WIDGETS.find((entry) => entry.id === id);
    assert.ok(def, `${id} fehlt in BUILTIN_WIDGETS`);
    assert.equal(def.tab, "dashboard");
    // api_cache-gepuffert (TTL 600 s) -> 10 min wie das Wetter-Widget.
    assert.equal(def.pollMs, 10 * 60 * 1000);
  }
});

test("resolveInstances: nicht-leeres Layout -> drei Kacheln ohne manuelles Platzieren", async () => {
  const app = new DashboardApp();
  const instances = await app.resolveInstances("dashboard", null);
  const ids = instances.map((instance) => instance.widget_id);

  // Die vorhandene Layout-Zeile bleibt genau einmal erhalten.
  assert.equal(ids.filter((id) => id === "weather").length, 1);
  assert.equal(instances.find((i) => i.widget_id === "weather").id, "layout-weather");

  for (const id of PHASE2_IDS) {
    assert.ok(ids.includes(id), `${id} fehlt im dashboard-Tab`);
    // Fallback-Instanzen sind als `builtin:` markiert (POST beim Verschieben).
    assert.ok(
      instances.some((instance) => instance.id === `builtin:${id}` && instance.widget_id === id),
      `${id} nicht als builtin:-Instanz ergänzt`,
    );
    assert.equal(instances.find((instance) => instance.widget_id === id).pollMs, 10 * 60 * 1000);
  }
});

test("Fallback: bereits geseedetes Layout zeigt die drei Kacheln ohne Platzieren", () => {
  const present = new Set(SEEDED_LAYOUT_IDS);
  const pending = pendingBuiltins(BUILTIN_WIDGETS, "dashboard", present, null);
  assert.deepEqual(
    pending.map((def) => def.id),
    PHASE2_IDS,
  );
});

test("Kein Doppel: vorhandene Phase-2-Zeilen erzeugen keinen Fallback", () => {
  const present = new Set([...SEEDED_LAYOUT_IDS, ...PHASE2_IDS]);
  assert.deepEqual(pendingBuiltins(BUILTIN_WIDGETS, "dashboard", present, null), []);
});

test("Fallback respektiert deaktivierte Widgets (Registry is_enabled=false)", () => {
  const registry = PHASE2_IDS.map((id) => ({ id, is_enabled: false }));
  const pending = pendingBuiltins(BUILTIN_WIDGETS, "dashboard", new Set(SEEDED_LAYOUT_IDS), registry);
  assert.deepEqual(pending, []);
});

test("Fallback gilt nur für den angefragten Tab", () => {
  const pending = pendingBuiltins(BUILTIN_WIDGETS, "todos", new Set(["weather"]), null);
  assert.deepEqual(
    pending.map((def) => def.id),
    ["hermes-todos"],
  );
});
