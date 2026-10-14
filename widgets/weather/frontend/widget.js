/**
 * Wetter — Widget (Widget-Plugin-Vertrag E.3).
 *
 * Zeigt den Momentwert aus `GET /api/weather/current` (Spezifikation D.6):
 * Temperatur in °C und die Condition, dazu Stand, Luftfeuchte und Wind als
 * Kontext sowie den Wohlfühlbereich als Ziel. Jede Kachel trägt damit
 * Label, Wert, Kontext und Ziel (Dashboard-Regel: in fünf Sekunden lesbar).
 *
 * Netzwerk ausschließlich über das Plugin-API `api` (E.5) — kein direkter
 * `fetch` im Widget.
 *
 * Fehlervertrag (E.3): Fällt der Provider aus (`{"error_code":
 * "WEATHER_UNAVAILABLE"}`) oder fehlt die Temperatur, wirft `refresh()`
 * bewusst NICHT, sondern zeigt den zuletzt bekannten Wert (als Cache
 * gekennzeichnet) oder einen leeren Zustand. So bleibt der Host stabil und
 * der Poll läuft weiter.
 */

/** WMO-/Service-Condition -> lesbare Beschriftung (deutsch). */
const CONDITION_LABELS = {
  clear: "klar",
  mainly_clear: "überwiegend klar",
  partly_cloudy: "teils bewölkt",
  overcast: "bedeckt",
  fog: "Nebel",
  depositing_rime_fog: "Reifnebel",
  light_drizzle: "leichter Nieselregen",
  drizzle: "Nieselregen",
  dense_drizzle: "starker Nieselregen",
  light_freezing_drizzle: "gefr. Nieselregen",
  dense_freezing_drizzle: "starker gefr. Nieselregen",
  slight_rain: "leichter Regen",
  rain: "Regen",
  heavy_rain: "starker Regen",
  light_freezing_rain: "gefrierender Regen",
  heavy_freezing_rain: "starker gefr. Regen",
  slight_snow: "leichter Schneefall",
  snow: "Schneefall",
  heavy_snow: "starker Schneefall",
  snow_grains: "Schneegriesel",
  slight_rain_showers: "leichte Schauer",
  rain_showers: "Schauer",
  violent_rain_showers: "heftige Schauer",
  slight_snow_showers: "leichte Schneeschauer",
  heavy_snow_showers: "starke Schneeschauer",
  thunderstorm: "Gewitter",
  thunderstorm_slight_hail: "Gewitter mit Hagel",
  thunderstorm_heavy_hail: "schweres Hagelgewitter",
  unknown: "unbekannt",
};

/** Zustands-Glyphe zusätzlich zur Beschriftung — nie Farbe oder Form allein (I.2). */
const CONDITION_GLYPHS = {
  clear: "☀",
  mainly_clear: "🌤",
  partly_cloudy: "⛅",
  overcast: "☁",
  fog: "🌫",
  depositing_rime_fog: "🌫",
  light_drizzle: "🌦",
  drizzle: "🌦",
  dense_drizzle: "🌧",
  light_freezing_drizzle: "🌧",
  dense_freezing_drizzle: "🌧",
  slight_rain: "🌦",
  rain: "🌧",
  heavy_rain: "🌧",
  light_freezing_rain: "🌧",
  heavy_freezing_rain: "🌧",
  slight_snow: "🌨",
  snow: "🌨",
  heavy_snow: "❄",
  snow_grains: "🌨",
  slight_rain_showers: "🌦",
  rain_showers: "🌧",
  violent_rain_showers: "⛈",
  slight_snow_showers: "🌨",
  heavy_snow_showers: "🌨",
  thunderstorm: "⛈",
  thunderstorm_slight_hail: "⛈",
  thunderstorm_heavy_hail: "⛈",
  unknown: "•",
};

/** Divergierend um den Wohlfühlbereich (über/unter Ziel) — mit Text + Glyphe. */
const COMFORT_GLYPHS = { below: "▼", within: "●", above: "▲" };
const COMFORT_LABELS = {
  below: "darunter",
  within: "im Bereich",
  above: "darüber",
};

const SOURCE_LABEL = "Open-Meteo";
const DEFAULT_COMFORT_MIN_C = 18;
const DEFAULT_COMFORT_MAX_C = 26;

/** Ältere Werte gelten als Cache/veraltet (Backend-TTL ist 600 s, D.6). */
export const STALE_AFTER_MS = 15 * 60 * 1000;

/** Takt für die relative Zeitangabe ("Stand vor X min"). */
const CONTEXT_TICK_MS = 30 * 1000;

