/**
 * Unit-Tests des Baikal-Kalender-Widgets (spec-phase2 D/WP-P2-1).
 *
 * Deckt das Rendern (Titel + Startzeit) und den Fehlervertrag (E.3) ohne Browser
 * und ohne Abhängigkeiten (Muster: `tests/mealie-mealplan.test.mjs`):
 *   - normale Antwort -> Wert = Anzahl, Liste je Termin mit Titel + Startzeit
 *   - Ganztagstermin -> "ganztägig" in der Meta-Zeile
 *   - leere `events`-Liste -> leerer Zustand (kein Fehler), Wert "—"
 *   - `BAIKAL_UNAVAILABLE` -> Fehler-/leerer Zustand, kein Crash
 *   - Netzfehler -> letzter bekannter Wert bleibt, Modus "degraded"
 *   - reine Helfer (Limit, Startzeit-Format, Normalisierung)
 *
 * Lauf: `npm test` im Ordner `frontend/` (`node --test tests/`).
 */
import test from "node:test";
import assert from "node:assert/strict";

import BaikalCalendarWidget, {
  formatStart,
  listLimit,
  readEvents,
} from "../../widgets/baikal-calendar/frontend/widget.js";

/** Minimales DOM — nur die Methoden, die `render()`/`renderValue()` anfassen. */
class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.className = "";
    this.dataset = {};
    this.attributes = {};
    this.textContent = "";
    this.hidden = false;
    this.children = [];
    this.listeners = {};
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
    this.children = [...nodes];
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
  events: [
    {
      uid: "event1@baikal",
      summary: "Zahnarzt",
      start: "2026-10-20T12:00:00Z",
      end: "2026-10-20T13:00:00Z",
      all_day: false,
      location: "Praxis",
    },
    {
      uid: "event2@baikal",
      summary: "Urlaub",
      start: "2026-10-21T00:00:00Z",
      end: "2026-10-22T00:00:00Z",
      all_day: true,
      location: "",
    },
  ],
  fetched_at: NOW,
};

/** Baut ein Widget und raeumt es nach dem Test auf (loescht den Takt-Timer). */
function makeWidget(t, { config = {}, fetchJson = async () => PAYLOAD } = {}) {
  const container = new FakeElement("div");
  const calls = [];
  const api = {
    fetchJson: async (path) => {
      calls.push(path);
      return fetchJson(path);
    },
  };
  const widget = new BaikalCalendarWidget(container, config, api);
  t.after(() => widget.destroy());
  return { container, widget, calls };
}

test("rendert Titel und Startzeit aus GET /api/baikal/events", async (t) => {
  const { widget, calls } = makeWidget(t);
  await widget.render();
  await widget.refresh();

  assert.deepEqual(calls, ["/api/baikal/events"]);
  assert.equal(widget.root.dataset.mode, "data");
  assert.equal(widget.valueEl.textContent, "2");

  const text = collectText(widget.listEl).join(" | ");
  assert.match(text, /Zahnarzt/);
  assert.match(text, /20\.10\./); // Startzeit (Datum) sichtbar
  assert.match(text, /Praxis/); // Ort als Kontext
  assert.match(text, /Urlaub/);
  assert.match(text, /ganztägig/); // Ganztagstermin kenntlich gemacht
  assert.match(widget.targetEl.textContent, /Nächster Termin: .*Zahnarzt/);
});

test("leere events-Liste ist kein Fehler (leerer Zustand)", async (t) => {
  const { widget } = makeWidget(t, {
    fetchJson: async () => ({ events: [], fetched_at: NOW }),
  });
  await widget.render();
  await widget.refresh(); // darf nicht werfen
  assert.equal(widget.root.dataset.mode, "empty");
  assert.equal(widget.valueEl.textContent, "—");
  assert.match(widget.subEl.textContent, /Keine Termine/);
});

test("BAIKAL_UNAVAILABLE -> Fehlerzustand ohne Crash", async (t) => {
  const { widget } = makeWidget(t, {
    fetchJson: async () => ({ events: [], error_code: "BAIKAL_UNAVAILABLE" }),
  });
  await widget.render();
  await widget.refresh(); // darf nicht werfen
  assert.equal(widget.root.dataset.mode, "empty");
  assert.equal(widget.valueEl.textContent, "—");
  assert.match(widget.contextEl.textContent, /nicht erreichbar/);
});

test("Netzfehler behält den letzten bekannten Wert (degraded)", async (t) => {
  let fail = false;
  const { widget } = makeWidget(t, {
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

  assert.equal(widget.root.dataset.mode, "degraded");
  assert.equal(widget.valueEl.textContent, "2"); // letzter Wert bleibt sichtbar
  assert.match(widget.contextEl.textContent, /Quelle nicht erreichbar/);
});

test("reine Helfer: Limit, Startzeit-Format, Normalisierung", () => {
  assert.equal(listLimit({}), 6);
  assert.equal(listLimit({ limit: 99 }), 20);
  assert.equal(listLimit({ limit: 0 }), 1);

  assert.equal(formatStart({ start: "2026-10-20T12:00:00Z", allDay: true }), "Di., 20.10. · ganztägig");
  assert.match(formatStart({ start: "2026-10-20T12:00:00Z", allDay: false }), /20\.10\..*12:00|12:00.*20\.10\./);
  assert.equal(formatStart({ start: "", allDay: false }), null);
  assert.equal(formatStart({ start: "kein-datum", allDay: false }), null);

  const read = readEvents(PAYLOAD);
  assert.equal(read.recovered, true);
  assert.equal(read.items.length, 2);
  assert.equal(readEvents({ error_code: "BAIKAL_UNAVAILABLE" }).recovered, false);
  assert.equal(readEvents({ events: [] }).recovered, true); // leer ist kein Fehler
});
