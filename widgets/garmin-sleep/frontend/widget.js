/**
 * Garmin-Schlaf — Widget (Widget-Plugin-Vertrag E.3, Spezifikation D.5/G.5).
 *
 * Zeigt `GET /api/garmin/sleep` (Feld-Schema G.5 `kind=sleep`): die
 * Gesamtschlafdauer als Wert, Einschlaf-/Aufwachzeit und den Sleep-Score als
 * Kontext sowie das Schlafziel (`config.sleep_target_h`) als Ziel. Die
 * Schlafphasen stehen als gestapelter Balken mit Text-Legende — vier Segmente,
 * die Aussage bleibt in Graustufen lesbar (I.2).
 *
 * Netzwerk ausschließlich über das Plugin-API `api` (E.5) — kein direkter
 * `fetch`. Fehlervertrag (E.3): Bei leerem Cache (`data:null`) oder Netzfehler
 * wirft `refresh()` NICHT; das Widget zeigt den leeren Zustand bzw. den letzten
 * bekannten Wert und der Poll des Hosts läuft weiter.
 */

export const DEFAULT_SLEEP_TARGET_H = 8;
const SOURCE_LABEL = "Garmin";

const NO_DECIMAL = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
const DATE = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit" });
const TIME = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });

/** Phasen in fester Reihenfolge; jede trägt Beschriftung und eigene Helligkeit. */
const STAGES = [
  { key: "deep_sleep_s", label: "Tief", cls: "gs-seg--deep" },
  { key: "light_sleep_s", label: "Leicht", cls: "gs-seg--light" },
  { key: "rem_sleep_s", label: "REM", cls: "gs-seg--rem" },
  { key: "awake_s", label: "Wach", cls: "gs-seg--awake" },
];

/** Divergierend um das Schlafziel — immer als Glyphe UND Text (I.2). */
const BAND_GLYPHS = { below: "▼", within: "●", above: "▲" };
const BAND_LABELS = { below: "unter Ziel", within: "Ziel erreicht", above: "über Ziel" };

/** "7 h 50 min" / "48 min"; ungültige Werte werden zu "—". */
export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "—";
  }
  const total = Math.round(seconds);
  let hours = Math.floor(total / 3600);
  let minutes = Math.round((total % 3600) / 60);
  if (minutes === 60) {
    hours += 1;
    minutes = 0;
  }
  if (hours === 0) {
    return `${minutes} min`;
  }
  return `${hours} h ${String(minutes).padStart(2, "0")} min`;
}

/** Schlafziel in Stunden aus der Config, mit Standard und Schutz gegen Unsinn. */
export function sleepTargetHours(config = {}) {
  const value = Number(config.sleep_target_h);
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_SLEEP_TARGET_H;
}

/** "unten"(<90 %) | "erreicht"(90–110 %) | "over"(>110 %) des Ziels. */
export function classifyRatio(ratio) {
  if (!Number.isFinite(ratio)) {
    return "unknown";
  }
  if (ratio < 0.9) {
    return "below";
  }
  if (ratio > 1.1) {
    return "above";
  }
  return "within";
}

/** Normalisiert `GET /api/garmin/sleep`; `null` bei leerem Cache. */
export function readSleep(payload) {
  if (!payload || typeof payload !== "object" || payload.error_code) {
    return null;
  }
  const data = payload.data;
  if (!data || typeof data !== "object" || !Number.isFinite(data.duration_s)) {
    return null;
  }
  return {
    date: typeof payload.date === "string" ? payload.date : null,
    stale: payload.stale === true,
    durationS: data.duration_s,
    sleepStart: typeof data.sleep_start === "string" ? data.sleep_start : null,
    sleepEnd: typeof data.sleep_end === "string" ? data.sleep_end : null,
    score: Number.isFinite(data.sleep_score) ? data.sleep_score : null,
    stages: {
      deep_sleep_s: Number.isFinite(data.deep_sleep_s) ? data.deep_sleep_s : 0,
      light_sleep_s: Number.isFinite(data.light_sleep_s) ? data.light_sleep_s : 0,
      rem_sleep_s: Number.isFinite(data.rem_sleep_s) ? data.rem_sleep_s : 0,
      awake_s: Number.isFinite(data.awake_s) ? data.awake_s : 0,
    },
  };
}

/** ISO-Zeitstempel -> "13.10. 22:31"; `null` bei ungültigem Wert. */
function formatMoment(iso) {
  if (typeof iso !== "string" || iso === "") {
    return null;
  }
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) {
    return null;
  }
  return `${DATE.format(ms)} ${TIME.format(ms)}`;
}

export default class GarminSleepWidget {
  constructor(container, config, api) {
    this.container = container;
    this.api = api;
    this.config = config || {};
    this.last = null; // letzter gültiger Wert (Cache im Widget)
    this.degraded = false; // letzter Abruf fehlgeschlagen
    this.lastError = null;
    this.root = null;
  }

