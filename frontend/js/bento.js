/**
 * Bento-Grid-Engine (Spezifikation E.6 / I.1).
 *
 * Reine Funktionen kapseln das Raster-Format (12 Spalten, x/y/w/h); die Klasse
 * `BentoGrid` rendert Instanzen in ein Grid-Element und macht sie per Touch
 * verschieb-/größerbar — ohne jede Hover-Abhängigkeit:
 *   - Verschieben: Long-Press auf die Kachelkopfzeile, dann ziehen.
 *   - Vergrößern: sichtbarer 44-px-Griff in der Kopfzeile.
 * Nach dem Loslassen wird die neue Position über `persist` gespeichert.
 */

export const LONG_PRESS_MS = 600;
export const MOVE_CANCEL_PX = 10;
/** Höchstdauer eines Fokus-Taps; darüber bleibt es ein Langdruck (F). */
export const TAP_MAX_MS = 250;

/** Rasterzelle -> CSS-Grid-Platzierung (0-basiert, `grid-*-start` 1-basiert). */
export function gridPlacement({ x, y, w, h }) {
  return {
    gridColumn: `${x + 1} / span ${w}`,
    gridRow: `${y + 1} / span ${h}`,
  };
}

/** Überlappen sich zwei Rechtecke? (Berühren zählt nicht als Überlappung.) */
export function rectsOverlap(a, b) {
  return !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y);
}

/**
 * Tap-Erkennung für den Fokusmodus (Spezifikation F): kurzer Druck ohne
 * nennenswerte Bewegung. `down`/`up` tragen `{t, x, y}` (Zeitstempel in ms).
 */
export function isTapGesture(down, up, { maxMs = TAP_MAX_MS, maxPx = MOVE_CANCEL_PX } = {}) {
  if (!down || !up) {
    return false;
  }
  const duration = up.t - down.t;
  if (duration < 0 || duration > maxMs) {
    return false;
  }
  return Math.hypot(up.x - down.x, up.y - down.y) < maxPx;
}

/**
 * Bereiche, die keinen Fokus-Tap auslösen: Kachelkopfzeile, ⤡-Griff, per
 * `data-no-focus` gesperrte Elemente und interaktive Kindelemente (z. B. der
 * ⟳-Refresh-Button oder To-Do-Kästchen im Widget-Body, F).
 */
export const FOCUS_EXCLUDE_SELECTOR =
  ".bento-item__header, .bento-item__handle, [data-no-focus], " +
  "button, a[href], input, select, textarea, [role='button']";

export function isFocusExcludedTarget(target) {
  if (!target || typeof target.closest !== "function") {
    return true;
  }
  return Boolean(target.closest(FOCUS_EXCLUDE_SELECTOR));
}

