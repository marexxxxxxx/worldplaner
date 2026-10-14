/**
 * Unit-Tests des Grocy-Bestands-Widgets (spec-phase2 D/WP-P2-2, MARAAA-42).
 *
 * Deckt das Rendern und den Fehlervertrag (E.3) ab, ohne Browser und ohne
 * Abhängigkeiten (Muster: `tests/mealie-mealplan.test.mjs`):
 *   - normale Antwort -> Wert = Anzahl, Liste je Produkt mit Menge/Einheit,
 *     MHD und Status-Text
 *   - Sortierung nach frühestem MHD, Produkte ohne MHD zuletzt
 *   - Ziel-Zeile: abgelaufen / bald fällig, als Glyphe UND Text (I.2)
 *   - leere `products`-Liste -> leerer Zustand (kein Fehler), Wert "—"
 *   - `GROCY_UNAVAILABLE` ohne Vorwert -> leerer Zustand ("—"), NICHT "0"
 *   - `GROCY_UNAVAILABLE` nach erfolgreichem Abruf -> zuletzt bekannter Bestand
 *     bleibt sichtbar, als „Quelle nicht erreichbar" gekennzeichnet
 *   - Netzfehler ohne Vorwert -> leerer Zustand, kein Crash
 *   - reine Helfer (Fenster, Limit, Klassifikation, Format, Normalisierung)
 *
 * FEHLERPFAD (MARAAA-45): Bei `GROCY_UNAVAILABLE` behält `refresh()` den zuletzt
 * bekannten Bestand (`this.last`) und markiert ihn über `degraded` als
 * „Quelle nicht erreichbar" (Modus "degraded", Wert gedämpft). Ohne Vorwert
 * bleibt `last === null` -> leerer Zustand ("—", „Kein Bestand"). Ein erfundenes
 * "0"/„in Ordnung" darf nie erscheinen. Vorbild: `mealie-mealplan`/`weather`.
 *
 * Lauf: `npm test` im Ordner `frontend/` (`node --test tests/`).
 */
import test from "node:test";
import assert from "node:assert/strict";

import GrocyStockWidget, {
  DEFAULT_EXPIRING_DAYS,
  DEFAULT_LIMIT,
  classifyProduct,
  expiringDays,
  formatAmount,
  formatDate,
  listLimit,
  normalizeProduct,
  readStock,
  relativeAge,
  sortProducts,
} from "../../widgets/grocy-stock/frontend/widget.js";

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

const DAY_MS = 24 * 60 * 60 * 1000;
/** ISO-Datum (UTC) relativ zu heute — macht die MHD-Klassifikation stabil. */
const isoIn = (offsetDays) =>
  new Date(Date.now() + offsetDays * DAY_MS).toISOString().slice(0, 10);

const NOW = new Date().toISOString();

const PAYLOAD = {
  products: [
    { product_id: 1, name: "Milch", amount: 1, unit: "l", best_before_date: isoIn(10), is_expired: false },
    { product_id: 2, name: "Brot", amount: 0.5, unit: "kg", best_before_date: isoIn(2), is_expired: false },
    { product_id: 3, name: "Joghurt", amount: 2, unit: "", best_before_date: isoIn(-1), is_expired: true },
    { product_id: 4, name: "Reis", amount: 1, unit: "kg", best_before_date: null, is_expired: false },
  ],
  fetched_at: NOW,
};

/** Baut ein Widget und räumt es nach dem Test auf (löscht den Takt-Timer). */
function makeWidget(t, { config = {}, fetchJson = async () => PAYLOAD } = {}) {
  const container = new FakeElement("div");
  const calls = [];
  const api = {
    fetchJson: async (path) => {
      calls.push(path);
      return fetchJson(path);
    },
  };
  const widget = new GrocyStockWidget(container, config, api);
  t.after(() => widget.destroy());
  return { container, widget, calls };
}

/** Titel der Listenzeilen in Anzeige-Reihenfolge. */
const itemTitles = (widget) =>
  widget.listEl.children.map((item) => item.children[1].children[0].textContent);

const itemStatuses = (widget) =>
  widget.listEl.children.map((item) => item.dataset.status);

