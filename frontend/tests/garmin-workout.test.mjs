/**
 * Unit-Tests des Garmin-Workout-Widgets (MARAAA-25, Befund B1 aus MARAAA-24).
 *
 * Deckt den Fehlervertrag (E.3) ab, ohne Browser und ohne Abhängigkeiten:
 *   - leerer Cache `{"workouts":[],"stale":true}` -> `data-mode="empty"`, Wert "—"
 *   - nicht-leere Liste -> bisheriger Modus, 7-Tage-Zähler korrekt
 *   - Netzfehler / ungültige Antwort -> bisheriger Fehler-/`stale`-Pfad, kein Crash
 *
 * WP-P2-7: Das Backend liefert höchstens `limit` Einträge (D.5). Der Zähler
 * ruft deshalb mit dem Backend-Maximum ab, sodass das Wochenziel auch bei mehr
 * als `limit` Aktivitäten stimmt (`makeBackend` simuliert genau dieses Kappen).
 *
 * Lauf: `npm test` im Ordner `frontend/` (`node --test tests/`).
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import GarminWorkoutWidget, {
  readWorkouts,
  weeklyCount,
  weeklySummary,
} from "../../widgets/garmin-workout/frontend/widget.js";

/** Minimales DOM — nur die Methoden, die `render()`/`renderValue()` anfassen. */
class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.className = "";
    this.dataset = {};
    this.attributes = {};
    this.textContent = "";
    this.hidden = false;
    this.style = {};
    this.children = [];
    this.listeners = {};
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
    this.children = [...nodes];
  }

  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }
}

globalThis.document = { createElement: (tagName) => new FakeElement(tagName) };

/** Aktivität „vor `minutesAgo` Minuten" — liegt sicher in den letzten 7 Tagen. */
function workout(minutesAgo, overrides = {}) {
  return {
    activity_type: "running",
    activity_name: "Lauf",
    start_time: new Date(Date.now() - minutesAgo * 60 * 1000).toISOString(),
    duration_s: 2400,
    ...overrides,
  };
}

function makeApi(result) {
  return {
    calls: [],
    async fetchJson(url) {
      this.calls.push(url);
      if (result instanceof Error) {
        throw result;
      }
      return result;
    },
  };
}

/** Widget im DOM bauen, einen Abruf fahren, Root zurückgeben. */
async function boot(config, result) {
  const container = new FakeElement("div");
  const api = makeApi(result);
  const widget = new GarminWorkoutWidget(container, config, api);
  await widget.render();
  await widget.refresh();
  return { widget, container, api };
}

test("leerer Cache (stale:true) rendert data-mode=empty und Wochenwert „—\"", async () => {
  const { widget } = await boot({}, { workouts: [], stale: true });

  assert.equal(widget.last, null);
  assert.equal(widget.root.dataset.mode, "empty");
  assert.equal(widget.valueEl.textContent, "—");
  assert.notEqual(widget.valueEl.textContent, "0");
  assert.match(widget.subEl.textContent, /Keine Workouts/);
  assert.equal(widget.listEl.children.length, 1);
  assert.equal(widget.listEl.children[0].className, "gw-empty");
  assert.match(widget.listEl.children[0].textContent, /Keine Workouts im Cache/);
});

test("leerer Cache ohne stale (stale:false) rendert ebenfalls data-mode=empty", async () => {
  const { widget } = await boot({}, { workouts: [], stale: false });

  assert.equal(widget.last, null);
  assert.equal(widget.root.dataset.mode, "empty");
  assert.equal(widget.valueEl.textContent, "—");
});

test("nicht-leere Liste bleibt data-mode=data und zählt die letzten 7 Tage", async () => {
  const { widget } = await boot(
    { weekly_target: 2 },
    { workouts: [workout(30), workout(60 * 24), workout(60 * 24 * 9)], stale: false },
  );

  assert.equal(widget.root.dataset.mode, "data");
  // Zwei Einträge innerhalb von 7 Tagen, der dritte (9 Tage alt) zählt nicht.
  assert.equal(widget.valueEl.textContent, "2");
  assert.equal(weeklyCount(widget.last.items), 2);
  assert.equal(widget.root.dataset.band, "within");
});

test("nicht-leere Liste mit stale:true bleibt data-mode=stale", async () => {
  const { widget } = await boot({}, { workouts: [workout(15)], stale: true });

  assert.equal(widget.root.dataset.mode, "stale");
  assert.equal(widget.valueEl.textContent, "1");
});

test("Netzfehler wirft nicht und zeigt den leeren Zustand", async () => {
  const { widget } = await boot({}, Object.assign(new Error("boom"), { code: "NETWORK" }));

  assert.equal(widget.degraded, true);
  assert.equal(widget.root.dataset.mode, "empty");
  assert.equal(widget.valueEl.textContent, "—");
  assert.equal(widget.subEl.textContent, "Daten nicht erreichbar");
});

