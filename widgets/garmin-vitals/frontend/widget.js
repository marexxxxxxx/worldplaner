/**
 * Garmin-Vitalwerte — Widget (Widget-Plugin-Vertrag E.3, Spezifikation D.5/G.5).
 *
 * Zeigt `GET /api/garmin/vitals` (Feld-Schema G.5 `kind=vitals`): die
 * Tagesschritte als Wert, Ruhepuls, Stress, Body Battery, Etagen und aktive
 * Kalorien als Kontext sowie ein konfigurierbares Schrittziel
 * (`config.steps_target`) als Ziel mit Fortschrittsbalken.
 *
 * Netzwerk ausschließlich über das Plugin-API `api` (E.5) — kein direkter
 * `fetch`. Fehlervertrag (E.3): leerer Cache (`data:null`) oder Netzfehler
 * führen NICHT zu einer Exception; das Widget zeigt den leeren Zustand bzw.
 * den letzten bekannten Wert.
 */

export const DEFAULT_STEPS_TARGET = 10000;
const SOURCE_LABEL = "Garmin";

const NO_DECIMAL = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

/** Divergierend um das Ziel — immer als Glyphe UND Text (I.2). */
const BAND_GLYPHS = { below: "▼", within: "●", above: "▲" };
const BAND_LABELS = { below: "unter Ziel", within: "Ziel erreicht", above: "über Ziel" };

/** Schrittziel aus der Config, mit Standard und Untergrenze. */
export function stepsTarget(config = {}) {
  const value = Number(config.steps_target);
  return Number.isFinite(value) && value >= 1 ? Math.round(value) : DEFAULT_STEPS_TARGET;
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

/** Normalisiert `GET /api/garmin/vitals`; `null` bei leerem Cache. */
export function readVitals(payload) {
  if (!payload || typeof payload !== "object" || payload.error_code) {
    return null;
  }
  const data = payload.data;
  if (!data || typeof data !== "object") {
    return null;
  }
  return {
    date: typeof payload.date === "string" ? payload.date : null,
    stale: payload.stale === true,
    steps: Number.isFinite(data.steps) ? data.steps : null,
    restingHr: Number.isFinite(data.resting_hr) ? data.resting_hr : null,
    stressAvg: Number.isFinite(data.stress_avg) ? data.stress_avg : null,
    bodyBattery: Number.isFinite(data.body_battery) ? data.body_battery : null,
    floors: Number.isFinite(data.floors_climbed) ? data.floors_climbed : null,
    activeCalories: Number.isFinite(data.calories_active) ? data.calories_active : null,
  };
}

/** "14.10.2026" aus einem ISO-Datum; `null` bei ungültigem Wert. */
function formatDay(iso) {
  if (typeof iso !== "string" || iso === "") {
    return null;
  }
  const ms = Date.parse(`${iso}T00:00:00Z`);
  if (Number.isNaN(ms)) {
    return null;
  }
  return new Intl.DateTimeFormat("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "UTC",
  }).format(ms);
}

export default class GarminVitalsWidget {
  constructor(container, config, api) {
    this.container = container;
    this.api = api;
    this.config = config || {};
    this.last = null;
    this.degraded = false;
    this.lastError = null;
    this.root = null;
  }

  async render() {
    const root = document.createElement("div");
    root.className = "gv";
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", "Garmin Vitalwerte");

    const head = document.createElement("div");
    head.className = "gv-head";
    const label = document.createElement("span");
    label.className = "gv-label";
    label.textContent = "Vitalwerte";
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "gv-refresh";
    refresh.setAttribute("aria-label", "Vitalwerte aktualisieren");
    refresh.textContent = "⟳";
    refresh.addEventListener("click", () => this.refresh());
    head.append(label, refresh);

    this.valueEl = document.createElement("p");
    this.valueEl.className = "gv-value";
    this.valueEl.setAttribute("role", "status");

    this.subEl = document.createElement("p");
    this.subEl.className = "gv-sub";
    this.subEl.textContent = "Schritte";

    this.barEl = document.createElement("div");
    this.barEl.className = "gv-bar";
    this.barEl.setAttribute("aria-hidden", "true");
    this.fillEl = document.createElement("span");
    this.fillEl.className = "gv-fill";
    this.barEl.append(this.fillEl);

    this.contextEl = document.createElement("p");
    this.contextEl.className = "gv-context";

    this.targetGlyphEl = document.createElement("span");
    this.targetGlyphEl.className = "gv-target__glyph";
    this.targetGlyphEl.setAttribute("aria-hidden", "true");
    this.targetTextEl = document.createElement("span");
    this.targetTextEl.className = "gv-target__text";
    this.targetEl = document.createElement("p");
    this.targetEl.className = "gv-target";
    this.targetEl.append(this.targetGlyphEl, this.targetTextEl);

    root.append(
      head,
      this.valueEl,
      this.subEl,
      this.barEl,
      this.contextEl,
      this.targetEl,
    );
    this.container.replaceChildren(root);
    this.root = root;
    this.renderValue();
  }

