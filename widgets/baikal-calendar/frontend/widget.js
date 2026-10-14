/**
 * Baikal-Kalender — Widget (Widget-Plugin-Vertrag E.3, spec-phase2 D/WP-P2-1).
 *
 * Zeigt `GET /api/baikal/events`: die Anzahl kommender Termine als Wert und je
 * Termin **Titel + Startzeit** als Liste (Kontext). Ganztagstermine werden als
 * solche ausgewiesen. Die naechste Startzeit bildet die Ziel-Linie der Kachel.
 *
 * Netzwerk ausschliesslich ueber das Plugin-API `api` (E.5) — kein direkter
 * `fetch`. Fehlervertrag (E.3): eine nicht erreichbare Quelle
 * (`{"error_code":"BAIKAL_UNAVAILABLE"}`) oder eine leere Liste fuehren NICHT zu
 * einer Exception; das Widget zeigt den letzten bekannten Wert (als Cache
 * gekennzeichnet) bzw. einen leeren Zustand. Der Host bleibt stabil.
 */

export const DEFAULT_LIMIT = 6;
const MAX_LIMIT = 20;

const SOURCE_LABEL = "Baikal";

/** Aeltere Werte gelten als Cache/veraltet (Backend-TTL ist 600 s, D12). */
export const STALE_AFTER_MS = 15 * 60 * 1000;

/** Takt fuer die relative Zeitangabe ("Stand vor X min"). */
const CONTEXT_TICK_MS = 30 * 1000;

const INTEGER = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
const DATE_TIME = new Intl.DateTimeFormat("de-DE", {
  weekday: "short",
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});
const DATE_ONLY = new Intl.DateTimeFormat("de-DE", {
  weekday: "short",
  day: "2-digit",
  month: "2-digit",
});

/** Listenlaenge aus der Config, geklemmt auf 1–20. */
export function listLimit(config = {}) {
  const value = Number(config.limit);
  if (!Number.isFinite(value)) {
    return DEFAULT_LIMIT;
  }
  return Math.max(1, Math.min(MAX_LIMIT, Math.round(value)));
}

/** Ein Termin aus `GET /api/baikal/events` -> Normalform. */
export function normalizeEvent(raw) {
  return {
    uid: typeof raw.uid === "string" ? raw.uid : "",
    summary: typeof raw.summary === "string" ? raw.summary : "",
    start: typeof raw.start === "string" ? raw.start : "",
    end: typeof raw.end === "string" ? raw.end : "",
    allDay: raw.all_day === true,
    location: typeof raw.location === "string" ? raw.location : "",
  };
}

/** Normalisiert `GET /api/baikal/events`; eine leere Liste ist kein Fehler. */
export function readEvents(payload) {
  if (!payload || typeof payload !== "object" || payload.error_code) {
    return {
      items: [],
      fetchedAt: null,
      recovered: false,
      errorCode: (payload && payload.error_code) || null,
    };
  }
  const list = Array.isArray(payload.events) ? payload.events : [];
  const items = list
    .filter((entry) => entry && typeof entry === "object")
    .map(normalizeEvent)
    .filter((event) => event.start !== "");
  return {
    items,
    fetchedAt: typeof payload.fetched_at === "string" ? payload.fetched_at : null,
    recovered: true,
    errorCode: null,
  };
}