test("ungültige Antwort behält den bisherigen Fehler-/stale-Pfad", async () => {
  const { widget } = await boot({}, { error_code: "UPSTREAM_ERROR" });

  assert.equal(widget.degraded, true);
  assert.equal(widget.root.dataset.mode, "stale");
  assert.deepEqual(widget.last, { items: [], stale: true });
});

test("readWorkouts: leerer Cache ist kein Fehler", () => {
  assert.deepEqual(readWorkouts({ workouts: [], stale: true }), {
    items: [],
    stale: true,
    recovered: true,
  });
  assert.deepEqual(readWorkouts({ error_code: "X" }), {
    items: [],
    stale: true,
    recovered: false,
  });
});

/** Aktivitäten „in den letzten 7 Tagen", `count` Stück, neueste zuerst. */
function recentWorkouts(count) {
  return Array.from({ length: count }, (_, index) => workout(5 + index));
}

/**
 * Backend-Simulation (D.5) wie `garmin_cache.read_workouts`: ohne `date`
 * höchstens `limit` der neuesten Aktivitäten — der Kern des WP-P2-7-Befunds.
 */
function makeBackend(total) {
  return {
    calls: [],
    async fetchJson(url) {
      this.calls.push(url);
      const limit = Number(new URL(url, "http://localhost").searchParams.get("limit"));
      return { workouts: recentWorkouts(Math.min(total, limit)), stale: false };
    },
  };
}

async function bootWithApi(config, api) {
  const container = new FakeElement("div");
  const widget = new GarminWorkoutWidget(container, config, api);
  await widget.render();
  await widget.refresh();
  return { widget, container, api };
}

/** Löst die am Element registrierten `click`-Listener aus (Fake-DOM). */
function click(element) {
  for (const handler of element.listeners.click || []) {
    handler({});
  }
}

test("WP-P2-7: > limit Workouts in 7 Tagen zeigt die tatsaechliche Wochenzahl", async () => {
  // 15 Aktivitäten in 7 Tagen, Anzeige-limit 10 -> Zähler muss 15 zeigen, nicht 10.
  const { widget, api } = await bootWithApi({ weekly_target: 10, limit: 10 }, makeBackend(15));

  assert.equal(widget.valueEl.textContent, "15");
  assert.equal(weeklyCount(widget.last.items), 15);
  assert.equal(widget.root.dataset.band, "above");
  assert.match(widget.targetTextEl.textContent, /\(15\/10\)/);
  // Der Zählabruf holt das Backend-Maximum, nicht das Anzeige-limit.
  assert.match(api.calls[0] ?? "", /limit=50/);
});

test("WP-P2-7: > limit Workouts — Liste bleibt auf config.limit begrenzt", async () => {
  const { widget } = await bootWithApi({ weekly_target: 3, limit: 10 }, makeBackend(15));

  assert.equal(widget.valueEl.textContent, "15");
  assert.equal(widget.listEl.children.length, 10);
  assert.match(widget.contextEl.textContent, /Letzte 10/);
});

test("WP-P2-7: <= limit Workouts verhalten sich unveraendert", async () => {
  const { widget } = await bootWithApi({ weekly_target: 3, limit: 10 }, makeBackend(4));

  assert.equal(widget.valueEl.textContent, "4");
  assert.equal(widget.listEl.children.length, 4);
  assert.match(widget.contextEl.textContent, /Letzte 4/);
});

test("WP-P2-7: ausgeschoepftes Abruf-Limit kennzeichnet die Zahl als Untergrenze", async () => {
  // 60 Aktivitäten in 7 Tagen, Backend-Maximum 50 -> „≥50" statt einer zu kleinen Zahl.
  const { widget } = await bootWithApi({ weekly_target: 10, limit: 10 }, makeBackend(60));

  assert.equal(widget.valueEl.textContent, "≥50");
  assert.match(widget.targetTextEl.textContent, /\(≥50\/10\)/);
  assert.equal(widget.listEl.children.length, 10);
});

function normalized(count) {
  return readWorkouts({ workouts: recentWorkouts(count) }).items;
}

test("WP-P2-7: weeklySummary — exakt bis zur Abrufgrenze, darueber Untergrenze", () => {
  assert.deepEqual(weeklySummary(normalized(12)), { count: 12, capped: false });
  assert.deepEqual(weeklySummary(normalized(50)), { count: 50, capped: true });

  // 50 gelieferte Einträge, aber einer älter als 7 Tage -> Wochenzahl bleibt exakt.
  const mixed = [
    ...normalized(49),
    ...readWorkouts({ workouts: [workout(60 * 24 * 9)] }).items,
  ];
  assert.deepEqual(weeklySummary(mixed), { count: 49, capped: false });
});