test("rendert den Bestand aus GET /api/grocy/stock", async (t) => {
  const { widget, calls } = makeWidget(t);
  await widget.render();
  await widget.refresh();

  assert.deepEqual(calls, ["/api/grocy/stock"]);
  assert.equal(widget.root.dataset.mode, "data");
  assert.equal(widget.valueEl.textContent, "4");
  assert.equal(widget.subEl.textContent, "Produkte im Bestand");
  assert.match(widget.contextEl.textContent, /Stand .*· Grocy/);

  // Sortierung: frühestes MHD zuerst, Produkt ohne MHD zuletzt.
  assert.deepEqual(itemTitles(widget), ["Joghurt", "Brot", "Milch", "Reis"]);
  assert.deepEqual(itemStatuses(widget), ["expired", "soon", "ok", "unknown"]);

  const text = collectText(widget.listEl).join(" | ");
  assert.match(text, /2 · MHD \d{2}\.\d{2}\.\d{4} · abgelaufen/);
  assert.match(text, /0,5 kg · MHD \d{2}\.\d{2}\.\d{4} · bald faellig/);
  assert.match(text, /1 l · MHD \d{2}\.\d{2}\.\d{4} · in Ordnung/);
  assert.match(text, /1 kg · kein MHD · ohne MHD/);
});

test("Ziel-Zeile: abgelaufen und bald fällig als Glyphe UND Text", async (t) => {
  const { widget } = makeWidget(t);
  await widget.render();
  await widget.refresh();

  const target = collectText(widget.targetEl);
  assert.equal(target[0], "⚠"); // expired
  assert.match(target[1], /^Ziel nichts abgelaufen · 1 abgelaufen, 1 bald faellig$/);
});

test("Ziel-Zeile: nichts abgelaufen/bald fällig -> Erfolgs-Glyphe (●)", async (t) => {
  const { widget } = makeWidget(t, {
    fetchJson: async () => ({
      products: [
        { product_id: 1, name: "Milch", amount: 1, unit: "l", best_before_date: isoIn(30), is_expired: false },
      ],
      fetched_at: NOW,
    }),
  });
  await widget.render();
  await widget.refresh();

  assert.equal(widget.root.dataset.status, "ok");
  const target = collectText(widget.targetEl);
  assert.equal(target[0], "●");
  assert.match(target[1], /^Nichts abgelaufen oder bald faellig \(3 Tage\)$/);
});

test("leere products-Liste ist kein Fehler (leerer Zustand)", async (t) => {
  const { widget } = makeWidget(t, {
    fetchJson: async () => ({ products: [], fetched_at: NOW }),
  });
  await widget.render();
  await widget.refresh(); // darf nicht werfen

  assert.equal(widget.root.dataset.mode, "empty");
  assert.equal(widget.valueEl.textContent, "—");
  assert.equal(widget.subEl.textContent, "Kein Bestand");
  assert.match(collectText(widget.listEl).join(" "), /Kein Bestand im Cache/);
});

test("GROCY_UNAVAILABLE ohne Vorwert -> leerer Zustand ('—'), nicht '0' (Kriterium 2)", async (t) => {
  const { widget } = makeWidget(t, {
    fetchJson: async () => ({ error_code: "GROCY_UNAVAILABLE" }),
  });
  await widget.render();
  await widget.refresh(); // darf nicht werfen

  // Kein erfundener Wert: leerer Zustand statt "0"/„in Ordnung" (I.2/E.3).
  assert.equal(widget.root.dataset.mode, "empty");
  assert.equal(widget.root.dataset.status, "unknown");
  assert.equal(widget.valueEl.textContent, "—");
  assert.notEqual(widget.valueEl.textContent, "0");
  assert.equal(widget.subEl.textContent, "Daten nicht erreichbar");
  assert.match(widget.contextEl.textContent, /nicht erreichbar/);
  assert.match(collectText(widget.listEl).join(" "), /Daten nicht erreichbar/);
});

