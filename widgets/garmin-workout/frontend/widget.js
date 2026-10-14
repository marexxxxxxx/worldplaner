/**
 * Garmin-Workouts — Widget (Widget-Plugin-Vertrag E.3, Spezifikation D.5/G.5).
 *
 * Zeigt `GET /api/garmin/workouts` (Feld-Schema G.5 `kind=workout`): die Anzahl
 * der Aktivitäten der letzten sieben Tage als Wert, die letzten Aktivitäten als
 * Liste (Datum, Typ, Dauer, Distanz, Kalorien, Puls) als Kontext sowie ein
 * konfigurierbares Wochenziel (`config.weekly_target`).
 *
 * WP-P2-7: Der Wochenzähler darf nicht am Anzeige-`limit` hängen. Das Backend
 * liefert höchstens `limit` Einträge (D.5, hart bei 50 geklemmt), deshalb ruft
 * der Zähler mit `COUNT_LIMIT` (Backend-Maximum) ab und erst die Liste wird auf
 * `config.limit` gekürzt. Jenseits des Maximums ist die Zahl eine Untergrenze
 * (`weeklySummary(...).capped`), keine geratene Zahl.
 *
 * Netzwerk ausschließlich über das Plugin-API `api` (E.5) — kein direkter
 * `fetch`. Fehlervertrag (E.3): leerer Cache (`workouts:[]`) oder Netzfehler
 * führen NICHT zu einer Exception; das Widget zeigt den leeren Zustand bzw. den
 * letzten bekannten Wert.
 */

export const DEFAULT_WEEKLY_TARGET = 3;
export const DEFAULT_LIMIT = 10;
const MAX_LIMIT = 50;
/**
 * Abruf-Limit des Wochenzählers (= Backend-Maximum aus D.5). Damit zeigt die
 * Kachel auch bei mehr als `config.limit` Aktivitäten die tatsächliche
 * Wochenzahl statt des abgeschnittenen `limit`.
 */
export const COUNT_LIMIT = MAX_LIMIT;
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const SOURCE_LABEL = "Garmin";

const NO_DECIMAL = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
const DISTANCE = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
const DATE = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" });
const TIME = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });

/** Aktivitätstyp -> deutsche Beschriftung (unbekannte Typen bleiben sichtbar). */
const ACTIVITY_LABELS = {
  running: "Laufen",
  cycling: "Radfahren",
  swimming: "Schwimmen",
  walking: "Gehen",
  hiking: "Wandern",
  strength_training: "Krafttraining",
  indoor_cardio: "Indoor-Cardio",
  cardio: "Cardio",
  yoga: "Yoga",
  elliptical: "Crosstrainer",
  indoor_cycling: "Indoor-Rad",
  breathwork: "Atemübung",
  other: "Sonstiges",
};

/** Glyphe je Typ — Form trägt Bedeutung, nie nur Farbe (I.2). */
const ACTIVITY_GLYPHS = {
  running: "🏃",
  cycling: "🚴",
  swimming: "🏊",
  walking: "🚶",
  hiking: "🥾",
  strength_training: "🏋",
  indoor_cardio: "🤸",
  cardio: "❤",
  yoga: "🧘",
  elliptical: "🚴",
  indoor_cycling: "🚴",
  breathwork: "🌬",
  other: "•",
};

/** Divergierend um das Wochenziel — immer als Glyphe UND Text (I.2). */
const BAND_GLYPHS = { below: "▼", within: "●", above: "▲" };
const BAND_LABELS = { below: "unter Ziel", within: "Ziel erreicht", above: "über Ziel" };

export function activityLabel(type) {
  if (!type) {
    return "Aktivität";
  }
  return ACTIVITY_LABELS[type] || type;
}

export function activityGlyph(type) {
  return ACTIVITY_GLYPHS[type] || ACTIVITY_GLYPHS.other;
}

/** Wochenziel aus der Config, mit Standard und Untergrenze. */
export function weeklyTarget(config = {}) {
  const value = Number(config.weekly_target);
  return Number.isFinite(value) && value >= 1 ? Math.round(value) : DEFAULT_WEEKLY_TARGET;
}

/** Abruf-Limit aus der Config, geklemmt auf 1–50 (D.5). */
export function listLimit(config = {}) {
  const value = Number(config.limit);
  if (!Number.isFinite(value)) {
    return DEFAULT_LIMIT;
  }
  return Math.max(1, Math.min(MAX_LIMIT, Math.round(value)));
}

/** "40 min" / "1 h 05 min"; ungültige Werte werden zu "—". */
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

