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
// wird unterdrückt (steuert den Test nicht). Der `createElement`-Stub ist reich
// genug für die Runtime-Tests (WP-F1-4): `classList`, `dataset`, `append`,
// `replaceChildren` und `setAttribute`.
function makeEl(tag = "div") {
  const classes = new Set();
  const el = {
    tagName: tag,
    children: [],
    dataset: {},
    className: "",
    classList: {
      add(...names) {
        names.forEach((name) => classes.add(name));
      },
      remove(...names) {
        names.forEach((name) => classes.delete(name));
      },
      toggle(name, force) {
        const on = force === undefined ? !classes.has(name) : Boolean(force);
        if (on) {
          classes.add(name);
        } else {
          classes.delete(name);
        }
      },
      contains(name) {
        return classes.has(name);
      },
    },
    setAttribute() {},
    append(...nodes) {
      el.children.push(...nodes);
    },
    replaceChildren(...nodes) {
      el.children = [...nodes];
    },
    remove() {},
  };
  return el;
}

globalThis.document = { createElement: (tag) => makeEl(tag), getElementById: () => null };
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
const { WidgetRuntime, validateManifest } = await import("../js/widget-runtime.js");

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

// --- WP-F1-4: optionales `renderExpanded(container)` (spec-focus E) ---------

const RUNTIME_MANIFEST = {
  id: "fake",
  name: "Fake",
  version: "1.0.0",
  category: "general",
  permissions: [],
  default_size: { w: 2, h: 2 },
};

const silentLog = {
  info() {},
  warn() {},
  error() {},
};

/** Widget-Basis ohne Vollbild-Hook (der abwärtskompatible Normalfall). */
class BaseWidget {
  constructor(container, config, api) {
    this.container = container;
    this.config = config;
    this.api = api;
    this.renderCalls = 0;
  }

  render() {
    this.renderCalls += 1;
  }

  refresh() {}
}

/** Widget mit optionalem Hook — zählt die Aufrufe und merkt sich den Container. */
class ExpandedWidget extends BaseWidget {
  renderExpanded(container) {
    this.expandedCalls = (this.expandedCalls || 0) + 1;
    this.expandedContainer = container;
  }
}

/** Widget, dessen Hook wirft — prüft den Fehlervertrag (E.3). */
class ThrowingWidget extends BaseWidget {
  renderExpanded() {
    throw new Error("renderExpanded kaputt");
  }
}

async function loadRuntimeWidget(runtime, WidgetClass) {
  const container = makeEl("div");
  const entry = await runtime.loadWidget({
    instanceId: "inst-1",
    widgetId: "fake",
    container,
    manifest: { ...RUNTIME_MANIFEST },
    config: {},
    pollMs: 0,
    widgetClass: WidgetClass,
  });
  return { container, entry };
}

test("Manifest braucht kein renderExpanded-Pflichtfeld", () => {
  assert.doesNotThrow(() => validateManifest({ ...RUNTIME_MANIFEST }, "fake"));
});

test("Kriterium 1: renderExpanded nur im Modus fullscreen und nur als Funktion", async () => {
  const runtime = new WidgetRuntime({ log: silentLog });
  const { container, entry } = await loadRuntimeWidget(runtime, ExpandedWidget);

  // highlight/enlarge sind rein Host-seitig -> kein Hook-Aufruf.
  assert.equal(await runtime.applyFocus(entry.instanceId, "highlight"), false);
  assert.equal(await runtime.applyFocus(entry.instanceId, "enlarge", { container }), false);
  assert.equal(entry.widget.expandedCalls || 0, 0);
  assert.equal(entry.state, "ready");

  // fullscreen -> genau ein Hook-Aufruf mit dem Ziel-Container.
  assert.equal(await runtime.applyFocus(entry.instanceId, "fullscreen", { container }), true);
  assert.equal(entry.widget.expandedCalls, 1);
  assert.equal(entry.widget.expandedContainer, container);
});

test("Kriterium 1: ohne Methode bleibt die generische Vollbild-Darstellung", async () => {
  const runtime = new WidgetRuntime({ log: silentLog });
  const { container, entry } = await loadRuntimeWidget(runtime, BaseWidget);

  assert.equal(await runtime.applyFocus(entry.instanceId, "fullscreen", { container }), false);
  assert.equal(entry.expanded, false);
  assert.equal(entry.state, "ready");
});

test("Kriterium 2: wirft renderExpanded -> Fehler-Platzhalter im Overlay, kein Absturz", async () => {
  const runtime = new WidgetRuntime({ log: silentLog });
  const overlay = makeEl("div");
  const { entry } = await loadRuntimeWidget(runtime, ThrowingWidget);

  await assert.doesNotReject(async () => {
    await runtime.applyFocus(entry.instanceId, "fullscreen", { container: overlay });
  });

  assert.equal(entry.state, "error");
  assert.equal(entry.errorCode, "WIDGET_ERROR");
  // Vollbild gilt als übernommen (mit Platzhalter), damit das Verlassen greift.
  assert.equal(entry.expanded, true);

  const placeholder = overlay.children[0];
  assert.ok(placeholder, "Platzhalter fehlt im Overlay");
  assert.equal(placeholder.className, "wg-error");
  assert.equal(placeholder.dataset.errorCode, "WIDGET_ERROR");
});

test("Kriterium 3: Verlassen des Vollbilds ruft render() erneut auf (idempotent)", async () => {
  const runtime = new WidgetRuntime({ log: silentLog });
  const { container, entry } = await loadRuntimeWidget(runtime, ExpandedWidget);
  const renderCallsAfterMount = entry.widget.renderCalls;

  assert.equal(await runtime.applyFocus(entry.instanceId, "fullscreen", { container }), true);
  assert.equal(await runtime.applyFocus(entry.instanceId, "highlight"), true);

  assert.equal(entry.widget.renderCalls, renderCallsAfterMount + 1);
  assert.equal(entry.expanded, false);

  // Erneutes Verlassen ist idempotent: kein weiterer render()-Aufruf, kein Fehler.
  assert.equal(await runtime.applyFocus(entry.instanceId, "highlight"), false);
  assert.equal(entry.widget.renderCalls, renderCallsAfterMount + 1);
  assert.equal(entry.state, "ready");
});