const oneDecimal = new Intl.NumberFormat("de-DE", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const noDecimal = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

/** Beschriftung der Condition; unbekannte Codes bleiben sichtbar als "unbekannt". */
export function conditionLabel(condition) {
  return CONDITION_LABELS[condition] || CONDITION_LABELS.unknown;
}

export function conditionGlyph(condition) {
  return CONDITION_GLYPHS[condition] || CONDITION_GLYPHS.unknown;
}

/** Wohlfühlbereich aus der Config, mit vertauschten Grenzen korrigiert. */
export function comfortBand(config = {}) {
  const min = Number.isFinite(config.comfort_min_c) ? config.comfort_min_c : DEFAULT_COMFORT_MIN_C;
  const max = Number.isFinite(config.comfort_max_c) ? config.comfort_max_c : DEFAULT_COMFORT_MAX_C;
  return min <= max ? { min, max } : { min: max, max: min };
}

/** "below" | "within" | "above" — Grenzen gehören zum Bereich. */
export function classifyTemperature(temperatureC, band) {
  if (temperatureC < band.min) {
    return "below";
  }
  if (temperatureC > band.max) {
    return "above";
  }
  return "within";
}

/** Relative Altersangabe; `null`, wenn kein gültiger Zeitstempel vorliegt. */
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

/**
 * Normalisiert die Provider-Antwort. `null`, wenn kein verwertbarer
 * Momentwert vorliegt (Fehlerobjekt `error_code` oder fehlendes `data`).
 */
export function readCurrent(payload) {
  if (!payload || typeof payload !== "object" || payload.error_code) {
    return null;
  }
  if (!Number.isFinite(payload.temperature_c)) {
    return null;
  }
  return {
    temperatureC: payload.temperature_c,
    condition: typeof payload.condition === "string" ? payload.condition : "unknown",
    humidity: Number.isFinite(payload.humidity) ? payload.humidity : null,
    windKmh: Number.isFinite(payload.wind_kmh) ? payload.wind_kmh : null,
    fetchedAt: typeof payload.fetched_at === "string" ? payload.fetched_at : null,
  };
}

export function formatTemperature(temperatureC) {
  return `${oneDecimal.format(temperatureC)} °C`;
}

export default class WeatherWidget {
  constructor(container, config, api) {
    this.container = container;
    this.api = api;
    this.config = config || {};
    this.band = comfortBand(this.config);
    this.last = null; // letzter gültiger Momentwert (Cache im Widget)
    this.degraded = false; // letzter Abruf ohne frischen Provider-Wert
    this.lastError = null;
    this.tick = null;
  }

  /** Baut das DOM einmalig auf (idempotent, E.3). Kein Netzwerk. */
  async render() {
    const root = document.createElement("div");
    root.className = "wx";
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", "Wetter");

    const head = document.createElement("div");
    head.className = "wx-head";
    const label = document.createElement("span");
    label.className = "wx-label";
    label.textContent = "Temperatur";
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "wx-refresh";
    refresh.setAttribute("aria-label", "Wetter aktualisieren");
    refresh.textContent = "⟳";
    refresh.addEventListener("click", () => {
      this.refresh();
    });
    head.append(label, refresh);

    this.valueEl = document.createElement("p");
    this.valueEl.className = "wx-value";
    this.valueEl.setAttribute("role", "status");

    this.glyphEl = document.createElement("span");
    this.glyphEl.className = "wx-glyph";
    this.glyphEl.setAttribute("aria-hidden", "true");
    this.conditionTextEl = document.createElement("span");
    this.conditionTextEl.className = "wx-condition__text";
    this.conditionEl = document.createElement("p");
    this.conditionEl.className = "wx-condition";
    this.conditionEl.append(this.glyphEl, this.conditionTextEl);

    this.contextEl = document.createElement("p");
    this.contextEl.className = "wx-context";

    this.detailsEl = document.createElement("p");
    this.detailsEl.className = "wx-details";

    this.comfortGlyphEl = document.createElement("span");
    this.comfortGlyphEl.className = "wx-target__glyph";
    this.comfortGlyphEl.setAttribute("aria-hidden", "true");
    this.comfortTextEl = document.createElement("span");
    this.comfortTextEl.className = "wx-target__text";
    this.targetEl = document.createElement("p");
    this.targetEl.className = "wx-target";
    this.targetEl.append(this.comfortGlyphEl, this.comfortTextEl);

    root.append(
      head,
      this.valueEl,
      this.conditionEl,
      this.contextEl,
      this.detailsEl,
      this.targetEl,
    );
    this.container.replaceChildren(root);
    this.root = root;
    this.renderValue();

    if (this.tick === null) {
      this.tick = setInterval(() => this.renderValue(), CONTEXT_TICK_MS);
    }
  }

  /**
   * Lädt `/api/weather/current` und aktualisiert nur die Inhalte (kein
   * Layout-Sprung, I.4). Wirft nie wegen fehlender Provider-Daten (E.3).
   */
  async refresh() {
    let payload = null;
    let fetchFailed = false;
    try {
      payload = await this.api.fetchJson("/api/weather/current");
    } catch (error) {
      fetchFailed = true;
      this.lastError = (error && error.code) || "NETWORK";
    }

    const current = readCurrent(payload);
    if (current) {
      this.last = current;
      this.degraded = false;
      this.lastError = null;
    } else {
      this.degraded = true;
      if (!fetchFailed) {
        this.lastError = (payload && payload.error_code) || "WEATHER_UNAVAILABLE";
      }
    }
    this.renderValue();
  }

  onConfigChange(newConfig) {
    this.config = { ...this.config, ...(newConfig || {}) };
    this.band = comfortBand(this.config);
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

  // --- Rendering (Domänenlogik rein und testbar gehalten) -------------------

  renderValue() {
    if (!this.root) {
      return;
    }
    const now = Date.now();
    const last = this.last;
    const mode = this.viewMode(last, now);
    this.root.dataset.mode = mode;

    if (!last) {
      this.root.dataset.comfort = "unknown";
      setText(this.valueEl, "—");
      setText(this.glyphEl, "⚠");
      setText(this.conditionTextEl, "Keine Wetterdaten");
      setText(this.contextEl, this.emptyContext());
      setText(this.detailsEl, "");
      this.detailsEl.hidden = true;
      setText(this.comfortGlyphEl, "");
      setText(this.comfortTextEl, `Ziel Wohlfühlbereich ${this.bandText()}`);
      return;
    }

    setText(this.valueEl, formatTemperature(last.temperatureC));
    setText(this.glyphEl, conditionGlyph(last.condition));
    setText(this.conditionTextEl, conditionLabel(last.condition));

    setText(this.contextEl, this.contextText(last, mode, now));

    const details = [];
    if (last.humidity !== null) {
      details.push(`Luftfeuchte ${noDecimal.format(last.humidity)} %`);
    }
    if (last.windKmh !== null) {
      details.push(`Wind ${noDecimal.format(last.windKmh)} km/h`);
    }
    setText(this.detailsEl, details.join(" · "));
    this.detailsEl.hidden = details.length === 0;

    const comfort = classifyTemperature(last.temperatureC, this.band);
    this.root.dataset.comfort = comfort;
    setText(this.comfortGlyphEl, COMFORT_GLYPHS[comfort]);
    setText(
      this.comfortTextEl,
      `Ziel Wohlfühlbereich ${this.bandText()} · ${COMFORT_LABELS[comfort]}`,
    );
  }

  /** "data" (frisch) | "degraded" (Cache/Ausfall) | "empty" (kein Wert). */
  viewMode(last, now) {
    if (!last) {
      return "empty";
    }
    if (this.degraded) {
      return "degraded";
    }
    if (last.fetchedAt === null) {
      return "degraded";
    }
    const age = now - Date.parse(last.fetchedAt);
    return Number.isNaN(age) || age > STALE_AFTER_MS ? "degraded" : "data";
  }

  contextText(last, mode, now) {
    const parts = [];
    const age = relativeAge(last.fetchedAt, now);
    parts.push(age ? `Stand ${age}` : "Stand unbekannt");
    parts.push(SOURCE_LABEL);
    if (mode === "degraded") {
      parts.push(this.degraded ? "Provider nicht erreichbar" : "Cache veraltet");
    }
    return parts.join(" · ");
  }

  emptyContext() {
    if (this.lastError === "WEATHER_UNAVAILABLE") {
      return "Provider nicht erreichbar · kein Cache-Wert vorhanden.";
    }
    if (this.lastError) {
      return `Kein Wert verfügbar (${this.lastError}).`;
    }
    return "Noch keine Daten geladen.";
  }

  bandText() {
    return `${noDecimal.format(this.band.min)}–${noDecimal.format(this.band.max)} °C`;
  }
}

/** Setzt Text nur bei Änderung (verhindert unnötige Screenreader-Ansagen). */
function setText(element, text) {
  if (element.textContent !== text) {
    element.textContent = text;
  }
}
