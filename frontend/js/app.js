/**
 * app.js — Bootstrap, Tab-Routing, SSE-Client, Widget-Laden (Spezifikation D, I).
 *
 * Ablauf: `/api/config` laden -> Tabs rendern -> pro Tab Instanzen auflösen
 * (Backend-Layout, sonst eingebaute Standardplatzierung) -> Widget-Runtime
 * laden. To-Dos laufen über SSE (`/api/events`), Cache-Daten über Polling (D6).
 */

import { api } from "./api.js";
import { BentoGrid } from "./bento.js";
import { WidgetRuntime } from "./widget-runtime.js";

/**
 * Eingebaute Widgets dieses Repos und ihr Ziel-Tab. Der Backend-Registry-Eintrag
 * (`/api/widgets`) und `dashboard_layout` haben Vorrang; diese Liste ist die
 * Standardplatzierung, solange WP-2 die Manifeste noch nicht in die `widgets`-
 * Tabelle registriert (Abschnitt C der Spezifikation).
 */
const BUILTIN_WIDGETS = [
  // Wetter/ggf. Garmin sind Cache-Daten -> Polling (Wetter 10 min, Spec I.4).
  { id: "weather", tab: "dashboard", pollMs: 600000 },
  { id: "hermes-todos", tab: "todos", pollMs: 0 },
];

const WIDGETS_TAB = "widgets";
const SSE_EVENTS = ["task.created", "task.updated", "task.deleted", "screen.changed"];

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

class DashboardApp {
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
  }

  async start() {
    const config = await api.getConfig();
    this.grid = config.grid || this.grid;
    const tabs = Array.isArray(config.tabs) ? config.tabs : [];

    this.renderTabs(tabs);
    await Promise.all(tabs.map((tab) => this.buildView(tab)));

    const active = tabs.some((tab) => tab.slug === config.active_tab)
      ? config.active_tab
      : tabs[0]?.slug;
    if (active) {
      this.showTab(active, { persist: false });
    }

    this.eventStream = connectEventStream({
      bus: this.bus,
      onStatus: (state) => this.setConnection(state),
      log: this.log,
    });

    // Debug-/Test-Handle (z. B. für den Fehlervertrag-Nachweis in WP-3).
    window.worldplaner = { app: this, runtime: this.runtime, bus: this.bus };
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

  /** Layout aus dem Backend; sonst Standardplatzierung der eingebauten Widgets. */
  async resolveInstances(slug, registry) {
    let layout = [];
    try {
      layout = (await api.getLayout(slug)) || [];
    } catch (error) {
      console.warn(`[app] /api/layout?tab=${slug} nicht erreichbar`, error);
    }

    if (layout.length > 0) {
      const resolved = [];
      for (const instance of layout) {
        if (!isWidgetEnabled(registry, instance.widget_id)) {
          continue;
        }
        const descriptor = await this.resolveManifest(instance.widget_id);
        if (!descriptor) {
          continue;
        }
        resolved.push({ ...instance, ...descriptor, pollMs: this.pollMs(instance.widget_id) });
      }
      return resolved;
    }

    const fallback = [];
    let y = 0;
    for (const def of BUILTIN_WIDGETS) {
      if (def.tab !== slug || !isWidgetEnabled(registry, def.id)) {
        continue;
      }
      const descriptor = await this.resolveManifest(def.id);
      if (!descriptor) {
        continue;
      }
      const size = descriptor.manifest.default_size;
      fallback.push({
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
    return fallback;
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

  async buildWidgetAdmin(section) {
    const registry = await safeGetWidgets();
    const rows =
      Array.isArray(registry) && registry.length > 0
        ? registry
        : BUILTIN_WIDGETS.map((def) => ({
            id: def.id,
            name: def.id,
            version: "",
            category: "",
            is_enabled: true,
            is_builtin: true,
          }));

    const list = document.createElement("ul");
    list.className = "widget-admin";
    for (const widget of rows) {
      const item = document.createElement("li");
      item.className = "widget-admin__item";

      const name = document.createElement("span");
      name.className = "widget-admin__name";
      name.textContent = widget.name || widget.id;

      const meta = document.createElement("span");
      meta.className = "widget-admin__meta";
      meta.textContent = [
        widget.id,
        widget.version,
        widget.category,
        widget.is_enabled === false ? "deaktiviert" : "aktiv",
      ]
        .filter(Boolean)
        .join(" · ");

      item.append(name, meta);
      list.append(item);
    }
    section.append(list);

    if (!Array.isArray(registry) || registry.length === 0) {
      const note = document.createElement("p");
      note.className = "empty-state";
      note.textContent =
        "Backend-Registry noch leer; Aktivieren/Deaktivieren folgt in Work-Package WP-9.";
      section.append(note);
    }
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