test("GROCY_UNAVAILABLE nach erfolgreichem Abruf -> letzter Bestand bleibt, gekennzeichnet (Kriterium 1)", async (t) => {
  let available = true;
  const { widget } = makeWidget(t, {
    fetchJson: async () =>
      available ? PAYLOAD : { error_code: "GROCY_UNAVAILABLE" },
  });
  await widget.render();
  await widget.refresh();
  assert.equal(widget.root.dataset.mode, "data");
  assert.equal(widget.valueEl.textContent, "4");

  available = false;
  await widget.refresh(); // darf nicht werfen

  // Letzter bekannter Bestand bleibt erhalten (kein Datenverlust, keine "0").
  assert.equal(widget.root.dataset.mode, "degraded");
  assert.equal(widget.valueEl.textContent, "4");
  assert.notEqual(widget.valueEl.textContent, "0");
  assert.equal(widget.subEl.textContent, "Produkte im Bestand");
  assert.deepEqual(itemTitles(widget), ["Joghurt", "Brot", "Milch", "Reis"]);

  // Der Status stammt aus dem echten letzten Bestand (nicht erfundenes "ok")
  // und der Ausfall wird benannt (I.2/E.3).
  assert.equal(widget.root.dataset.status, "expired");
  assert.match(widget.contextEl.textContent, /Quelle nicht erreichbar/);

  // Erholung: wieder erreichbar -> frischer Zustand, Ausfall-Kennzeichnung weg.
  available = true;
  await widget.refresh();
  assert.equal(widget.root.dataset.mode, "data");
  assert.doesNotMatch(widget.contextEl.textContent, /nicht erreichbar/);
});

test("Netzfehler ohne Vorwert -> leerer Zustand, kein Crash", async (t) => {
  const { widget } = makeWidget(t, {
    fetchJson: async () => {
      throw Object.assign(new Error("offline"), { code: "NETWORK" });
    },
  });
  await widget.render();
  await widget.refresh(); // darf nicht werfen

  assert.equal(widget.root.dataset.mode, "empty");
  assert.equal(widget.valueEl.textContent, "—");
  assert.equal(widget.subEl.textContent, "Daten nicht erreichbar");
  assert.match(widget.contextEl.textContent, /Kein Bestand verfuegbar \(NETWORK\)/);
});

test("reine Helfer: Fenster, Limit, Klassifikation, Format, Normalisierung", () => {
  assert.equal(DEFAULT_EXPIRING_DAYS, 3);
  assert.equal(DEFAULT_LIMIT, 8);
  assert.equal(expiringDays({}), 3);
  assert.equal(expiringDays({ expiring_days: 0 }), 0);
  assert.equal(expiringDays({ expiring_days: 5 }), 5);

  assert.equal(listLimit({}), 8);
  assert.equal(listLimit({ limit: 99 }), 50);
  assert.equal(listLimit({ limit: 0 }), 1);

  assert.equal(classifyProduct({ isExpired: true, bestBeforeDate: null }, 3), "expired");
  assert.equal(classifyProduct({ isExpired: false, bestBeforeDate: null }, 3), "unknown");
  assert.equal(classifyProduct({ isExpired: false, bestBeforeDate: isoIn(2) }, 3), "soon");
  assert.equal(classifyProduct({ isExpired: false, bestBeforeDate: isoIn(30) }, 3), "ok");

  assert.equal(formatAmount({ amount: 1, unit: "l" }), "1 l");
  assert.equal(formatAmount({ amount: 0.5, unit: "kg" }), "0,5 kg");
  assert.equal(formatAmount({ amount: 3, unit: "" }), "3");

  assert.match(formatDate("2026-10-20"), /^20\.10\.2026$/);
  assert.equal(formatDate(""), null);
  assert.equal(formatDate("kein-datum"), null);

  assert.equal(relativeAge(new Date().toISOString()), "gerade eben");
  assert.equal(relativeAge(new Date(Date.now() - 120_000).toISOString(), Date.now()), "vor 2 min");
  assert.equal(relativeAge(null), null);
  assert.equal(relativeAge("kein-datum"), null);

  const read = readStock(PAYLOAD);
  assert.equal(read.recovered, true);
  assert.equal(read.items.length, 4);
  assert.equal(readStock({ error_code: "GROCY_UNAVAILABLE" }).recovered, false);
  assert.equal(readStock({ products: [] }).recovered, true); // leer ist kein Fehler

  const normalized = normalizeProduct({ product_id: 7, name: "Butter", amount: 2, unit: "Stk" });
  assert.deepEqual(normalized, {
    productId: 7,
    name: "Butter",
    amount: 2,
    unit: "Stk",
    bestBeforeDate: null,
    isExpired: false,
  });

  const sorted = sortProducts([
    { name: "Z", bestBeforeDate: null },
    { name: "A", bestBeforeDate: "2026-01-01" },
    { name: "B", bestBeforeDate: "2026-01-01" },
  ]);
  assert.deepEqual(sorted.map((p) => p.name), ["A", "B", "Z"]);
});
