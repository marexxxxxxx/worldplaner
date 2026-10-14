/**
 * Unit-Tests des Garmin-Workout-Widgets (MARAAA-25, Befund B1 aus MARAAA-24).
 *
 * Deckt den Fehlervertrag (E.3) ab, ohne Browser und ohne Abhängigkeiten:
 *   - leerer Cache `{"workouts":[],"stale":true}` -> `data-mode="empty"`, Wert "—"
 *   - nicht-leere Liste -> bisheriger Modus, 7-Tage-Zähler korrekt
 *   - Netzfehler / ungültige Antwort -> bisheriger Fehler-/`stale`-Pfad, kein Crash
 *
 * Lauf: `npm test` im Ordner `frontend/` (`node --test tests/`).
 */
import test from "node:test";
import assert from "node:assert/strict";

import GarminWorkoutWidget, { readWorkouts, weeklyCount } from "../../widgets/garmin-workout/frontend/widget.js";

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
