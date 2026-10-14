/**
 * Mealie-Wochenplan — Widget (Widget-Plugin-Vertrag E.3, spec-phase2 D/WP-P2-3).
 *
 * Zeigt `GET /api/mealie/mealplan?days=N` (Pull-Adapter nach D12): die Anzahl
 * geplanter Mahlzeiten im Zeitraum als Wert, die Mahlzeiten je Tag als Liste
 * (Mahlzeitentyp, Rezept, Portionen) als Kontext sowie ein konfigurierbares
 * Mindestziel (`config.min_meals`). Jede Kachel traegt damit Label, Wert,
 * Kontext und Ziel (Dashboard-Regel: in fuenf Sekunden lesbar).
 *
 * Netzwerk ausschliesslich ueber das Plugin-API `api` (E.5) — kein direktes
 * `fetch` im Widget.
 *
 * Fehlervertrag (E.3): `{"error_code":"MEALIE_UNAVAILABLE"}` oder ein
 * Netzfehler fuehren NICHT zu einer Exception; das Widget zeigt den letzten
 * bekannten Wert (als Cache/Ausfall gekennzeichnet) oder einen leeren Zustand.
 * Eine leere `meals`-Liste ist kein Fehler (leerer Zustand).
 */

export const DEFAULT_DAYS = 7;
export const DEFAULT_MIN_MEALS = 7;
export const DEFAULT_LIMIT = 12;
const MAX_DAYS = 7;
const MAX_LIMIT = 50;
const SOURCE_LABEL = "Mealie";

/** Aeltere Werte gelten als Cache/veraltet (Backend-TTL ist 600 s, D/WP-P2-3). */
export const STALE_AFTER_MS = 15 * 60 * 1000;

/** Mahlzeitentyp -> deutsche Beschriftung (unbekannte Typen bleiben sichtbar). */
export const MEAL_TYPE_LABELS = {
  breakfast: "Frühstück",
  lunch: "Mittagessen",
  dinner: "Abendessen",
  side: "Beilage",
  snack: "Snack",
  dessert: "Nachtisch",
  drink: "Getränk",
  meal: "Mahlzeit",
};

/** Glyphe je Typ — Form traegt Bedeutung, nie nur Farbe (I.2). */
export const MEAL_TYPE_GLYPHS = {
  breakfast: "🌅",
  lunch: "🍽",
  dinner: "🌙",
  side: "🥗",
  snack: "🍎",
  dessert: "🍰",
  drink: "🥤",
  meal: "🍴",
};

const MEAL_TYPE_ORDER = [
  "breakfast",
  "lunch",
  "dinner",
  "side",
  "snack",
  "dessert",
  "drink",
  "meal",
];

/** Ziel-Bewertung: unter der Mindestzahl (Luecke) oder erreicht. */
const BAND_GLYPHS = { below: "▼", within: "●" };
const BAND_LABELS = { below: "unter Ziel", within: "Ziel erreicht" };

const NO_DECIMAL = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
const DAY_FORMAT = new Intl.DateTimeFormat("de-DE", {
  weekday: "short",
  day: "2-digit",
  month: "2-digit",
});

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function mealTypeLabel(type) {
  if (!type) {
    return MEAL_TYPE_LABELS.meal;
  }
  return MEAL_TYPE_LABELS[type] || type;
}

export function mealTypeGlyph(type) {
  return MEAL_TYPE_GLYPHS[type] || MEAL_TYPE_GLYPHS.meal;
}

export function mealTypeRank(type) {
  const index = MEAL_TYPE_ORDER.indexOf(type);
  return index === -1 ? MEAL_TYPE_ORDER.length : index;
}

/** Zeitraum in Tagen, geklemmt auf 1–7 (Standard 7). */
export function daysWindow(config = {}) {
  const value = Number(config.days);
  if (!Number.isFinite(value)) {
    return DEFAULT_DAYS;
  }
  return Math.max(1, Math.min(MAX_DAYS, Math.round(value)));
}

/** Mindestziel aus der Config, mit Standard und Untergrenze 1. */
export function mealTarget(config = {}) {
  const value = Number(config.min_meals);
  return Number.isFinite(value) && value >= 1 ? Math.round(value) : DEFAULT_MIN_MEALS;
}

/** Anzeige-Limit aus der Config, geklemmt auf 1–50. */
export function listLimit(config = {}) {
  const value = Number(config.limit);
  if (!Number.isFinite(value)) {
    return DEFAULT_LIMIT;
  }
  return Math.max(1, Math.min(MAX_LIMIT, Math.round(value)));
}

/** "2 Portionen" / "1 Portion"; fehlende Angabe wird zu 1. */
export function formatServings(servings) {
  const value = Number.isFinite(servings) && servings >= 1 ? Math.round(servings) : 1;
  return `${NO_DECIMAL.format(value)} ${value === 1 ? "Portion" : "Portionen"}`;
}

/** Relative Altersangabe ("Stand vor 3 min"); `null` bei ungueltigem Wert. */
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