/** "5,21 km" ab 1000 m, sonst "850 m". */
export function formatDistance(meters) {
  if (!Number.isFinite(meters) || meters < 0) {
    return null;
  }
  if (meters >= 1000) {
    return `${DISTANCE.format(meters / 1000)} km`;
  }
  return `${NO_DECIMAL.format(meters)} m`;
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

export function normalizeWorkout(raw) {
  return {
    date: typeof raw.date === "string" ? raw.date : null,
    type: typeof raw.activity_type === "string" ? raw.activity_type : null,
    name: typeof raw.activity_name === "string" ? raw.activity_name : null,
    startTime: typeof raw.start_time === "string" ? raw.start_time : null,
    durationS: Number.isFinite(raw.duration_s) ? raw.duration_s : null,
    distanceM: Number.isFinite(raw.distance_m) ? raw.distance_m : null,
    calories: Number.isFinite(raw.calories) ? raw.calories : null,
    avgHr: Number.isFinite(raw.avg_hr) ? raw.avg_hr : null,
    maxHr: Number.isFinite(raw.max_hr) ? raw.max_hr : null,
  };
}

/** Normalisiert `GET /api/garmin/workouts`; leere Liste ist kein Fehler. */
export function readWorkouts(payload) {
  if (!payload || typeof payload !== "object" || payload.error_code) {
    return { items: [], stale: true, recovered: false };
  }
  const list = Array.isArray(payload.workouts) ? payload.workouts : [];
  const items = list
    .filter((entry) => entry && typeof entry === "object")
    .map(normalizeWorkout);
  return { items, stale: payload.stale === true, recovered: true };
}

/** Liegt die Aktivität innerhalb der letzten `days` Tage? */
export function isWithinDays(item, days, now = Date.now()) {
  const iso = item.startTime || item.date;
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) {
    return false;
  }
  return now - ms <= days * 24 * 60 * 60 * 1000;
}

export function weeklyCount(items, now = Date.now()) {
  return items.filter((item) => isWithinDays(item, 7, now)).length;
}

/**
 * Wochenauswertung der geladenen Einträge.
 *
 * `capped` ist wahr, wenn die Liste das Abruf-Limit `COUNT_LIMIT` ausschöpft
 * UND alle Einträge in die letzten sieben Tage fallen — dann kann das Backend
 * weitere Aktivitäten abgeschnitten haben und `count` ist eine Untergrenze
 * statt eines exakten Werts (keine erfundene Zahl).
 */
export function weeklySummary(items, now = Date.now()) {
  const count = weeklyCount(items, now);
  const capped = items.length >= COUNT_LIMIT && count >= COUNT_LIMIT;
  return { count, capped };
}

function formatMoment(iso) {
  if (typeof iso !== "string" || iso === "") {
    return null;
  }
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : `${DATE.format(ms)} · ${TIME.format(ms)}`;
}

export default class GarminWorkoutWidget {
  constructor(container, config, api) {
    this.container = container;
    this.api = api;
    this.config = config || {};
    this.last = null; // { items, stale }
    this.degraded = false;
    this.lastError = null;
    this.root = null;
  }

  async render() {
    const root = document.createElement("div");
    root.className = "gw";
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", "Garmin Workouts");

    const head = document.createElement("div");
    head.className = "gw-head";
    const label = document.createElement("span");
    label.className = "gw-label";
    label.textContent = "Workouts";
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "gw-refresh";
    refresh.setAttribute("aria-label", "Workouts aktualisieren");
    refresh.textContent = "⟳";
    refresh.addEventListener("click", () => this.refresh());
    head.append(label, refresh);

    this.valueEl = document.createElement("p");
    this.valueEl.className = "gw-value";
    this.valueEl.setAttribute("role", "status");

    this.subEl = document.createElement("p");
    this.subEl.className = "gw-sub";

    this.listEl = document.createElement("ul");
    this.listEl.className = "gw-list";

    this.contextEl = document.createElement("p");
    this.contextEl.className = "gw-context";

    this.targetGlyphEl = document.createElement("span");
    this.targetGlyphEl.className = "gw-target__glyph";
    this.targetGlyphEl.setAttribute("aria-hidden", "true");
    this.targetTextEl = document.createElement("span");
    this.targetTextEl.className = "gw-target__text";
    this.targetEl = document.createElement("p");
    this.targetEl.className = "gw-target";
    this.targetEl.append(this.targetGlyphEl, this.targetTextEl);

    root.append(head, this.valueEl, this.subEl, this.listEl, this.contextEl, this.targetEl);
    this.container.replaceChildren(root);
    this.root = root;
    this.renderValue();
  }

