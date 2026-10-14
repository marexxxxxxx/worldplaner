/**
 * Garmin-Readiness — Widget (Widget-Plugin-Vertrag E.3, Spezifikation D.5/G.5).
 *
 * Zeigt `GET /api/garmin/readiness` (Feld-Schema G.5 `kind=readiness`,
 * Readiness-Ersatz nach D11): Body Battery als Wert, die Tagesstreuung als
 * Balken (low..high mit Marke), Stress, Ruhepuls und Sleep-Score als Kontext
 * sowie ein konfigurierbares Body-Battery-Ziel (`config.body_battery_target`).
 *
 * Netzwerk ausschließlich über das Plugin-API `api` (E.5) — kein direkter
 * `fetch`. Fehlervertrag (E.3): leerer Cache (`data:null`) oder Netzfehler
 * führen NICHT zu einer Exception; das Widget zeigt den leeren Zustand bzw.
 * den letzten bekannten Wert.
 */

export const DEFAULT_BODY_BATTERY_TARGET = 50;
const SOURCE_LABEL = "Garmin";

const NO_DECIMAL = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

/** Divergierend um das Ziel — immer als Glyphe UND Text (I.2). */
const BAND_GLYPHS = { below: "▼", within: "●", above: "▲" };
const BAND_LABELS = { below: "unter Ziel", within: "Ziel erreicht", above: "über Ziel" };

