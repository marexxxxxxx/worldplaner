/**
 * app.js — Bootstrap, Tab-Routing, SSE-Client, Widget-Laden (Spezifikation D, I).
 *
 * Ablauf: `/api/config` laden -> Tabs rendern -> pro Tab Instanzen auflösen
 * (Backend-Layout, sonst eingebaute Standardplatzierung) -> Widget-Runtime
 * laden. To-Dos laufen über SSE (`/api/events`), Cache-Daten über Polling (D6).
 *
 * Fokusmodus (spec-focus F): genau ein aktiver Fokus (highlight|enlarge|
 * fullscreen), ausgelöst per Touch-Tap (`bento.js`) oder SSE `focus.changed`
 * (Agent), jederzeit per Zurück-Schaltfläche/`Escape`/erneutem Tap zurücknehmbar.
 * Der Fokus ist ein reines Anzeige-Overlay — `dashboard_layout` bleibt unberührt.
 */

import { api } from "./api.js";
import { BentoGrid } from "./bento.js";
import { WidgetRuntime } from "./widget-runtime.js";

/** Fokus-REST (spec-focus D.1): PUT/DELETE/GET /api/state/focus. */
function getFocusState() {
  return api.request("/api/state/focus");
}

function putFocusState(payload) {
  return api.request("/api/state/focus", { method: "PUT", body: payload });
}

function deleteFocusState() {
  return api.request("/api/state/focus", { method: "DELETE" });
}

/**
 * Eingebaute Widgets dieses Repos und ihr Ziel-Tab. Der Backend-Registry-Eintrag
 * (`/api/widgets`) und `dashboard_layout` haben Vorrang; diese Liste ist die
 * Standardplatzierung, solange WP-2 die Manifeste noch nicht in die `widgets`-
 * Tabelle registriert (Abschnitt C der Spezifikation) — und sie füllt
 * eingebaute Widgets nach, die auf einer bereits geseedeten Installation noch
 * keine `dashboard_layout`-Zeile haben (siehe `resolveInstances`).
 */

/** Polling-Intervalle der Cache-Widgets (Spezifikation I.4). */
const POLL_WEATHER_MS = 10 * 60 * 1000;
const POLL_GARMIN_MS = 5 * 60 * 1000;
/**
 * Pull-/Cache-Widgets, deren Quelle das Backend in `api_cache` puffert
 * (TTL 600 s): Baikal, Grocy, Mealie. Takt 10 min wie das Wetter-Widget; die
 * Widgets gelten erst nach 15 min ohne frischen Wert als veraltet, der Takt
 * liegt also sicher darunter.
 */
const POLL_CACHE_MS = 10 * 60 * 1000;

export const BUILTIN_WIDGETS = [
  // Wetter und Garmin sind Cache-Daten -> Polling (Wetter 10 min, Garmin 5 min, I.4).
  { id: "weather", tab: "dashboard", pollMs: POLL_WEATHER_MS },
  { id: "garmin-sleep", tab: "dashboard", pollMs: POLL_GARMIN_MS },
  { id: "garmin-readiness", tab: "dashboard", pollMs: POLL_GARMIN_MS },
  { id: "garmin-vitals", tab: "dashboard", pollMs: POLL_GARMIN_MS },
  { id: "garmin-workout", tab: "dashboard", pollMs: POLL_GARMIN_MS },
  // Phase-2-Widgets (WP-P2-1/2/3): api_cache-gepuffert (TTL 600 s) -> 10-min-Takt.
  { id: "baikal-calendar", tab: "dashboard", pollMs: POLL_CACHE_MS },
  { id: "grocy-stock", tab: "dashboard", pollMs: POLL_CACHE_MS },
  { id: "mealie-mealplan", tab: "dashboard", pollMs: POLL_CACHE_MS },
  { id: "hermes-todos", tab: "todos", pollMs: 0 },
];

/**
 * Eingebaute Widgets, die auf `slug` noch keine Layout-Zeile haben und nicht
 * deaktiviert sind. Genau diese Ergänzung macht neu hinzugekommene eingebaute
 * Widgets auf einer bereits geseedeten Installation sichtbar, ohne sie manuell
 * zu platzieren — der Backend-Seed legt Standardzeilen nur bei leerer
 * `dashboard_layout`-Tabelle an (`backend/app/db.py` `seed_builtin_widgets`).
 */