  async refresh() {
    let payload = null;
    let fetchFailed = false;
    try {
      payload = await this.api.fetchJson(`/api/garmin/workouts?limit=${COUNT_LIMIT}`);
    } catch (error) {
      fetchFailed = true;
      this.lastError = (error && error.code) || "NETWORK";
    }

    if (fetchFailed) {
      this.degraded = true;
    } else {
      const result = readWorkouts(payload);
      this.lastError = null;
      this.degraded = !result.recovered;
      // Leerer Cache ist kein Fehler (E.3): keine Einträge -> leerer Zustand
      // ("—", ``data-mode="empty"``) statt ``stale`` mit Wert ``0``. Eine
      // nicht-leere Liste bleibt auch bei ``stale:true`` sichtbar; ein
      // ungültiger Abruf (``recovered:false``) behält den Fehler-/stale-Pfad.
      this.last =
        result.recovered && result.items.length === 0
          ? null
          : { items: result.items, stale: result.stale };
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

    const target = weeklyTarget(this.config);

    if (!last) {
      this.root.dataset.band = "unknown";
      setText(this.valueEl, "—");
      setText(this.subEl, this.degraded ? "Daten nicht erreichbar" : "Keine Workouts");
      this.renderList([]);
      setText(this.contextEl, this.emptyContext());
      setText(this.targetGlyphEl, "○");
      setText(this.targetTextEl, `Ziel ${NO_DECIMAL.format(target)} pro Woche`);
      return;
    }

    const summary = weeklySummary(last.items);
    const count = summary.count;
    const countText = formatWeeklyCount(summary);
    setText(this.valueEl, countText);
    setText(
      this.subEl,
      `Workout${count === 1 ? "" : "s"} in den letzten 7 Tagen`,
    );
    // Liste auf das konfigurierte Anzeige-`limit` kürzen; der Zähler oben nutzt
    // weiterhin alle geladenen Einträge (WP-P2-7).
    this.renderList(last.items.slice(0, listLimit(this.config)));
    setText(this.contextEl, this.contextText(last, mode));

    const ratio = count / target;
    const band = classifyRatio(ratio);
    this.root.dataset.band = band;
    setText(this.targetGlyphEl, BAND_GLYPHS[band] || "○");
    setText(
      this.targetTextEl,
      `Ziel ${NO_DECIMAL.format(target)} pro Woche · ${BAND_LABELS[band] || "—"} (${countText}/${NO_DECIMAL.format(
        target,
      )})`,
    );
  }

  /** "data" (frisch) | "stale" (Cache/Ausfall) | "empty" (kein Wert). */
  viewMode(last) {
    if (!last) {
      return "empty";
    }
    return this.degraded || last.stale ? "stale" : "data";
  }

  renderList(items) {
    this.listEl.replaceChildren();
    if (items.length === 0) {
      const empty = document.createElement("li");
      empty.className = "gw-empty";
      empty.textContent = this.degraded
        ? "Daten nicht erreichbar."
        : "Keine Workouts im Cache.";
      this.listEl.append(empty);
      return;
    }

    for (const workout of items) {
      const item = document.createElement("li");
      item.className = "gw-item";

      const glyph = document.createElement("span");
      glyph.className = "gw-item__glyph";
      glyph.setAttribute("aria-hidden", "true");
      glyph.textContent = activityGlyph(workout.type);

      const main = document.createElement("span");
      main.className = "gw-item__main";

      const title = document.createElement("span");
      title.className = "gw-item__title";
      title.textContent = workout.name || activityLabel(workout.type);

      const meta = document.createElement("span");
      meta.className = "gw-item__meta";
      meta.textContent = this.itemMeta(workout);

      main.append(title, meta);
      item.append(glyph, main);
      this.listEl.append(item);
    }
  }

  itemMeta(workout) {
    const parts = [];
    const moment = formatMoment(workout.startTime) || workout.date;
    if (moment) {
      parts.push(moment);
    }
    parts.push(activityLabel(workout.type));
    if (workout.durationS !== null) {
      parts.push(formatDuration(workout.durationS));
    }
    const distance = formatDistance(workout.distanceM);
    if (distance) {
      parts.push(distance);
    }
    if (workout.calories !== null) {
      parts.push(`${NO_DECIMAL.format(workout.calories)} kcal`);
    }
    if (workout.avgHr !== null) {
      parts.push(
        workout.maxHr !== null
          ? `Puls Ø ${NO_DECIMAL.format(workout.avgHr)}/${NO_DECIMAL.format(workout.maxHr)}`
          : `Puls Ø ${NO_DECIMAL.format(workout.avgHr)}`,
      );
    }
    return parts.join(" · ");
  }

  contextText(last, mode) {
    const shown = Math.min(last.items.length, listLimit(this.config));
    const parts = [`Letzte ${NO_DECIMAL.format(shown)}`];
    parts.push(SOURCE_LABEL);
    parts.push(mode === "stale" ? "Cache veraltet" : "Cache aktuell");
    return parts.join(" · ");
  }

  emptyContext() {
    if (this.lastError) {
      return `Keine Workouts im Cache (${this.lastError}).`;
    }
    return "Keine Workouts im Cache.";
  }
}

function setText(element, text) {
  if (element.textContent !== text) {
    element.textContent = text;
  }
}

/** „15" bzw. „≥50", wenn das Abruf-Limit die Wochenzahl abschneidet. */
function formatWeeklyCount({ count, capped }) {
  return capped ? `≥${NO_DECIMAL.format(COUNT_LIMIT)}` : NO_DECIMAL.format(count);
}