  /** Baut das DOM einmalig auf (idempotent, E.3). Kein Netzwerk. */
  async render() {
    const root = document.createElement("div");
    root.className = "gs";
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", "Garmin Schlaf");

    const head = document.createElement("div");
    head.className = "gs-head";
    const label = document.createElement("span");
    label.className = "gs-label";
    label.textContent = "Schlaf";
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "gs-refresh";
    refresh.setAttribute("aria-label", "Schlafdaten aktualisieren");
    refresh.textContent = "⟳";
    refresh.addEventListener("click", () => this.refresh());
    head.append(label, refresh);

    this.valueEl = document.createElement("p");
    this.valueEl.className = "gs-value";
    this.valueEl.setAttribute("role", "status");

    this.scoreEl = document.createElement("p");
    this.scoreEl.className = "gs-score";

    this.barEl = document.createElement("div");
    this.barEl.className = "gs-bar";
    this.barEl.setAttribute("aria-hidden", "true");

    this.legendEl = document.createElement("p");
    this.legendEl.className = "gs-legend";

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
      this.scoreEl,
      this.barEl,
      this.legendEl,
      this.contextEl,
      this.targetEl,
    );
    this.container.replaceChildren(root);
    this.root = root;
    this.renderValue();
  }

  /** Lädt `/api/garmin/sleep` und aktualisiert nur die Inhalte (kein Layout-Sprung). */
  async refresh() {
    let payload = null;
    let fetchFailed = false;
    try {
      payload = await this.api.fetchJson("/api/garmin/sleep");
    } catch (error) {
      fetchFailed = true;
      this.lastError = (error && error.code) || "NETWORK";
    }

    if (fetchFailed) {
      this.degraded = true;
    } else {
      this.lastError = null;
      this.last = readSleep(payload); // null bei leerem Cache (kein Fehler)
      this.degraded = false;
    }
    this.renderValue();
  }

  onConfigChange(newConfig) {
    this.config = { ...this.config, ...(newConfig || {}) };
    this.renderValue();
  }

  destroy() {
    this.root = null;
    this.container.replaceChildren();
  }

  // --- Rendering (Domänenlogik rein und testbar gehalten) -------------------

  renderValue() {
    if (!this.root) {
      return;
    }
    const last = this.last;
    const mode = this.viewMode(last);
    this.root.dataset.mode = mode;

    const targetS = sleepTargetHours(this.config) * 3600;

    if (!last) {
      this.root.dataset.band = "unknown";
      setText(this.valueEl, "—");
      setText(
        this.scoreEl,
        this.degraded ? "Daten nicht erreichbar" : "Keine Schlafdaten",
      );
      this.barEl.replaceChildren();
      this.barEl.hidden = true;
      setText(this.legendEl, "");
      this.legendEl.hidden = true;
      setText(this.contextEl, this.emptyContext());
      setText(this.targetGlyphEl, "○");
      setText(this.targetTextEl, `Ziel ${formatDuration(targetS)}`);
      return;
    }

    setText(this.valueEl, formatDuration(last.durationS));
    setText(
      this.scoreEl,
      last.score === null ? "Sleep-Score nicht verfügbar" : `Sleep-Score ${last.score}/100`,
    );
    this.renderStages(last.stages);
    setText(this.contextEl, this.contextText(last, mode));

    const ratio = last.durationS / targetS;
    const band = classifyRatio(ratio);
    this.root.dataset.band = band;
    setText(this.targetGlyphEl, BAND_GLYPHS[band] || "○");
    setText(
      this.targetTextEl,
      `Ziel ${formatDuration(targetS)} · ${BAND_LABELS[band] || "—"} (${NO_DECIMAL.format(
        ratio * 100,
      )} %)`,
    );
  }

  /** "data" (frisch) | "stale" (Cache/Ausfall) | "empty" (kein Wert). */
  viewMode(last) {
    if (!last) {
      return "empty";
    }
    return this.degraded || last.stale ? "stale" : "data";
  }

  renderStages(stages) {
    const total = STAGES.reduce((sum, stage) => sum + (stages[stage.key] || 0), 0);
    this.barEl.replaceChildren();
    this.legendEl.replaceChildren();
    if (total <= 0) {
      this.barEl.hidden = true;
      this.legendEl.hidden = true;
      return;
    }
    this.barEl.hidden = false;
    this.legendEl.hidden = false;

    const legend = [];
    for (const stage of STAGES) {
      const seconds = stages[stage.key] || 0;
      const segment = document.createElement("span");
      segment.className = `gs-seg ${stage.cls}`;
      segment.style.flexGrow = String(seconds);
      segment.title = `${stage.label}: ${formatDuration(seconds)}`;
      this.barEl.append(segment);
      if (seconds > 0) {
        legend.push(`${stage.label} ${formatDuration(seconds)}`);
      }
    }
    setText(this.legendEl, legend.join(" · "));
  }

  contextText(last, mode) {
    const parts = [];
    const from = formatMoment(last.sleepStart);
    const to = formatMoment(last.sleepEnd);
    if (from && to) {
      parts.push(`${from} – ${to}`);
    } else if (last.date) {
      parts.push(last.date);
    }
    parts.push(SOURCE_LABEL);
    parts.push(mode === "stale" ? "Cache veraltet" : "Cache aktuell");
    return parts.join(" · ");
  }

  emptyContext() {
    if (this.lastError) {
      return `Keine Schlafdaten im Cache (${this.lastError}).`;
    }
    return "Keine Schlafdaten im Cache.";
  }
}

/** Setzt Text nur bei Änderung (verhindert unnötige Screenreader-Ansagen). */
function setText(element, text) {
  if (element.textContent !== text) {
    element.textContent = text;
  }
}