export function pendingBuiltins(builtins, slug, presentIds, registry) {
  return builtins.filter(
    (def) => def.tab === slug && !presentIds.has(def.id) && isWidgetEnabled(registry, def.id),
  );
}

const WIDGETS_TAB = "widgets";
const SSE_EVENTS = ["task.created", "task.updated", "task.deleted", "screen.changed", "focus.changed"];

/** Anzeigenamen der Manifest-Kategorien (Spezifikation E.2). */
const CATEGORY_LABELS = {
  weather: "Wetter",
  health: "Gesundheit",
  productivity: "Produktivität",
  general: "Allgemein",
};

/** Minimaler Pub/Sub für SSE-Events (Widgets abonnieren hierüber, E.5). */
class EventBus {
  constructor() {
    this.listeners = new Map();
  }

  on(event, callback) {
    const set = this.listeners.get(event) ?? new Set();
    set.add(callback);
    this.listeners.set(event, set);
    return () => set.delete(callback);
  }

  emit(event, payload) {
    const set = this.listeners.get(event);
    if (!set) {
      return;
    }
    for (const callback of [...set]) {
      try {
        callback(payload, event);
      } catch (error) {
        console.error(`[events] Handler für ${event} fehlgeschlagen`, error);
      }
    }
  }
}

/** SSE-Verbindung mit exponentiellem Backoff (Basis 1 s, Cap 30 s, D.8). */
function connectEventStream({ bus, onStatus, log = console }) {
  let attempt = 0;
  let source = null;
  let stopped = false;

  const open = () => {
    if (stopped) {
      return;
    }
    source = new EventSource("/api/events");
    source.addEventListener("open", () => {
      attempt = 0;
      onStatus("online");
    });
    for (const event of SSE_EVENTS) {
      source.addEventListener(event, (message) => {
        let payload = null;
        try {
          payload = message.data ? JSON.parse(message.data) : null;
        } catch {
          payload = null;
        }
        bus.emit(event, payload);
      });
    }
    source.addEventListener("error", () => {
      onStatus("offline");
      try {
        source.close();
      } catch {
        /* egal – unten wird neu verbunden. */
      }
      if (stopped) {
        return;
      }
      const delay = Math.min(30000, 1000 * 2 ** attempt);
      attempt += 1;
      log.info(`[sse] Verbindung getrennt – Reconnect in ${delay} ms`);
      setTimeout(open, delay);
    });
  };

  open();
  return {
    close() {
      stopped = true;
      try {
        source?.close();
      } catch {
        /* nichts zu tun. */
      }
    },
  };
}

function connectionLabel(state) {
  if (state === "online") {
    return "live";
  }
  if (state === "offline") {
    return "offline – Neuverbinden…";
  }
  return "verbindet…";
}

function isWidgetEnabled(registry, widgetId) {
  if (!Array.isArray(registry)) {
    return true; // Registry nicht verfügbar -> nichts blockieren.
  }
  const entry = registry.find((widget) => widget.id === widgetId);
  return entry ? entry.is_enabled !== false : true;
}

async function safeGetWidgets() {
  try {
    return await api.getWidgets();
  } catch (error) {
    console.warn("[app] /api/widgets nicht erreichbar", error);
    return null;
  }
}

/** App-Klasse; exportiert, damit Tests `resolveInstances` ohne Browser fahren können. */
export class DashboardApp {
  constructor({ log = console } = {}) {
    this.log = log;
    this.bus = new EventBus();
    this.runtime = new WidgetRuntime({ eventBus: this.bus, log });
    this.manifestCache = new Map();
    this.grid = { columns: 12, row_height_px: 96, gap_px: 8 };
    this.views = new Map();
    this.grids = new Map();
    this.activeSlug = null;
    this.tabButtons = new Map();
    this.widgetRegistry = null;

    // Fokusmodus (spec-focus F): genau ein aktiver Fokus, reines Overlay.
    this.focus = null;
    this.focusAutoResetSeconds = 0;
    this.autoResetTimer = null;
    this.focusBackButton = null;
    this._focusTargetNode = null;
    this._onFocusPointerDown = null;
    this._onKeydown = (event) => {
      if (event.key === "Escape" && this.focus) {
        this.clearFocus();
      }
    };
  }