/** Body-Battery-Ziel (0–100) aus der Config, mit Standard und Schutz. */
export function bodyBatteryTarget(config = {}) {
  const value = Number(config.body_battery_target);
  return Number.isFinite(value) && value >= 0 && value <= 100
    ? value
    : DEFAULT_BODY_BATTERY_TARGET;
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

/** Position in Prozent auf einer 0–100-Achse, geklemmt. */
export function axisPercent(value) {
  if (!Number.isFinite(value)) {
    return null;
  }
  return `${Math.max(0, Math.min(100, value))}%`;
}

/** Normalisiert `GET /api/garmin/readiness`; `null` bei leerem Cache. */
export function readReadiness(payload) {
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
    bodyBattery: Number.isFinite(data.body_battery) ? data.body_battery : null,
    high: Number.isFinite(data.body_battery_high) ? data.body_battery_high : null,
    low: Number.isFinite(data.body_battery_low) ? data.body_battery_low : null,
    stressAvg: Number.isFinite(data.stress_avg) ? data.stress_avg : null,
    sleepScore: Number.isFinite(data.sleep_score) ? data.sleep_score : null,
    restingHr: Number.isFinite(data.resting_hr) ? data.resting_hr : null,
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

export default class GarminReadinessWidget {
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
    root.className = "gr";
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", "Garmin Readiness");

    const head = document.createElement("div");
    head.className = "gr-head";
    const label = document.createElement("span");
    label.className = "gr-label";
    label.textContent = "Readiness";
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "gr-refresh";
    refresh.setAttribute("aria-label", "Readiness aktualisieren");
    refresh.textContent = "⟳";
    refresh.addEventListener("click", () => this.refresh());
    head.append(label, refresh);

    this.valueEl = document.createElement("p");
    this.valueEl.className = "gr-value";
    this.valueEl.setAttribute("role", "status");
    this.valueTextEl = document.createElement("span");
    this.valueTextEl.className = "gr-value__text";
    this.unitEl = document.createElement("span");
    this.unitEl.className = "gr-value__unit";
    this.unitEl.textContent = " / 100";
    this.valueEl.append(this.valueTextEl, this.unitEl);

    this.subEl = document.createElement("p");
    this.subEl.className = "gr-sub";
    this.subEl.textContent = "Body Battery";

    this.barEl = document.createElement("div");
    this.barEl.className = "gr-bar";
    this.barEl.setAttribute("aria-hidden", "true");

    this.legendEl = document.createElement("p");
    this.legendEl.className = "gr-legend";

    this.contextEl = document.createElement("p");
    this.contextEl.className = "gr-context";

    this.targetGlyphEl = document.createElement("span");
    this.targetGlyphEl.className = "gr-target__glyph";
    this.targetGlyphEl.setAttribute("aria-hidden", "true");
    this.targetTextEl = document.createElement("span");
    this.targetTextEl.className = "gr-target__text";
    this.targetEl = document.createElement("p");
    this.targetEl.className = "gr-target";
    this.targetEl.append(this.targetGlyphEl, this.targetTextEl);

    root.append(
      head,
      this.valueEl,
      this.subEl,
      this.barEl,
      this.legendEl,
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
      payload = await this.api.fetchJson("/api/garmin/readiness");
    } catch (error) {
      fetchFailed = true;
      this.lastError = (error && error.code) || "NETWORK";
    }

    if (fetchFailed) {
      this.degraded = true;
    } else {
      this.lastError = null;
      this.last = readReadiness(payload);
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

    const target = bodyBatteryTarget(this.config);

    if (!last) {
      this.root.dataset.band = "unknown";
      setText(this.valueTextEl, "—");
      this.unitEl.hidden = true;
      setText(this.subEl, this.degraded ? "Daten nicht erreichbar" : "Body Battery");
      this.barEl.replaceChildren();
      this.barEl.hidden = true;
      setText(this.legendEl, "");
      this.legendEl.hidden = true;
      setText(this.contextEl, this.emptyContext());
      setText(this.targetGlyphEl, "○");
      setText(this.targetTextEl, `Ziel ≥ ${NO_DECIMAL.format(target)}`);
      return;
    }

    this.unitEl.hidden = last.bodyBattery === null;
    setText(this.valueTextEl, last.bodyBattery === null ? "—" : NO_DECIMAL.format(last.bodyBattery));
    setText(this.subEl, "Body Battery (0–100)");
    this.renderRange(last, target);
    setText(this.contextEl, this.contextText(last, mode));

    const ratio = last.bodyBattery === null ? NaN : last.bodyBattery / target;
    const band = classifyRatio(ratio);
    this.root.dataset.band = band;
    setText(this.targetGlyphEl, BAND_GLYPHS[band] || "○");
    const suffix = last.bodyBattery === null ? "" : ` (${NO_DECIMAL.format(last.bodyBattery)})`;
    setText(
      this.targetTextEl,
      `Ziel ≥ ${NO_DECIMAL.format(target)} · ${BAND_LABELS[band] || "—"}${suffix}`,
    );
  }

  viewMode(last) {
    if (!last) {
      return "empty";
    }
    return this.degraded || last.stale ? "stale" : "data";
  }

  /** Tagesstreuung (low..high) mit Marke für den aktuellen Wert und Ziel-Linie. */
  renderRange(last, target) {
    this.barEl.replaceChildren();
    const legend = [];

    if (last.low !== null && last.high !== null) {
      const range = document.createElement("span");
      range.className = "gr-range";
      const low = Math.min(last.low, last.high);
      const high = Math.max(last.low, last.high);
      range.style.left = axisPercent(low) || "0%";
      range.style.width = `${Math.max(0, Math.min(100, high) - Math.max(0, Math.min(100, low)))}%`;
      this.barEl.append(range);
      legend.push(`Tagesverlauf ${NO_DECIMAL.format(last.low)}–${NO_DECIMAL.format(last.high)}`);
    }

    const targetLine = document.createElement("span");
    targetLine.className = "gr-target-line";
    targetLine.style.left = axisPercent(target) || "0%";
    this.barEl.append(targetLine);

    if (last.bodyBattery !== null) {
      const marker = document.createElement("span");
      marker.className = "gr-marker";
      marker.style.left = axisPercent(last.bodyBattery) || "0%";
      this.barEl.append(marker);
    }

    this.barEl.hidden = false;
    setText(this.legendEl, legend.join(" · "));
    this.legendEl.hidden = legend.length === 0;
  }

  contextText(last, mode) {
    const parts = [];
    const day = formatDay(last.date);
    if (day) {
      parts.push(day);
    }
    if (last.stressAvg !== null) {
      parts.push(`Stress ${NO_DECIMAL.format(last.stressAvg)}/100`);
    }
    if (last.restingHr !== null) {
      parts.push(`Ruhepuls ${NO_DECIMAL.format(last.restingHr)}/min`);
    }
    if (last.sleepScore !== null) {
      parts.push(`Sleep-Score ${NO_DECIMAL.format(last.sleepScore)}/100`);
    }
    parts.push(SOURCE_LABEL);
    parts.push(mode === "stale" ? "Cache veraltet" : "Cache aktuell");
    return parts.join(" · ");
  }

  emptyContext() {
    if (this.lastError) {
      return `Keine Readiness-Daten im Cache (${this.lastError}).`;
    }
    return "Keine Readiness-Daten im Cache.";
  }
}

function setText(element, text) {
  if (element.textContent !== text) {
    element.textContent = text;
  }
}