// --- WP-F1-6: Trainingsplan ausklappen --------------------------------------

const GW_CSS = readFileSync(
  new URL("../../widgets/garmin-workout/frontend/style.css", import.meta.url),
  "utf8",
);

test("WP-F1-6 AC1/AC2: 'Mehr anzeigen' klappt auf alle geladenen Eintraege aus", async () => {
  const { widget, api } = await bootWithApi({ weekly_target: 3, limit: 10 }, makeBackend(15));

  // Normalzustand: Liste auf config.limit (10) gekuerzt, Schaltflaeche da.
  assert.equal(widget.listEl.children.length, 10);
  assert.equal(widget.toggleEl.hidden, false);
  assert.equal(widget.toggleEl.textContent, "Mehr anzeigen");
  assert.equal(widget.toggleEl.getAttribute("aria-expanded"), "false");

  // Aufgeklappt: alle geladenen Eintraege (15), Beschriftung/Zustand wechseln.
  click(widget.toggleEl);
  assert.equal(widget.expanded, true);
  assert.equal(widget.listEl.children.length, 15);
  assert.equal(widget.toggleEl.textContent, "Weniger anzeigen");
  assert.equal(widget.toggleEl.getAttribute("aria-expanded"), "true");

  // Zugeklappt: zurueck auf config.limit.
  click(widget.toggleEl);
  assert.equal(widget.expanded, false);
  assert.equal(widget.listEl.children.length, 10);
  assert.equal(widget.toggleEl.textContent, "Mehr anzeigen");
  assert.equal(widget.toggleEl.getAttribute("aria-expanded"), "false");

  // AC3: kein zusaetzlicher Endpunkt — nur der eine refresh()-Abruf.
  assert.equal(api.calls.length, 1);
  assert.match(api.calls[0], /limit=50/);
});

test("WP-F1-6 AC2: ausgeklappt bis COUNT_LIMIT (50), nicht darueber", async () => {
  const { widget } = await bootWithApi({ weekly_target: 10, limit: 10 }, makeBackend(60));

  assert.equal(widget.listEl.children.length, 10);
  assert.match(widget.contextEl.textContent, /Letzte 10/);

  click(widget.toggleEl);
  assert.equal(widget.listEl.children.length, 50);
  assert.match(widget.contextEl.textContent, /Letzte 50/);
});

test("WP-F1-6: Schaltflaeche steht unter der Liste und bei kurzer Liste harmlos", async () => {
  const { widget } = await bootWithApi({ limit: 10 }, makeBackend(4));

  const order = widget.root.children.map((child) => child.className);
  assert.ok(order.indexOf("gw-list") < order.indexOf("gw-toggle"), "Toggle liegt unter der Liste");

  assert.equal(widget.listEl.children.length, 4);
  assert.equal(widget.toggleEl.hidden, false);
  click(widget.toggleEl);
  // Nichts verborgen -> dieselbe Anzahl, aber Zustand wechselt sichtbar.
  assert.equal(widget.listEl.children.length, 4);
  assert.equal(widget.toggleEl.textContent, "Weniger anzeigen");
  assert.equal(widget.toggleEl.getAttribute("aria-expanded"), "true");
});

test("WP-F1-6: ohne geladene Eintraege keine Ausklapp-Schaltflaeche", async () => {
  const empty = await boot({}, { workouts: [], stale: true });
  assert.equal(empty.widget.toggleEl.hidden, true);

  const failed = await boot({}, Object.assign(new Error("boom"), { code: "NETWORK" }));
  assert.equal(failed.widget.toggleEl.hidden, true);
});

test("WP-F1-6 AC1: .gw-toggle ist ein Tap-Ziel >= 44 px", () => {
  const rule = GW_CSS.match(/\.gw-toggle\s*\{([^}]*)\}/);
  assert.ok(rule, "Regel .gw-toggle fehlt in style.css");
  const height = rule[1].match(/min-height:\s*([^;]+);/);
  assert.ok(height, "min-height fehlt in .gw-toggle");
  assert.match(height[1], /44px/);
});

test("WP-F1-6: refresh() behaelt den Ausklapp-Zustand und aktualisiert die Liste", async () => {
  let total = 15;
  const api = {
    calls: [],
    async fetchJson(url) {
      this.calls.push(url);
      const limit = Number(new URL(url, "http://localhost").searchParams.get("limit"));
      return { workouts: recentWorkouts(Math.min(total, limit)), stale: false };
    },
  };
  const { widget } = await bootWithApi({ limit: 10 }, api);
  click(widget.toggleEl);
  assert.equal(widget.listEl.children.length, 15);

  total = 20;
  await widget.refresh();
  assert.equal(widget.expanded, true);
  assert.equal(widget.listEl.children.length, 20);
  assert.equal(widget.toggleEl.textContent, "Weniger anzeigen");
});
