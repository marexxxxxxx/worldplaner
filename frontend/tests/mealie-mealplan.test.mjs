/**
 * Unit-Tests des Mealie-Wochenplan-Widgets (spec-phase2 D/WP-P2-3).
 *
 * Deckt den Fehlervertrag (E.3) und das Rendern ab, ohne Browser und ohne
 * Abhängigkeiten (Muster: `tests/garmin-workout.test.mjs`):
 *   - normale Antwort -> Wert = Anzahl, Liste je Tag mit Mahlzeitentyp/Rezept/Portionen
 *   - leere `meals`-Liste -> leerer Zustand (kein Fehler), Wert "—"
 *   - `MEALIE_UNAVAILABLE` -> Fehler-/leerer Zustand, kein Crash
 *   - Netzfehler -> letzter bekannter Wert bleibt, Modus "stale"
 *   - Ziel-Bewertung (unter / erreicht) und reine Helfer
 *
 * Lauf: `npm test` im Ordner `frontend/` (`node --test tests/`).
 */
import test from "node:test";
import assert from "node:assert/strict";

import MealieMealplanWidget, {
  classifyCount,
  daysWindow,
  groupByDate,
  mealTarget,
  readMealplan,
} from "../../widgets/mealie-mealplan/frontend/widget.js";

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

function collectText(node, out = []) {
  if (!node) {
    return out;
  }
  if (typeof node.textContent === "string" && node.textContent) {
    out.push(node.textContent);
  }
  for (const child of node.children || []) {
    collectText(child, out);
  }
  return out;
}

const NOW = new Date().toISOString();

const PAYLOAD = {
  meals: [
    { date: "2026-10-14", meal_type: "dinner", recipe_id: "r1", recipe_name: "Bolognese", servings: 2 },
    { date: "2026-10-14", meal_type: "breakfast", recipe_id: "", recipe_name: "Overnight Oats", servings: 1 },
    { date: "2026-10-15", meal_type: "lunch", recipe_id: "r2", recipe_name: "Kürbissuppe", servings: 4 },
  ],
  fetched_at: NOW,
};

function makeWidget({ config = {}, fetchJson = async () => PAYLOAD } = {}) {
  const container = new FakeElement("div");
  const calls = [];
  const api = {
    fetchJson: async (path) => {
      calls.push(path);
      return fetchJson(path);
    },
  };
  const widget = new MealieMealplanWidget(container, config, api);
  return { container, widget, calls };
}

test("rendert die Mahlzeiten aus GET /api/mealie/mealplan", async () => {
  const { widget, calls } = makeWidget();
  await widget.render();
  await widget.refresh();

  assert.deepEqual(calls, ["/api/mealie/mealplan?days=7"]);
  assert.equal(widget.root.dataset.mode, "data");
  assert.equal(widget.valueEl.textContent, "3");
  assert.match(widget.subEl.textContent, /geplante Mahlzeiten in den nächsten 7 Tagen/);

  const text = collectText(widget.listEl).join(" | ");
  assert.match(text, /Bolognese/);
  assert.match(text, /Overnight Oats/);
  assert.match(text, /Kürbissuppe/);
  assert.match(text, /2 Portionen/);

  // Gruppierung und Sortierung: erst Frühstück, dann Abendessen am selben Tag.
  const firstDay = widget.listEl.children[0];
  const firstDayText = collectText(firstDay.children[1]).join(" | ");
  assert.match(firstDayText, /Overnight Oats[\s\S]*Bolognese/);
  // Zweiter Tag ist der Folgetag.
  assert.equal(widget.listEl.children[1].children[0].children[0].dateTime, "2026-10-15");
});

test("Ziel-Bewertung: unter Ziel und erreicht, als Glyphe UND Text", async () => {
  const below = makeWidget({ config: { min_meals: 7 } });
  await below.widget.render();
  await below.widget.refresh();
  assert.equal(below.widget.root.dataset.band, "below");
  assert.match(below.widget.targetTextEl.textContent, /unter Ziel \(3\/7\)/);
  assert.equal(below.widget.targetGlyphEl.textContent, "▼");

  const within = makeWidget({ config: { min_meals: 3 } });
  await within.widget.render();
  await within.widget.refresh();
  assert.equal(within.widget.root.dataset.band, "within");
  assert.match(within.widget.targetTextEl.textContent, /Ziel erreicht \(3\/3\)/);
  assert.equal(within.widget.targetGlyphEl.textContent, "●");
});

test("leere meals-Liste ist kein Fehler (leerer Zustand)", async () => {
  const { widget } = makeWidget({ fetchJson: async () => ({ meals: [], fetched_at: NOW }) });
  await widget.render();
  await widget.refresh(); // darf nicht werfen
  assert.equal(widget.root.dataset.mode, "empty");
  assert.equal(widget.valueEl.textContent, "—");
  assert.match(widget.subEl.textContent, /Keine Mahlzeiten in den nächsten 7 Tagen/);
});

test("MEALIE_UNAVAILABLE -> Fehlerzustand ohne Crash", async () => {
  const { widget } = makeWidget({
    fetchJson: async () => ({ meals: [], error_code: "MEALIE_UNAVAILABLE" }),
  });
  await widget.render();
  await widget.refresh(); // darf nicht werfen
  assert.equal(widget.root.dataset.mode, "empty");
  assert.equal(widget.valueEl.textContent, "—");
  assert.match(widget.contextEl.textContent, /nicht erreichbar/);
});

test("Netzfehler behält den letzten bekannten Wert (stale)", async () => {
  let fail = false;
  const { widget } = makeWidget({
    fetchJson: async () => {
      if (fail) {
        throw Object.assign(new Error("offline"), { code: "NETWORK" });
      }
      return PAYLOAD;
    },
  });
  await widget.render();
  await widget.refresh();
  fail = true;
  await widget.refresh(); // darf nicht werfen

  assert.equal(widget.root.dataset.mode, "stale");
  assert.equal(widget.valueEl.textContent, "3"); // letzter Wert bleibt sichtbar
  assert.match(widget.contextEl.textContent, /Quelle nicht erreichbar/);
});

test("reine Helfer: Fenster, Ziel, Bewertung, Gruppierung", () => {
  assert.equal(daysWindow({}), 7);
  assert.equal(daysWindow({ days: 99 }), 7);
  assert.equal(daysWindow({ days: 0 }), 1);
  assert.equal(mealTarget({}), 7);
  assert.equal(mealTarget({ min_meals: 0 }), 7);
  assert.equal(classifyCount(3, 7), "below");
  assert.equal(classifyCount(7, 7), "within");
  assert.equal(classifyCount(9, 7), "within");

  const read = readMealplan(PAYLOAD);
  assert.equal(read.recovered, true);
  assert.equal(read.meals.length, 3);
  assert.equal(readMealplan({ error_code: "MEALIE_UNAVAILABLE" }).recovered, false);

  const groups = groupByDate(read.meals);
  assert.equal(groups.length, 2);
  assert.deepEqual(
    groups[0].items.map((meal) => meal.mealType),
    ["breakfast", "dinner"],
  );
});