/** Ziel-Zeilenspan der vergrößerten Kachel: `span max(3, aktuelle Höhe)` (F). */
export function enlargeRowSpan(height) {
  const h = Number(height);
  return Math.max(3, Number.isFinite(h) && h > 0 ? Math.ceil(h) : 1);
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

export class BentoGrid {
  /**
   * @param {object} options
   * @param {HTMLElement} options.element     Grid-Container (CSS-Grid).
   * @param {string} options.tabId
   * @param {number} [options.columns=12]
   * @param {number} [options.rowHeight=96]
   * @param {number} [options.gap=8]
   * @param {object} [options.minSizes]       widgetId -> {w,h}
   * @param {Function} [options.persist]      async (instance) => void
   * @param {Function} [options.onFocusTap]   (entry, event) => void bei Fokus-Tap
   * @param {object} [options.log]
   */
  constructor({
    element,
    tabId,
    columns = 12,
    rowHeight = 96,
    gap = 8,
    minSizes = {},
    persist = null,
    onFocusTap = null,
    log = console,
  }) {
    this.element = element;
    this.tabId = tabId;
    this.columns = columns;
    this.rowHeight = rowHeight;
    this.gap = gap;
    this.minSizes = minSizes;
    this.persist = persist;
    this.onFocusTap = onFocusTap;
    this.log = log;
    this.entries = new Map();

    this.element.style.setProperty("--grid-columns", String(columns));
    this.element.style.setProperty("--grid-row-height", `${rowHeight}px`);
    this.element.style.setProperty("--grid-gap", `${gap}px`);
  }

  /** Fügt eine Instanz hinzu und gibt den Kachel-Body (Widget-Container) zurück. */
  add(instance) {
    const node = document.createElement("section");
    node.className = "bento-item";
    node.dataset.instanceId = instance.id;
    node.dataset.widgetId = instance.widget_id;

    const header = document.createElement("header");
    header.className = "bento-item__header";

    const title = document.createElement("h2");
    title.className = "bento-item__title";
    title.textContent = instance.title || instance.widget_id;

    const handle = document.createElement("button");
    handle.type = "button";
    handle.className = "bento-item__handle";
    handle.textContent = "⤡";
    handle.setAttribute("aria-label", `Widget ${instance.widget_id} vergrößern oder verkleinern`);

    header.append(title, handle);

    const body = document.createElement("div");
    body.className = "bento-item__body";

    node.append(header, body);
    this.element.append(node);

    const entry = { instance, node, header, handle, body };
    this._applyRect(entry, instance);
    this.entries.set(instance.id, entry);
    this._bindMove(entry);
    this._bindResize(entry);
    this._bindTap(entry);
    return body;
  }

  remove(instanceId) {
    const entry = this.entries.get(instanceId);
    if (!entry) {
      return;
    }
    entry.node.remove();
    this.entries.delete(instanceId);
  }

  get(instanceId) {
    return this.entries.get(instanceId) || null;
  }

  destroy() {
    for (const entry of this.entries.values()) {
      entry.node.remove();
    }
    this.entries.clear();
  }

  // --- intern ---------------------------------------------------------------

  _applyRect(entry, rect) {
    const placement = gridPlacement(rect);
    entry.node.style.gridColumn = placement.gridColumn;
    entry.node.style.gridRow = placement.gridRow;
  }

  _others(instanceId) {
    return [...this.entries.values()]
      .filter((entry) => entry.instance.id !== instanceId)
      .map((entry) => entry.instance);
  }

  _minSize(instance) {
    const min = this.minSizes[instance.widget_id] || {};
    return { w: Math.max(1, min.w || 1), h: Math.max(1, min.h || 1) };
  }

  _fits(candidate, others) {
    if (candidate.x < 0 || candidate.y < 0 || candidate.w < 1 || candidate.h < 1) {
      return false;
    }
    if (candidate.x + candidate.w > this.columns) {
      return false;
    }
    return !others.some((other) => rectsOverlap(candidate, other));
  }

  _cellSize() {
    const rect = this.element.getBoundingClientRect();
    const width = rect.width || this.columns * (this.rowHeight + this.gap);
    const cellWidth = (width - this.gap * (this.columns - 1)) / this.columns;
    return { cellWidth: Math.max(1, cellWidth), cellHeight: this.rowHeight, rect };
  }

  _bindMove(entry) {
    const { header, node, instance } = entry;
    let pressTimer = null;
    let start = null;
    let dragging = false;

    const reset = () => {
      if (pressTimer) {
        clearTimeout(pressTimer);
        pressTimer = null;
      }
      node.classList.remove("is-dragging");
      node.style.removeProperty("transform");
      node.style.removeProperty("z-index");
    };

    header.addEventListener("pointerdown", (event) => {
      if (event.target.closest(".bento-item__handle")) {
        return;
      }
      start = { x: event.clientX, y: event.clientY };
      pressTimer = setTimeout(() => {
        dragging = true;
        node.classList.add("is-dragging");
        node.style.zIndex = "5";
      }, LONG_PRESS_MS);
    });

    header.addEventListener("pointermove", (event) => {
      if (!start) {
        return;
      }
      const dx = event.clientX - start.x;
      const dy = event.clientY - start.y;
      if (!dragging) {
        if (Math.hypot(dx, dy) > MOVE_CANCEL_PX) {
          reset();
          start = null;
        }
        return;
      }
      event.preventDefault();
      node.style.transform = `translate(${dx}px, ${dy}px)`;
    });

    const finish = (event) => {
      if (!start || !dragging) {
        reset();
        start = null;
        return;
      }
      const dx = event.clientX - start.x;
      const dy = event.clientY - start.y;
      const { cellWidth, cellHeight } = this._cellSize();
      const stepX = cellWidth + this.gap;
      const stepY = cellHeight + this.gap;
      const target = {
        ...instance,
        x: clamp(instance.x + Math.round(dx / stepX), 0, this.columns - instance.w),
        y: Math.max(0, instance.y + Math.round(dy / stepY)),
      };
      reset();
      start = null;
      const committed = this._commit(entry, target);
      if (!committed) {
        this._applyRect(entry, instance);
      }
    };

    header.addEventListener("pointerup", finish);
    header.addEventListener("pointercancel", finish);
  }

  _bindResize(entry) {
    const { handle, instance } = entry;
    let context = null;

    const finish = (event) => {
      if (!context) {
        return;
      }
      const dx = event.clientX - context.start.x;
      const dy = event.clientY - context.start.y;
      const { cellWidth, cellHeight } = this._cellSize();
      const stepX = cellWidth + this.gap;
      const stepY = cellHeight + this.gap;
      const min = this._minSize(instance);
      const target = {
        ...instance,
        w: clamp(instance.w + Math.round(dx / stepX), min.w, this.columns - instance.x),
        h: Math.max(min.h, instance.h + Math.round(dy / stepY)),
      };
      context = null;
      const committed = this._commit(entry, target);
      if (!committed) {
        this._applyRect(entry, instance);
      }
    };

    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();
      context = { start: { x: event.clientX, y: event.clientY } };
      handle.setPointerCapture?.(event.pointerId);
    });
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
  }

  /**
   * Fokus-Tap (Spezifikation F): `pointerdown -> pointerup` auf dem Kachel-Body
   * mit Dauer <= `TAP_MAX_MS` und Bewegung < `MOVE_CANCEL_PX`. Kopfzeile, Griff,
   * `data-no-focus` und interaktive Kindelemente sind ausgenommen.
   */
  _bindTap(entry) {
    if (typeof this.onFocusTap !== "function") {
      return;
    }
    const { node } = entry;
    let start = null;

    node.addEventListener("pointerdown", (event) => {
      start = isFocusExcludedTarget(event.target)
        ? null
        : { t: event.timeStamp, x: event.clientX, y: event.clientY };
    });
    node.addEventListener("pointerup", (event) => {
      const down = start;
      start = null;
      if (!down || isFocusExcludedTarget(event.target)) {
        return;
      }
      const up = { t: event.timeStamp, x: event.clientX, y: event.clientY };
      if (isTapGesture(down, up)) {
        this.onFocusTap(entry, event);
      }
    });
    node.addEventListener("pointercancel", () => {
      start = null;
    });
  }

  /**
   * Fokus-Overlay setzen (Spezifikation F): `is-focus-mode` dimmt alle Kacheln,
   * die Ziel-Kachel bleibt opak und erhält den Fokus-Ring. Bei `enlarge` wächst
   * sie auf `grid-column: 1 / -1` und `grid-row: span max(3, Höhe)`. Das ist ein
   * reines Anzeige-Overlay — `dashboard_layout` wird nicht berührt.
   */
  setFocus(instanceId, mode) {
    const entries = [...this.entries.values()];
    if (!entries.some((entry) => entry.instance.id === instanceId)) {
      return null;
    }
    this.element.classList.add("is-focus-mode");
    for (const entry of entries) {
      const target = entry.instance.id === instanceId;
      entry.node.classList.toggle("is-focused", target);
      entry.node.classList.toggle("bento-item--focus-enlarge", target && mode === "enlarge");
      entry.node.classList.toggle(
        "bento-item--focus-fullscreen",
        target && mode === "fullscreen",
      );
      if (target && mode === "enlarge") {
        entry.node.style.gridColumn = "1 / -1";
        entry.node.style.gridRow = `span ${enlargeRowSpan(entry.instance.h)}`;
      } else {
        this._applyRect(entry, entry.instance);
      }
    }
    return this.entries.get(instanceId);
  }

  /** Fokus-Overlay entfernen und alle Kacheln auf ihre Rasterposition zurücksetzen. */
  clearFocus() {
    this.element.classList.remove("is-focus-mode");
    for (const entry of this.entries.values()) {
      entry.node.classList.remove(
        "is-focused",
        "bento-item--focus-enlarge",
        "bento-item--focus-fullscreen",
      );
      this._applyRect(entry, entry.instance);
    }
  }

  /** Validiert gegen Bounds/Überlappung, übernimmt und persistiert. */
  _commit(entry, target) {
    if (!this._fits(target, this._others(entry.instance.id))) {
      this.log?.info?.(`[bento:${this.tabId}] Ziel belegt oder außerhalb des Rasters`, target);
      return false;
    }
    const next = { ...entry.instance, ...target };
    entry.instance = next;
    this._applyRect(entry, next);
    if (typeof this.persist === "function") {
      Promise.resolve(
        this.persist(next, { x: next.x, y: next.y, w: next.w, h: next.h }),
      )
        .then((saved) => this._rekey(entry, saved))
        .catch((error) => {
          this.log?.warn?.(`[bento:${this.tabId}] Speichern fehlgeschlagen`, error);
        });
    }
    return true;
  }

  /**
   * Programmatisches Platzieren (auch für Tests/Keyboard): validiert wie ein
   * Drag und persistiert. `id` kann sich ändern, wenn erst eine Layout-Zeile
   * angelegt werden musste (Standardplatzierung ohne Backend-Seed).
   */
  applyLayout(instanceId, rect) {
    const entry = this.entries.get(instanceId);
    if (!entry) {
      return false;
    }
    return this._commit(entry, { ...entry.instance, ...rect });
  }

  /** Nach dem ersten Persistieren kann das Backend eine neue Instanz-ID liefern. */
  _rekey(entry, saved) {
    if (!saved || !saved.id || saved.id === entry.instance.id) {
      return;
    }
    this.entries.delete(entry.instance.id);
    entry.instance = {
      ...entry.instance,
      id: saved.id,
      x: saved.x ?? entry.instance.x,
      y: saved.y ?? entry.instance.y,
      w: saved.w ?? entry.instance.w,
      h: saved.h ?? entry.instance.h,
    };
    entry.node.dataset.instanceId = saved.id;
    this.entries.set(saved.id, entry);
  }
}