  async start() {
    const config = await api.getConfig();
    this.grid = config.grid || this.grid;
    const tabs = Array.isArray(config.tabs) ? config.tabs : [];
    const reset = Number(config.focus_auto_reset_seconds);
    this.focusAutoResetSeconds = Number.isFinite(reset) && reset > 0 ? reset : 0;

    this.renderTabs(tabs);
    await Promise.all(tabs.map((tab) => this.buildView(tab)));

    const active = tabs.some((tab) => tab.slug === config.active_tab)
      ? config.active_tab
      : tabs[0]?.slug;
    if (active) {
      this.showTab(active, { persist: false });
    }

    this.bus.on("focus.changed", (payload) => this.onFocusChanged(payload));
    document.addEventListener?.("keydown", this._onKeydown);

    await this.restoreFocus();

    this.eventStream = connectEventStream({
      bus: this.bus,
      onStatus: (state) => this.setConnection(state),
      log: this.log,
    });

    // Debug-/Test-Handle (z. B. für den Fehlervertrag-Nachweis in WP-3).
    window.worldplaner = { app: this, runtime: this.runtime, bus: this.bus };
  }

  /**
   * Persistierten Fokus beim Start anwenden (spec-focus F: „Reload bleibt
   * persistent"). Backend ohne Fokus-State -> still ignorieren, kein Bruch.
   */
  async restoreFocus() {
    try {
      const current = await getFocusState();
      if (current && current.value) {
        this.setFocus({ ...current.value }, { persist: false });
      }
    } catch (error) {
      this.log?.info?.("[focus] /api/state/focus nicht verfügbar", error?.message || error);
    }
  }