/** Startzeit als Text: "Di 20.10. · 09:00" bzw. "Di 20.10. · ganztägig". */
export function formatStart(event) {
  if (typeof event.start !== "string" || event.start === "") {
    return null;
  }
  const ms = Date.parse(event.start);
  if (Number.isNaN(ms)) {
    return null;
  }
  return event.allDay
    ? `${DATE_ONLY.format(ms)} · ganztägig`
    : DATE_TIME.format(ms);
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

export default class BaikalCalendarWidget {
  constructor(container, config, api) {
    this.container = container;
    this.api = api;
    this.config = config || {};
    this.limit = listLimit(this.config);
    this.last = null; // letzter gueltiger Termin-Stand { items, fetchedAt }
    this.degraded = false; // letzter Abruf ohne frischen Provider-Wert
    this.lastError = null;
    this.root = null;
    this.tick = null;
  }

  /** Baut das DOM einmalig auf (idempotent, E.3). Kein Netzwerk. */
  async render() {
    const root = document.createElement("div");
    root.className = "bc";
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", "Kalender");

    const head = document.createElement("div");
    head.className = "bc-head";
    const label = document.createElement("span");
    label.className = "bc-label";
    label.textContent = "Kalender";
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "bc-refresh";
    refresh.setAttribute("aria-label", "Kalender aktualisieren");
    refresh.textContent = "⟳";
    refresh.addEventListener("click", () => this.refresh());
    head.append(label, refresh);

    this.valueEl = document.createElement("p");
    this.valueEl.className = "bc-value";
    this.valueEl.setAttribute("role", "status");

    this.subEl = document.createElement("p");
    this.subEl.className = "bc-sub";

    this.listEl = document.createElement("ul");
    this.listEl.className = "bc-list";

    this.contextEl = document.createElement("p");
    this.contextEl.className = "bc-context";

    this.targetEl = document.createElement("p");
    this.targetEl.className = "bc-target";

    root.append(head, this.valueEl, this.subEl, this.listEl, this.contextEl, this.targetEl);
    this.container.replaceChildren(root);
    this.root = root;
    this.renderValue();

    if (this.tick === null) {
      this.tick = setInterval(() => this.renderValue(), CONTEXT_TICK_MS);
    }
  }

  /** Laedt `/api/baikal/events`; wirft nie wegen fehlender Provider-Daten (E.3). */
  async refresh() {
    let payload = null;
    let fetchFailed = false;
    try {
      payload = await this.api.fetchJson("/api/baikal/events");
    } catch (error) {
      fetchFailed = true;
      this.lastError = (error && error.code) || "NETWORK";
    }

    if (fetchFailed) {
      this.degraded = true;
    } else {
      const result = readEvents(payload);
      this.lastError = result.errorCode;
      if (result.recovered) {
        this.degraded = false;
        // Leere Liste ist kein Fehler: kein Wert -> leerer Zustand ("—").
        this.last =
          result.items.length === 0
            ? null
            : { items: result.items, fetchedAt: result.fetchedAt };
      } else {
        // Fehlerobjekt: letzten bekannten Wert behalten; ohne -> leerer Zustand.
        this.degraded = true;
      }
    }
    this.renderValue();
  }

  onConfigChange(newConfig) {
    this.config = { ...this.config, ...(newConfig || {}) };
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
      setText(this.valueEl, "—");
      setText(this.subEl, this.degraded ? "Daten nicht erreichbar" : "Keine Termine");
      this.renderList([]);
      setText(this.contextEl, this.emptyContext());
      setText(this.targetEl, "Nächster Termin: —");
      return;
    }

    const items = last.items;
    setText(this.valueEl, INTEGER.format(items.length));
    setText(this.subEl, `Termin${items.length === 1 ? "" : "e"} geplant`);
    this.renderList(items);
    setText(this.contextEl, this.contextText(last, mode));
    setText(this.targetEl, this.targetText(items));
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

  renderList(items) {
    this.listEl.replaceChildren();
    if (items.length === 0) {
      const empty = document.createElement("li");
      empty.className = "bc-empty";
      empty.textContent = this.degraded
        ? "Daten nicht erreichbar."
        : "Keine Termine im Cache.";
      this.listEl.append(empty);
      return;
    }

    for (const event of items.slice(0, this.limit)) {
      const item = document.createElement("li");
      item.className = "bc-item";
      item.dataset.allDay = String(event.allDay);

      const glyph = document.createElement("span");
      glyph.className = "bc-item__glyph";
      glyph.setAttribute("aria-hidden", "true");
      glyph.textContent = event.allDay ? "◼" : "•";

      const main = document.createElement("span");
      main.className = "bc-item__main";
      const title = document.createElement("span");
      title.className = "bc-item__title";
      title.textContent = event.summary || "(ohne Titel)";
      const meta = document.createElement("span");
      meta.className = "bc-item__meta";
      meta.textContent = this.itemMeta(event);
      main.append(title, meta);

      item.append(glyph, main);
      this.listEl.append(item);
    }

    if (items.length > this.limit) {
      const rest = document.createElement("li");
      rest.className = "bc-more";
      rest.textContent = `+ ${INTEGER.format(items.length - this.limit)} weitere`;
      this.listEl.append(rest);
    }
  }

  itemMeta(event) {
    const parts = [formatStart(event) || "Zeit unbekannt"];
    if (event.location) {
      parts.push(event.location);
    }
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

  targetText(items) {
    const next = items[0];
    const start = next && formatStart(next);
    if (!start) {
      return "Nächster Termin: —";
    }
    return `Nächster Termin: ${start} · ${next.summary || "(ohne Titel)"}`;
  }

  emptyContext() {
    if (this.lastError === "BAIKAL_UNAVAILABLE") {
      return "Baikal nicht erreichbar · kein Cache-Wert vorhanden.";
    }
    if (this.lastError) {
      return `Keine Termine verfuegbar (${this.lastError}).`;
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
