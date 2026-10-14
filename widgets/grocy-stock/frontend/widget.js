/**
 * Grocy-Vorratsbestand — Widget (Widget-Plugin-Vertrag E.3, spec-phase2 D/WP-P2-2).
 *
 * Zeigt `GET /api/grocy/stock`: die Anzahl der Produkte im Bestand als Wert
 * sowie Produkt, Menge/Einheit und naechstes MHD als Liste (Kontext). Bald
 * ablaufende bzw. abgelaufene Produkte werden als Ziel-Linie ausgewiesen.
 *
 * Netzwerk ausschliesslich ueber das Plugin-API `api` (E.5) — kein direkter
 * `fetch`. Fehlervertrag (E.3): eine nicht erreichbare Quelle
 * (`{"error_code":"GROCY_UNAVAILABLE"}`) oder ein leeres Ergebnis fuehren NICHT
 * zu einer Exception; das Widget zeigt den letzten bekannten Wert (als Cache
 * gekennzeichnet) bzw. einen leeren Zustand. Der Host bleibt stabil.
 */

export const DEFAULT_EXPIRING_DAYS = 3;
export const DEFAULT_LIMIT = 8;
const MAX_LIMIT = 50;

const SOURCE_LABEL = "Grocy";

/** Aeltere Werte gelten als Cache/veraltet (Backend-TTL ist 600 s, D12). */
export const STALE_AFTER_MS = 15 * 60 * 1000;

/** Takt fuer die relative Zeitangabe ("Stand vor X min"). */
const CONTEXT_TICK_MS = 30 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