  async refresh() {
    let payload = null;
    let fetchFailed = false;
    try {
      payload = await this.api.fetchJson("/api/garmin/vitals");
    } catch (error) {
      fetchFailed = true;
      this.lastError = (error && error.code) || "NETWORK";
    }

    if (fetchFailed) {
      this.degraded = true;
    } else {
      this.lastError = null;
      this.last = readVitals(payload);
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

  renderValue() {
    if (!this.root) {
      return;
    }
    const last = this.last;
    const mode = this.viewMode(last);
    this.root.dataset.mode = mode;

    const target = stepsTarget(this.config);

    if (!last) {
      this.root.dataset.band = "unknown";
      setText(this.valueEl, "—");
      setText(this.subEl, this.degraded ? "Daten nicht erreichbar" : "Schritte");
      this.fillEl.style.width = "0%";
      setText(this.contextEl, this.emptyContext());
      setText(this.targetGlyphEl, "○");
      setText(this.targetTextEl, `Ziel ${NO_DECIMAL.format(target)} Schritte`);
      return;
    }

    const steps = last.steps;
    setText(this.valueEl, steps === null ? "—" : NO_DECIMAL.format(steps));
    setText(this.subEl, "Schritte");
    const ratio = steps === null ? NaN : steps / target;
    const band = classifyRatio(ratio);
    this.fillEl.style.width = `${Number.isFinite(ratio) ? Math.max(0, Math.min(100, ratio * 100)) : 0}%`;
    setText(this.contextEl, this.contextText(last, mode));
    this.root.dataset.band = band;
    setText(this.targetGlyphEl, BAND_GLYPHS[band] || "○");
    const percent = Number.isFinite(ratio) ? ` (${NO_DECIMAL.format(ratio * 100)} %)` : "";
    setText(
      this.targetTextEl,
      `Ziel ${NO_DECIMAL.format(target)} Schritte · ${BAND_LABELS[band] || "—"}${percent}`,
    );
  }

  viewMode(last) {
    if (!last) {
      return "empty";
    }
    return this.degraded || last.stale ? "stale" : "data";
  }

  contextText(last, mode) {
    const parts = [];
    const day = formatDay(last.date);
    if (day) {
      parts.push(day);
    }
    if (last.restingHr !== null) {
      parts.push(`Ruhepuls ${NO_DECIMAL.format(last.restingHr)}/min`);
    }
    if (last.stressAvg !== null) {
      parts.push(`Stress ${NO_DECIMAL.format(last.stressAvg)}/100`);
    }
    if (last.bodyBattery !== null) {
      parts.push(`Body Battery ${NO_DECIMAL.format(last.bodyBattery)}`);
    }
    if (last.floors !== null) {
      parts.push(`Etagen ${NO_DECIMAL.format(last.floors)}`);
    }
    if (last.activeCalories !== null) {
      parts.push(`Aktiv ${NO_DECIMAL.format(last.activeCalories)} kcal`);
    }
    parts.push(SOURCE_LABEL);
    parts.push(mode === "stale" ? "Cache veraltet" : "Cache aktuell");
    return parts.join(" · ");
  }

  emptyContext() {
    if (this.lastError) {
      return `Keine Vitalwerte im Cache (${this.lastError}).`;
    }
    return "Keine Vitalwerte im Cache.";
  }
}

function setText(element, text) {
  if (element.textContent !== text) {
    element.textContent = text;
  }
}