  renderTabs(tabs) {
    const bar = document.getElementById("tabbar");
    bar.replaceChildren();
    for (const tab of tabs) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "tab";
      button.textContent = tab.title || tab.slug;
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", "false");
      button.dataset.tab = tab.slug;
      button.addEventListener("click", () => this.showTab(tab.slug));
      bar.append(button);
      this.tabButtons.set(tab.slug, button);
    }
  }

  async buildView(tab) {
    const views = document.getElementById("views");
    const section = document.createElement("section");
    section.className = "view";
    section.dataset.tab = tab.slug;
    section.setAttribute("role", "tabpanel");
    section.hidden = true;
    views.append(section);
    this.views.set(tab.slug, section);

    if (tab.slug === WIDGETS_TAB) {
      await this.buildWidgetAdmin(section);
      return;
    }
    await this.buildBento(section, tab.slug);
  }

  async buildBento(section, slug) {
    const registry = await safeGetWidgets();
    const instances = await this.resolveInstances(slug, registry);

    if (instances.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "Keine Widgets auf diesem Tab.";
      section.append(empty);
      return;
    }

    const gridEl = document.createElement("div");
    gridEl.className = "bento-grid";
    gridEl.dataset.tab = slug;
    section.append(gridEl);

    const minSizes = {};
    for (const instance of instances) {
      const min = instance.manifest.min_size;
      if (min) {
        minSizes[instance.widget_id] = min;
      }
    }

    const bento = new BentoGrid({
      element: gridEl,
      tabId: slug,
      columns: this.grid.columns,
      rowHeight: this.grid.row_height_px,
      gap: this.grid.gap_px,
      minSizes,
      log: this.log,
      persist: (instance, rect) => this.persistPlacement(slug, instance, rect),
      onFocusTap: (entry) => this.handleTileTap(entry),
    });
    this.grids.set(slug, bento);

    for (const instance of instances) {
      const body = bento.add({
        id: instance.id,
        widget_id: instance.widget_id,
        x: instance.x,
        y: instance.y,
        w: instance.w,
        h: instance.h,
        config_override: instance.config_override,
        title: instance.manifest.name,
      });
      await this.runtime.loadWidget({
        instanceId: instance.id,
        widgetId: instance.widget_id,
        container: body,
        manifest: instance.manifest,
        config: { ...instance.config, ...instance.config_override },
        pollMs: instance.pollMs,
      });
    }
  }

  /**
   * Layout aus dem Backend; eingebaute Widgets, die (noch) keine Layout-Zeile
   * haben, werden ergänzt. Der Backend-Seed legt Standardzeilen nur bei leerem
   * `dashboard_layout` an (Abschnitt C) — auf einer bereits geseedeten
   * Installation bleiben neu hinzugefügte eingebaute Widgets sonst unsichtbar.
   */
  async resolveInstances(slug, registry) {
    let layout = [];
    try {
      layout = (await api.getLayout(slug)) || [];
    } catch (error) {
      console.warn(`[app] /api/layout?tab=${slug} nicht erreichbar`, error);
    }

    const resolved = [];
    let y = 0;
    for (const instance of layout) {
      y = Math.max(y, (instance.y || 0) + (instance.h || 0));
      if (!isWidgetEnabled(registry, instance.widget_id)) {
        continue;
      }
      const descriptor = await this.resolveManifest(instance.widget_id);
      if (!descriptor) {
        continue;
      }
      resolved.push({ ...instance, ...descriptor, pollMs: this.pollMs(instance.widget_id) });
    }

    const present = new Set(resolved.map((instance) => instance.widget_id));
    for (const def of pendingBuiltins(BUILTIN_WIDGETS, slug, present, registry)) {
      const descriptor = await this.resolveManifest(def.id);
      if (!descriptor) {
        continue;
      }
      const size = descriptor.manifest.default_size;
      resolved.push({
        id: `builtin:${def.id}`,
        widget_id: def.id,
        x: 0,
        y,
        w: size.w,
        h: size.h,
        config: descriptor.config,
        config_override: {},
        manifest: descriptor.manifest,
        pollMs: def.pollMs ?? 0,
      });
      y += size.h;
    }
    return resolved;
  }

  async resolveManifest(widgetId) {
    if (this.manifestCache.has(widgetId)) {
      return this.manifestCache.get(widgetId);
    }
    try {
      const descriptor = await this.runtime.resolveWidget(widgetId);
      this.manifestCache.set(widgetId, descriptor);
      return descriptor;
    } catch (error) {
      this.log.error(`[app] Widget ${widgetId} nicht auflösbar`, error);
      return null;
    }
  }

  pollMs(widgetId) {
    const def = BUILTIN_WIDGETS.find((entry) => entry.id === widgetId);
    return def?.pollMs ?? 0;
  }

  /**
   * Platzierung speichern. Standardinstanzen ohne Layout-Zeile (Backend-Seed
   * aus Abschnitt C fehlt noch) werden beim ersten Verschieben angelegt (POST),
   * danach per PUT aktualisiert. Gibt die gespeicherte Instanz zurück — die
   * Bento-Grid-Engine übernimmt eine ggf. neue Instanz-ID.
   */
  persistPlacement(tabId, instance, rect) {
    const body = { ...rect, config_override: instance.config_override ?? {} };
    if (String(instance.id).startsWith("builtin:")) {
      return api.createLayout({ widget_id: instance.widget_id, tab_id: tabId, ...body });
    }
    return api.updateLayout(instance.id, body);
  }

  // --- Fokusmodus (spec-focus F) -------------------------------------------

  /** Signatur des aktiven Fokus — identische Echos werden idempotent ignoriert. */
  _focusSignature(focus = this.focus) {
    return focus ? `${focus.instance_id}|${focus.mode}|${focus.tab_id}` : "";
  }

  /**
   * Touch-Pfad (F): ein Tap auf den Kachel-Body schaltet `enlarge` für genau
   * diese Kachel ein; ein erneuter Tap auf die bereits fokussierte Kachel hebt
   * den Fokus wieder auf.
   */
  handleTileTap(entry) {
    if (!entry) {
      return;
    }
    const instanceId = entry.instance.id;
    if (this.focus && this.focus.instance_id === instanceId) {
      this.clearFocus();
      return;
    }
    this.setFocus({
      widget_id: entry.instance.widget_id,
      instance_id: instanceId,
      tab_id: this.activeSlug,
      mode: "enlarge",
    });
  }

  /**
   * Fokus setzen: ggf. Tab wechseln, Overlay anwenden, Auto-Reset starten und
   * (Touch-Pfad) fire-and-forget persistieren. `persist:false` bei SSE/Start,
   * damit das eigene Echo und der wiederhergestellte Zustand nicht erneut
   * geschrieben werden.
   */
  setFocus({ widget_id, instance_id, tab_id, mode }, { persist = true } = {}) {
    const tab = tab_id || this.activeSlug;
    const next = { widget_id, instance_id, tab_id: tab, mode };
    if (this.focus && this._focusSignature() === this._focusSignature(next)) {
      return;
    }
    if (tab && tab !== this.activeSlug && this.views.has(tab)) {
      this.showTab(tab, { persist: false });
    }
    this.focus = next;
    this.applyFocusToDom();
    this._restartAutoReset();
    if (persist) {
      this._persistFocus();
    }
    this.log?.info?.(`[focus] ${mode} ${widget_id} (${instance_id})`);
  }

  /** Fokus lokal und (falls `persist`) am Backend zurücksetzen. Idempotent. */
  clearFocus({ persist = true } = {}) {
    if (this.autoResetTimer) {
      clearTimeout(this.autoResetTimer);
      this.autoResetTimer = null;
    }
    const had = Boolean(this.focus);
    this.focus = null;
    this.applyFocusToDom();
    if (had && persist) {
      deleteFocusState().catch((error) =>
        this.log?.warn?.("[focus] DELETE /api/state/focus fehlgeschlagen", error),
      );
    }
    if (had) {
      this.log?.info?.("[focus] aufgehoben");
    }
  }

  _persistFocus() {
    const { widget_id, mode, instance_id } = this.focus || {};
    const body = { widget_id, mode };
    // Fallback-Kacheln (`builtin:<id>`) haben (noch) keine Layout-Zeile; dann
    // löst das Backend die Instanz selbst aus `widget_id` auf (spec-focus D.1).
    if (instance_id && !String(instance_id).startsWith("builtin:")) {
      body.instance_id = instance_id;
    }
    putFocusState(body).catch((error) =>
      this.log?.warn?.("[focus] PUT /api/state/focus fehlgeschlagen", error),
    );
  }

  /** Overlay anwenden: alle Grids räumen, dann nur den aktiven Tab fokussieren. */
  applyFocusToDom() {
    this._removeFocusBackButton();
    this._unbindFocusReset();
    for (const grid of this.grids.values()) {
      grid.clearFocus();
    }
    if (!this.focus || this.activeSlug !== this.focus.tab_id) {
      return;
    }
    const grid = this.grids.get(this.activeSlug);
    const entry = grid && grid.get(this.focus.instance_id);
    if (!entry) {
      return;
    }
    grid.setFocus(this.focus.instance_id, this.focus.mode);
    this._mountFocusBackButton(entry);
    this._bindFocusReset(entry);
  }

  _restartAutoReset() {
    if (this.autoResetTimer) {
      clearTimeout(this.autoResetTimer);
      this.autoResetTimer = null;
    }
    if (!this.focus || !(this.focusAutoResetSeconds > 0)) {
      return;
    }
    this.autoResetTimer = setTimeout(
      () => this.clearFocus(),
      this.focusAutoResetSeconds * 1000,
    );
  }

  /** Sichtbare Zurück-Schaltfläche (>= 44x44 px, `aria-label`) in der Kachel (F). */
  _mountFocusBackButton(entry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "focus-back";
    button.textContent = "✕";
    button.setAttribute("aria-label", "Fokus aufheben");
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      this.clearFocus();
    });
    entry.node.append(button);
    this.focusBackButton = button;
  }

  _removeFocusBackButton() {
    this.focusBackButton?.remove?.();
    this.focusBackButton = null;
  }

  /** Jeder `pointerdown` auf der fokussierten Kachel setzt die Auto-Reset-Uhr (F). */
  _bindFocusReset(entry) {
    this._focusTargetNode = entry.node;
    this._onFocusPointerDown = () => this._restartAutoReset();
    entry.node.addEventListener("pointerdown", this._onFocusPointerDown);
  }

  _unbindFocusReset() {
    if (this._focusTargetNode && this._onFocusPointerDown) {
      this._focusTargetNode.removeEventListener("pointerdown", this._onFocusPointerDown);
    }
    this._focusTargetNode = null;
    this._onFocusPointerDown = null;
  }

  /**
   * SSE `focus.changed` (spec-focus D.2/F): Objekt -> Tab wechseln + anwenden;
   * `null` -> Overlay entfernen. Das eigene Echo wird idempotent verworfen.
   */
  onFocusChanged(payload) {
    const focus = payload && typeof payload === "object" ? payload.focus : null;
    if (!focus) {
      this.clearFocus({ persist: false });
      return;
    }
    if (this.focus && this._focusSignature() === this._focusSignature(focus)) {
      return;
    }
    this.setFocus({ ...focus }, { persist: false });
  }

  /**
   * Widget-Verwaltung (WP-9). Listet alle Zeilen aus `GET /api/widgets` mit
   * Name, Version, Kategorie und Zustand und schaltet `is_enabled` über
   * `PATCH /api/widgets/{id}` persistent um. Nach jedem Umschalten werden die
   * Daten-Tabs neu aufgebaut, damit ein deaktiviertes Widget sofort aus dem
   * `dashboard`-Tab verschwindet und ein reaktiviertes wieder erscheint.
   */
  async buildWidgetAdmin(section) {
    const head = document.createElement("div");
    head.className = "widget-admin__head";

    const title = document.createElement("h2");
    title.className = "widget-admin__title";
    title.textContent = "Installierte Widgets";

    const status = document.createElement("p");
    status.className = "widget-admin__status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    head.append(title, status);

    this.widgetListEl = document.createElement("ul");
    this.widgetListEl.className = "widget-admin";
    this.widgetStatusEl = status;
    this.widgetEmptyNote = document.createElement("p");
    this.widgetEmptyNote.className = "empty-state";
    this.widgetEmptyNote.textContent =
      "Backend-Registry noch leer (Widget-Seed fehlt). Aktivieren/Deaktivieren ist erst möglich, wenn GET /api/widgets Zeilen liefert.";

    section.append(
      head,
      this.buildInstallForms(status),
      this.widgetListEl,
      this.widgetEmptyNote,
    );
    await this.reloadWidgets();
  }

  /**
   * Installations-UI (WP-P2-4): Zip-Upload und Git-URL. Ein erfolgreicher
   * Aufruf registriert das Widget (`is_builtin=0`) und baut Liste/Ansichten neu.
   */
  buildInstallForms(status) {
    const wrapper = document.createElement("div");
    wrapper.className = "widget-install";
    wrapper.append(this.buildZipInstallForm(status), this.buildGitInstallForm(status));
    return wrapper;
  }

  buildZipInstallForm(status) {
    const form = document.createElement("form");
    form.className = "widget-install__form";
    form.setAttribute("aria-label", "Widget aus Zip installieren");

    const label = document.createElement("label");
    label.className = "widget-install__label";
    label.textContent = "Widget-Zip";

    const input = document.createElement("input");
    input.type = "file";
    input.name = "file";
    input.accept = ".zip,application/zip";
    input.className = "widget-install__input";
    label.append(input);

    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "widget-install__submit";
    submit.textContent = "Zip installieren";

    form.append(label, submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const file = input.files?.[0];
      if (!file) {
        status.textContent = "Bitte zuerst eine Zip-Datei auswählen.";
        return;
      }
      submit.disabled = true;
      status.textContent = "Zip wird installiert…";
      try {
        const created = await api.installWidget(file);
        form.reset();
        await this.reloadWidgets();
        await this.refreshDataViews();
        status.textContent = `Widget „${created?.widget?.id ?? file.name}“ installiert.`;
      } catch (error) {
        status.textContent = `Installation fehlgeschlagen – ${error?.message || error}`;
        this.log?.error?.("[app] Zip-Installation fehlgeschlagen", error);
      } finally {
        submit.disabled = false;
      }
    });
    return form;
  }

  buildGitInstallForm(status) {
    const form = document.createElement("form");
    form.className = "widget-install__form";
    form.setAttribute("aria-label", "Widget aus Git installieren");

    const urlLabel = document.createElement("label");
    urlLabel.className = "widget-install__label";
    urlLabel.textContent = "Git-URL";
    const urlInput = document.createElement("input");
    urlInput.type = "url";
    urlInput.name = "url";
    urlInput.required = true;
    urlInput.placeholder = "https://…/widget.git";
    urlInput.className = "widget-install__input";
    urlLabel.append(urlInput);

    const refLabel = document.createElement("label");
    refLabel.className = "widget-install__label widget-install__label--ref";
    refLabel.textContent = "Branch/Tag";
    const refInput = document.createElement("input");
    refInput.type = "text";
    refInput.name = "ref";
    refInput.value = "main";
    refInput.className = "widget-install__input";
    refLabel.append(refInput);

    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "widget-install__submit";
    submit.textContent = "Von Git installieren";

    form.append(urlLabel, refLabel, submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      status.textContent = "Git-Repository wird installiert…";
      try {
        const created = await api.installWidgetGit(urlInput.value.trim(), refInput.value.trim() || "main");
        await this.reloadWidgets();
        await this.refreshDataViews();
        status.textContent = `Widget „${created?.widget?.id ?? "?"}“ installiert.`;
      } catch (error) {
        status.textContent = `Installation fehlgeschlagen – ${error?.message || error}`;
        this.log?.error?.("[app] Git-Installation fehlgeschlagen", error);
      } finally {
        submit.disabled = false;
      }
    });
    return form;
  }

  /** Registry neu laden und Liste (inkl. Deinstallieren) neu aufbauen. */
  async reloadWidgets() {
    const registry = await safeGetWidgets();
    const hasRegistry = Array.isArray(registry) && registry.length > 0;
    this.widgetRegistry = hasRegistry ? registry : null;

    const list = this.widgetListEl;
    const status = this.widgetStatusEl;
    list.replaceChildren();

    if (hasRegistry) {
      for (const widget of registry) {
        list.append(this.buildWidgetRow(widget, { status, interactive: true }));
      }
    } else {
      // Keine Registry-Zeilen: nur die eingebauten Platzhalter zeigen, ohne
      // Schalter – PATCH hätte kein Ziel (Datenquelle fehlt sichtbar gemacht).
      for (const def of BUILTIN_WIDGETS) {
        list.append(
          this.buildWidgetRow(
            {
              id: def.id,
              name: def.id,
              version: "",
              category: "",
              is_enabled: true,
              is_builtin: true,
            },
            { status, interactive: false },
          ),
        );
      }
    }

    this.renderWidgetStatus(status);
    if (this.widgetEmptyNote) {
      this.widgetEmptyNote.hidden = hasRegistry;
    }
  }

  /** Eine Verwaltungszeile: Name/Meta links, Tap-Schalter rechts (>= 44x44). */
  buildWidgetRow(widget, { status, interactive }) {
    const item = document.createElement("li");
    item.className = "widget-admin__item";
    item.dataset.widgetId = widget.id;

    const info = document.createElement("div");
    info.className = "widget-admin__info";

    const name = document.createElement("span");
    name.className = "widget-admin__name";
    name.textContent = widget.name || widget.id;

    const meta = document.createElement("span");
    meta.className = "widget-admin__meta";
    meta.textContent = [
      widget.id,
      widget.version ? `v${widget.version}` : null,
      widget.category ? CATEGORY_LABELS[widget.category] || widget.category : null,
      widget.is_builtin ? "eingebaut" : null,
    ]
      .filter(Boolean)
      .join(" · ");

    info.append(name, meta);

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "widget-admin__toggle";
    toggle.setAttribute("role", "switch");
    toggle.disabled = !interactive;
    this.renderToggleState(toggle, widget.is_enabled !== false);
    if (interactive) {
      toggle.addEventListener("click", () => this.toggleWidget(widget, { toggle, status }));
    } else {
      toggle.setAttribute("aria-label", `Widget ${widget.id}: Zustand nicht verfügbar`);
    }

    // Deinstallation nur für installierte (nicht eingebaute) Widgets (WP-P2-4).
    if (interactive && !widget.is_builtin) {
      const uninstall = document.createElement("button");
      uninstall.type = "button";
      uninstall.className = "widget-admin__uninstall";
      uninstall.textContent = "Deinstallieren";
      uninstall.setAttribute("aria-label", `Widget ${widget.name || widget.id} deinstallieren`);
      uninstall.addEventListener("click", () =>
        this.uninstallWidget(widget, { status, button: uninstall }),
      );
      item.append(info, uninstall, toggle);
      return item;
    }

    item.append(info, toggle);
    return item;
  }

  /** Deinstalliert ein installiertes Widget (`DELETE /api/widgets/{id}`). */
  async uninstallWidget(widget, { status, button }) {
    button.disabled = true;
    status.textContent = `${widget.name || widget.id} wird deinstalliert…`;
    try {
      await api.uninstallWidget(widget.id);
      await this.reloadWidgets();
      await this.refreshDataViews();
      status.textContent = `${widget.name || widget.id} deinstalliert.`;
    } catch (error) {
      button.disabled = false;
      status.textContent = `${widget.name || widget.id}: Deinstallation fehlgeschlagen – ${error?.message || error}`;
      this.log?.error?.(`[app] Widget ${widget.id} deinstallieren fehlgeschlagen`, error);
    }
  }

  /** Zustand trägt Form + Text, nicht nur Farbe (I.2, graustufenfest). */
  renderToggleState(toggle, enabled) {
    toggle.dataset.state = enabled ? "on" : "off";
    toggle.setAttribute("aria-checked", enabled ? "true" : "false");
    toggle.setAttribute(
      "aria-label",
      enabled ? "Aktiv – zum Deaktivieren tippen" : "Deaktiviert – zum Aktivieren tippen",
    );
    toggle.textContent = enabled ? "Aktiv" : "Deaktiviert";
  }

  /** Schaltet `is_enabled` persistent um (PATCH /api/widgets/{id}). */
  async toggleWidget(widget, { toggle, status }) {
    const previous = widget.is_enabled !== false;
    const next = !previous;
    toggle.disabled = true;
    status.textContent = `${widget.name || widget.id} wird ${next ? "aktiviert" : "deaktiviert"}…`;
    try {
      const saved = await api.patchWidget(widget.id, { is_enabled: next });
      widget.is_enabled = saved?.is_enabled !== false;
      this.renderToggleState(toggle, widget.is_enabled);
      toggle.disabled = false;
      this.renderWidgetStatus(status);
      await this.refreshDataViews();
    } catch (error) {
      widget.is_enabled = previous;
      this.renderToggleState(toggle, previous);
      toggle.disabled = false;
      status.textContent = `${widget.name || widget.id}: Schalten fehlgeschlagen – ${error?.message || error}`;
      this.log?.error?.(`[app] Widget ${widget.id} schalten fehlgeschlagen`, error);
    }
  }

  /** Statuszeile „N von M aktiv" aus dem aktuellen Registry-Zustand. */
  renderWidgetStatus(status) {
    const widgets = this.widgetRegistry;
    if (!Array.isArray(widgets) || widgets.length === 0) {
      status.textContent = "Registry leer – Zustand nicht verfügbar";
      return;
    }
    const active = widgets.filter((widget) => widget.is_enabled !== false).length;
    status.textContent = `${active} von ${widgets.length} aktiv`;
  }

  /** Daten-Tabs neu aufbauen, damit `is_enabled` sofort sichtbar wirkt (AC3). */
  async refreshDataViews() {
    for (const slug of [...this.views.keys()]) {
      if (slug === WIDGETS_TAB) {
        continue;
      }
      await this.rebuildView(slug);
    }
  }

  async rebuildView(slug) {
    const section = this.views.get(slug);
    if (!section) {
      return;
    }
    for (const entry of [...this.runtime.instances.values()]) {
      if (entry.container && section.contains(entry.container)) {
        this.runtime.destroy(entry);
      }
    }
    const grid = this.grids.get(slug);
    if (grid) {
      grid.destroy();
      this.grids.delete(slug);
    }
    section.replaceChildren();
    await this.buildBento(section, slug);
    this.applyFocusToDom();
  }

  showTab(slug, { persist = true } = {}) {
    if (!this.views.has(slug)) {
      return;
    }
    this.activeSlug = slug;
    for (const [key, view] of this.views) {
      view.hidden = key !== slug;
    }
    for (const [key, button] of this.tabButtons) {
      button.setAttribute("aria-selected", key === slug ? "true" : "false");
    }
    if (persist) {
      api.putState("active_tab", slug).catch(() => {
        /* Der aktive Tab ist Komfort, kein Fehlerfall. */
      });
    }
    this.applyFocusToDom();
  }

  setConnection(state) {
    const el = document.getElementById("conn");
    if (!el) {
      return;
    }
    el.dataset.state = state;
    el.querySelector(".conn__label").textContent = connectionLabel(state);
  }
}

async function main() {
  const app = new DashboardApp();
  try {
    await app.start();
    document.getElementById("app")?.setAttribute("data-state", "ready");
  } catch (error) {
    console.error("[app] Start fehlgeschlagen", error);
    document.getElementById("app")?.setAttribute("data-state", "error");
    const views = document.getElementById("views");
    if (views) {
      const message = document.createElement("p");
      message.className = "empty-state";
      message.textContent = `Start fehlgeschlagen: ${error?.message || error}`;
      views.replaceChildren(message);
    }
  }
}

main();