function parseIsoDate(iso) {
  if (typeof iso !== "string" || !ISO_DATE.test(iso)) {
    return null;
  }
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "Mi 14.10." aus einem ISO-Datum; ungueltige Werte bleiben als Rohtext. */
export function formatDay(iso) {
  const date = parseIsoDate(iso);
  return date ? DAY_FORMAT.format(date) : String(iso || "");
}

export function normalizeMeal(raw) {
  const recipeId = typeof raw.recipe_id === "string" ? raw.recipe_id : "";
  const recipeName = typeof raw.recipe_name === "string" ? raw.recipe_name : "";
  return {
    date: typeof raw.date === "string" ? raw.date : "",
    mealType: typeof raw.meal_type === "string" ? raw.meal_type : "meal",
    recipeId,
    recipeName,
    servings: Number.isFinite(raw.servings) ? raw.servings : 1,
  };
}

/**
 * Normalisiert `GET /api/mealie/mealplan`. `error_code` -> nicht erholt
 * (Fehlerobjekt); sonst `meals` (leer ist kein Fehler).
 */
export function readMealplan(payload) {
  if (!payload || typeof payload !== "object" || payload.error_code) {
    return { meals: [], recovered: false, errorCode: (payload && payload.error_code) || null };
  }
  const list = Array.isArray(payload.meals) ? payload.meals : [];
  const meals = list
    .filter((entry) => entry && typeof entry === "object")
    .map(normalizeMeal);
  return {
    meals,
    recovered: true,
    fetchedAt: typeof payload.fetched_at === "string" ? payload.fetched_at : null,
  };
}

/** Mahlzeiten nach Tag gruppieren, je Tag nach Mahlzeitentyp sortiert. */
export function groupByDate(meals) {
  const sorted = [...meals].sort((a, b) => {
    if (a.date !== b.date) {
      return a.date < b.date ? -1 : 1;
    }
    if (mealTypeRank(a.mealType) !== mealTypeRank(b.mealType)) {
      return mealTypeRank(a.mealType) - mealTypeRank(b.mealType);
    }
    return a.recipeName.localeCompare(b.recipeName, "de");
  });

  const groups = [];
  for (const meal of sorted) {
    const last = groups[groups.length - 1];
    if (last && last.date === meal.date) {
      last.items.push(meal);
    } else {
      groups.push({ date: meal.date, items: [meal] });
    }
  }
  return groups;
}

/**
 * "below" | "within" — die Ziel-Linie ist eine Untergrenze (mindestens N
 * geplante Mahlzeiten), darum gibt es kein "above": Mehr Planung ist keine
 * Abweichung. Immer als Glyphe UND Text ausgegeben (I.2, graustufenfest).
 */
export function classifyCount(count, target) {
  return count < target ? "below" : "within";
}

export default class MealieMealplanWidget {
  constructor(container, config, api) {
    this.container = container;
    this.api = api;
    this.config = config || {};
    this.last = null; // { meals, fetchedAt }
    this.degraded = false;
    this.lastError = null;
    this.root = null;
  }

  /** Baut das DOM einmalig auf (idempotent, E.3). Kein Netzwerk. */
  async render() {
    const root = document.createElement("div");
    root.className = "mp";
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", "Wochenplan");

    const head = document.createElement("div");
    head.className = "mp-head";
    const label = document.createElement("span");
    label.className = "mp-label";
    label.textContent = "Wochenplan";
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "mp-refresh";
    refresh.setAttribute("aria-label", "Wochenplan aktualisieren");
    refresh.textContent = "⟳";
    refresh.addEventListener("click", () => this.refresh());
    head.append(label, refresh);

    this.valueEl = document.createElement("p");
    this.valueEl.className = "mp-value";
    this.valueEl.setAttribute("role", "status");

    this.subEl = document.createElement("p");
    this.subEl.className = "mp-sub";

    this.listEl = document.createElement("ul");
    this.listEl.className = "mp-list";

    this.contextEl = document.createElement("p");
    this.contextEl.className = "mp-context";

    this.targetGlyphEl = document.createElement("span");
    this.targetGlyphEl.className = "mp-target__glyph";
    this.targetGlyphEl.setAttribute("aria-hidden", "true");
    this.targetTextEl = document.createElement("span");
    this.targetTextEl.className = "mp-target__text";
    this.targetEl = document.createElement("p");
    this.targetEl.className = "mp-target";
    this.targetEl.append(this.targetGlyphEl, this.targetTextEl);

    root.append(head, this.valueEl, this.subEl, this.listEl, this.contextEl, this.targetEl);
    this.container.replaceChildren(root);
    this.root = root;
    this.renderValue();
  }

  /** Laedt `/api/mealie/mealplan` und aktualisiert nur die Inhalte (I.4). */
  async refresh() {
    let payload = null;
    let fetchFailed = false;
    try {
      payload = await this.api.fetchJson(
        `/api/mealie/mealplan?days=${daysWindow(this.config)}`,
      );
    } catch (error) {
      fetchFailed = true;
      this.lastError = (error && error.code) || "NETWORK";
    }

    if (fetchFailed) {
      this.degraded = true; // letzten bekannten Wert behalten
    } else {
      const result = readMealplan(payload);
      if (result.recovered) {
        this.degraded = false;
        this.lastError = null;
        // Leerer Plan ist kein Fehler: leerer Zustand statt Wert 0.
        this.last =
          result.meals.length === 0
            ? null
            : { meals: result.meals, fetchedAt: result.fetchedAt };
      } else {
        this.degraded = true;
        this.lastError = result.errorCode || "MEALIE_UNAVAILABLE";
      }
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
    const now = Date.now();
    const last = this.last;
    const mode = this.viewMode(last, now);
    this.root.dataset.mode = mode;

    const days = daysWindow(this.config);
    const target = mealTarget(this.config);

    if (!last) {
      this.root.dataset.band = "unknown";
      setText(this.valueEl, "—");
      setText(
        this.subEl,
        this.degraded
          ? "Daten nicht erreichbar"
          : `Keine Mahlzeiten in den nächsten ${NO_DECIMAL.format(days)} Tagen`,
      );
      this.renderList([]);
      setText(this.contextEl, this.emptyContext());
      setText(this.targetGlyphEl, "○");
      setText(this.targetTextEl, `Ziel ≥ ${NO_DECIMAL.format(target)} Mahlzeiten`);
      return;
    }

    const count = last.meals.length;
    setText(this.valueEl, NO_DECIMAL.format(count));
    setText(
      this.subEl,
      `geplante Mahlzeit${count === 1 ? "" : "en"} in den nächsten ${NO_DECIMAL.format(days)} Tagen`,
    );
    this.renderList(groupByDate(last.meals));
    setText(this.contextEl, this.contextText(last, mode, now));

    const band = classifyCount(count, target);
    this.root.dataset.band = band;
    setText(this.targetGlyphEl, BAND_GLYPHS[band]);
    setText(
      this.targetTextEl,
      `Ziel ≥ ${NO_DECIMAL.format(target)} Mahlzeiten · ${BAND_LABELS[band]} (${NO_DECIMAL.format(
        count,
      )}/${NO_DECIMAL.format(target)})`,
    );
  }

  /** "data" (frisch) | "stale" (Cache/Ausfall) | "empty" (kein Wert). */
  viewMode(last, now) {
    if (!last) {
      return "empty";
    }
    if (this.degraded) {
      return "stale";
    }
    if (last.fetchedAt === null) {
      return "stale";
    }
    const age = now - Date.parse(last.fetchedAt);
    return Number.isNaN(age) || age > STALE_AFTER_MS ? "stale" : "data";
  }

  renderList(groups) {
    this.listEl.replaceChildren();
    if (groups.length === 0) {
      const empty = document.createElement("li");
      empty.className = "mp-empty";
      empty.textContent = this.degraded
        ? "Daten nicht erreichbar."
        : "Keine Mahlzeiten geplant.";
      this.listEl.append(empty);
      return;
    }

    const limit = listLimit(this.config);
    let shown = 0;
    for (const group of groups) {
      if (shown >= limit) {
        break;
      }
      const day = document.createElement("li");
      day.className = "mp-day";

      const dayHead = document.createElement("p");
      dayHead.className = "mp-day__head";
      const time = document.createElement("time");
      time.className = "mp-day__date";
      time.dateTime = group.date;
      time.textContent = formatDay(group.date);
      const dayCount = document.createElement("span");
      dayCount.className = "mp-day__count";
      dayCount.textContent = `${NO_DECIMAL.format(group.items.length)}`;
      dayHead.append(time, dayCount);

      const meals = document.createElement("ul");
      meals.className = "mp-day__meals";
      for (const meal of group.items) {
        if (shown >= limit) {
          break;
        }
        meals.append(this.renderMeal(meal));
        shown += 1;
      }

      day.append(dayHead, meals);
      this.listEl.append(day);
    }
  }

  renderMeal(meal) {
    const item = document.createElement("li");
    item.className = "mp-meal";

    const glyph = document.createElement("span");
    glyph.className = "mp-meal__glyph";
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = mealTypeGlyph(meal.mealType);

    const main = document.createElement("span");
    main.className = "mp-meal__main";

    const title = document.createElement("span");
    title.className = "mp-meal__title";
    title.textContent = meal.recipeName || mealTypeLabel(meal.mealType);

    const meta = document.createElement("span");
    meta.className = "mp-meal__meta";
    meta.textContent = `${mealTypeLabel(meal.mealType)} · ${formatServings(meal.servings)}`;

    main.append(title, meta);
    item.append(glyph, main);
    return item;
  }

  contextText(last, mode, now) {
    const parts = [];
    const age = relativeAge(last.fetchedAt, now);
    parts.push(age ? `Stand ${age}` : "Stand unbekannt");
    parts.push(SOURCE_LABEL);
    if (mode === "stale") {
      parts.push(this.degraded ? "Quelle nicht erreichbar" : "Cache veraltet");
    }
    return parts.join(" · ");
  }

  emptyContext() {
    if (this.lastError === "MEALIE_UNAVAILABLE") {
      return "Quelle nicht erreichbar · kein Cache-Wert vorhanden.";
    }
    if (this.lastError) {
      return `Kein Wert verfügbar (${this.lastError}).`;
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