const INTEGER = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
const AMOUNT = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
const DATE = new Intl.DateTimeFormat("de-DE", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

/** Produkt-Zustand — Glyphe UND Text, nie allein Farbe/Form (I.2). */
const STATUS_GLYPHS = { expired: "⚠", soon: "⏳", ok: "●", unknown: "○" };
const STATUS_LABELS = {
  expired: "abgelaufen",
  soon: "bald faellig",
  ok: "in Ordnung",
  unknown: "ohne MHD",
};

export function expiringDays(config = {}) {
  const value = Number(config.expiring_days);
  return Number.isFinite(value) && value >= 0 ? Math.round(value) : DEFAULT_EXPIRING_DAYS;
}

/** Listenlaenge aus der Config, geklemmt auf 1–50. */
export function listLimit(config = {}) {
  const value = Number(config.limit);
  if (!Number.isFinite(value)) {
    return DEFAULT_LIMIT;
  }
  return Math.max(1, Math.min(MAX_LIMIT, Math.round(value)));
}

function isoDate(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

/** "abgelaufen" | "bald faellig" | "in Ordnung" | "ohne MHD". */
export function classifyProduct(product, days, now = Date.now()) {
  if (product.isExpired) {
    return "expired";
  }
  if (!product.bestBeforeDate) {
    return "unknown";
  }
  const threshold = isoDate(now + days * DAY_MS);
  return product.bestBeforeDate <= threshold ? "soon" : "ok";
}

export function normalizeProduct(raw) {
  return {
    productId: Number.isInteger(raw.product_id) ? raw.product_id : null,
    name: typeof raw.name === "string" ? raw.name : "",
    amount: Number.isFinite(raw.amount) ? raw.amount : 0,
    unit: typeof raw.unit === "string" ? raw.unit : "",
    bestBeforeDate:
      typeof raw.best_before_date === "string" && raw.best_before_date !== ""
        ? raw.best_before_date
        : null,
    isExpired: raw.is_expired === true,
  };
}

/** Normalisiert `GET /api/grocy/stock`; ein leeres Ergebnis ist kein Fehler. */
export function readStock(payload) {
  if (!payload || typeof payload !== "object" || payload.error_code) {
    return {
      items: [],
      fetchedAt: null,
      recovered: false,
      errorCode: (payload && payload.error_code) || null,
    };
  }
  const list = Array.isArray(payload.products) ? payload.products : [];
  const items = list
    .filter((entry) => entry && typeof entry === "object")
    .map(normalizeProduct)
    .filter((product) => product.productId !== null);
  return {
    items,
    fetchedAt: typeof payload.fetched_at === "string" ? payload.fetched_at : null,
    recovered: true,
    errorCode: null,
  };
}

/** Fruehestes MHD zuerst, Produkte ohne MHD zuletzt; gleichstand nach Name. */
export function sortProducts(items) {
  return [...items].sort((a, b) => {
    if (a.bestBeforeDate === b.bestBeforeDate) {
      return a.name.localeCompare(b.name, "de");
    }
    if (a.bestBeforeDate === null) {
      return 1;
    }
    if (b.bestBeforeDate === null) {
      return -1;
    }
    return a.bestBeforeDate < b.bestBeforeDate ? -1 : 1;
  });
}

/** "2 l" / "0,5 kg" / "3" (ohne Einheit). */
export function formatAmount(product) {
  const value = AMOUNT.format(product.amount);
  return product.unit ? `${value} ${product.unit}` : value;
}

/** "20.10.2026"; ungueltige/fehlende Angabe -> `null`. */
export function formatDate(iso) {
  if (typeof iso !== "string" || iso === "") {
    return null;
  }
  const ms = Date.parse(`${iso}T00:00:00Z`);
  return Number.isNaN(ms) ? null : DATE.format(ms);
}

/** Relative Altersangabe; `null`, wenn kein gueltiger Zeitstempel vorliegt. */
export function relativeAge(fetchedAt, now = Date.now()) {
  if (typeof fetchedAt !== "string" || fetchedAt === "") {
    return null;
  }
  const then = Date.parse(fetchedAt);
  if (Number.isNaN(then)) {
    return null;
  }
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 90) {
    return "gerade eben";
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `vor ${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `vor ${hours} h` : `vor ${hours} h ${rest} min`;
}

export default class GrocyStockWidget {
  constructor(container, config, api) {
    this.container = container;
    this.api = api;
    this.config = config || {};
    this.days = expiringDays(this.config);
    this.limit = listLimit(this.config);
    this.last = null; // letzter gueltiger Bestand { items, fetchedAt }
    this.degraded = false; // letzter Abruf ohne frischen Provider-Wert
    this.lastError = null;
    this.root = null;
    this.tick = null;
  }

  /** Baut das DOM einmalig auf (idempotent, E.3). Kein Netzwerk. */
  async render() {
    const root = document.createElement("div");
    root.className = "gs";
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", "Vorratsbestand");

    const head = document.createElement("div");
    head.className = "gs-head";
    const label = document.createElement("span");
    label.className = "gs-label";
    label.textContent = "Vorrat";
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "gs-refresh";
    refresh.setAttribute("aria-label", "Vorrat aktualisieren");
    refresh.textContent = "⟳";
    refresh.addEventListener("click", () => this.refresh());
    head.append(label, refresh);

    this.valueEl = document.createElement("p");
    this.valueEl.className = "gs-value";
    this.valueEl.setAttribute("role", "status");

    this.subEl = document.createElement("p");
    this.subEl.className = "gs-sub";

    this.listEl = document.createElement("ul");
    this.listEl.className = "gs-list";

    this.contextEl = document.createElement("p");
    this.contextEl.className = "gs-context";

    this.targetGlyphEl = document.createElement("span");
    this.targetGlyphEl.className = "gs-target__glyph";
    this.targetGlyphEl.setAttribute("aria-hidden", "true");
    this.targetTextEl = document.createElement("span");
    this.targetTextEl.className = "gs-target__text";
    this.targetEl = document.createElement("p");
    this.targetEl.className = "gs-target";
    this.targetEl.append(this.targetGlyphEl, this.targetTextEl);

    root.append(
      head,
      this.valueEl,
      this.subEl,
      this.listEl,
      this.contextEl,
      this.targetEl,
    );
    this.container.replaceChildren(root);
    this.root = root;
    this.renderValue();

    if (this.tick === null) {
      this.tick = setInterval(() => this.renderValue(), CONTEXT_TICK_MS);
    }
  }

  /** Laedt `/api/grocy/stock`; wirft nie wegen fehlender Provider-Daten (E.3). */
  async refresh() {
    let payload = null;
    let fetchFailed = false;
    try {
      payload = await this.api.fetchJson("/api/grocy/stock");
    } catch (error) {
      fetchFailed = true;
      this.lastError = (error && error.code) || "NETWORK";
    }

    if (fetchFailed) {
      this.degraded = true; // letzten bekannten Wert behalten
    } else {
      const result = readStock(payload);
      if (result.recovered) {
        this.degraded = false;
        this.lastError = null;
        // Leerer Bestand ist kein Fehler (Kriterium 3): kein Wert -> leerer
        // Zustand ("—") statt "0 Produkte".
        this.last =
          result.items.length === 0
            ? null
            : { items: result.items, fetchedAt: result.fetchedAt };
      } else {
        // Nicht erreichbar (Kriterium 1/2): den zuletzt bekannten Bestand
        // behalten und als Quelldefekt kennzeichnen; ohne Vorwert bleibt
        // `last === null` -> leerer Zustand ("—", kein erfundenes "0").
        this.degraded = true;
        this.lastError = result.errorCode || "GROCY_UNAVAILABLE";
      }
    }
    this.renderValue();
  }

  onConfigChange(newConfig) {
    this.config = { ...this.config, ...(newConfig || {}) };
    this.days = expiringDays(this.config);
    this.limit = listLimit(this.config);
    this.renderValue();
  }

  destroy() {
    if (this.tick !== null) {
      clearInterval(this.tick);
      this.tick = null;
    }
    this.root = null;
    this.container.replaceChildren();
  }

  renderValue() {
    if (!this.root) {
      return;
    }
    const last = this.last;
    const mode = this.viewMode(last);
    this.root.dataset.mode = mode;

    if (!last) {
      this.root.dataset.status = "unknown";
      setText(this.valueEl, "—");
      setText(this.subEl, this.degraded ? "Daten nicht erreichbar" : "Kein Bestand");
      this.renderList([]);
      setText(this.contextEl, this.emptyContext());
      setText(this.targetGlyphEl, "");
      setText(this.targetTextEl, this.targetText(null));
      return;
    }

    const items = sortProducts(last.items);
    const counts = this.countByStatus(items);
    setText(this.valueEl, INTEGER.format(items.length));
    setText(
      this.subEl,
      `Produkt${items.length === 1 ? "" : "e"} im Bestand`,
    );
    this.renderList(items);

    const status =
      counts.expired > 0 ? "expired" : counts.soon > 0 ? "soon" : "ok";
    this.root.dataset.status = status;
    setText(this.contextEl, this.contextText(last, mode));
    setText(this.targetGlyphEl, STATUS_GLYPHS[status]);
    setText(this.targetTextEl, this.targetText(counts));
  }

  /** "data" (frisch) | "degraded" (Cache/Ausfall) | "empty" (kein Wert). */
  viewMode(last) {
    if (!last) {
      return "empty";
    }
    if (this.degraded || last.fetchedAt === null) {
      return "degraded";
    }
    const age = Date.now() - Date.parse(last.fetchedAt);
    return Number.isNaN(age) || age > STALE_AFTER_MS ? "degraded" : "data";
  }

  countByStatus(items) {
    const counts = { expired: 0, soon: 0, ok: 0, unknown: 0 };
    for (const product of items) {
      counts[classifyProduct(product, this.days)] += 1;
    }
    return counts;
  }

  renderList(items) {
    this.listEl.replaceChildren();
    if (items.length === 0) {
      const empty = document.createElement("li");
      empty.className = "gs-empty";
      empty.textContent = this.degraded
        ? "Daten nicht erreichbar."
        : "Kein Bestand im Cache.";
      this.listEl.append(empty);
      return;
    }

    for (const product of items.slice(0, this.limit)) {
      const status = classifyProduct(product, this.days);
      const item = document.createElement("li");
      item.className = "gs-item";
      item.dataset.status = status;

      const glyph = document.createElement("span");
      glyph.className = "gs-item__glyph";
      glyph.setAttribute("aria-hidden", "true");
      glyph.textContent = STATUS_GLYPHS[status];

      const main = document.createElement("span");
      main.className = "gs-item__main";
      const title = document.createElement("span");
      title.className = "gs-item__title";
      title.textContent = product.name || "Unbenannt";
      const meta = document.createElement("span");
      meta.className = "gs-item__meta";
      meta.textContent = this.itemMeta(product, status);
      main.append(title, meta);

      item.append(glyph, main);
      this.listEl.append(item);
    }

    if (items.length > this.limit) {
      const rest = document.createElement("li");
      rest.className = "gs-more";
      rest.textContent = `+ ${INTEGER.format(items.length - this.limit)} weitere`;
      this.listEl.append(rest);
    }
  }

  itemMeta(product, status) {
    const parts = [formatAmount(product)];
    const date = formatDate(product.bestBeforeDate);
    parts.push(date ? `MHD ${date}` : "kein MHD");
    parts.push(STATUS_LABELS[status]);
    return parts.join(" · ");
  }

  contextText(last, mode) {
    const parts = [];
    const age = relativeAge(last.fetchedAt);
    parts.push(age ? `Stand ${age}` : "Stand unbekannt");
    parts.push(SOURCE_LABEL);
    if (mode === "degraded") {
      parts.push(this.degraded ? "Quelle nicht erreichbar" : "Cache veraltet");
    }
    return parts.join(" · ");
  }

  targetText(counts) {
    if (!counts) {
      return "Bald ablaufend: —";
    }
    const parts = [];
    if (counts.expired > 0) {
      parts.push(`${INTEGER.format(counts.expired)} abgelaufen`);
    }
    if (counts.soon > 0) {
      parts.push(`${INTEGER.format(counts.soon)} bald faellig`);
    }
    return parts.length === 0
      ? `Nichts abgelaufen oder bald faellig (${INTEGER.format(this.days)} Tage)`
      : `Ziel nichts abgelaufen · ${parts.join(", ")}`;
  }

  emptyContext() {
    if (this.lastError === "GROCY_UNAVAILABLE") {
      return "Grocy nicht erreichbar · kein Cache-Wert vorhanden.";
    }
    if (this.lastError) {
      return `Kein Bestand verfuegbar (${this.lastError}).`;
    }
    return "Noch keine Daten geladen.";
  }
}

/** Setzt Text nur bei Aenderung (verhindert unnoetige Screenreader-Ansagen). */
function setText(element, text) {
  if (element.textContent !== text) {
    element.textContent = text;
  }
}
