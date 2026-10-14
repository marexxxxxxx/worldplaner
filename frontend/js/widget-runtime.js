/**
 * Widget-Runtime — Plugin-Host (Widget-Plugin-Vertrag E, Spezifikation I).
 *
 * Aufgaben:
 *  - Manifest eines Widgets auflösen (Backend-Registry `/api/widgets/{id}`,
 *    Rückfall auf die statische Datei `/widgets/{id}/manifest.json`).
 *  - `frontend/widget.js` dynamisch als ES-Modul laden (kein `eval`).
 *  - Lifecycle `constructor -> render -> (refresh)* -> destroy` fahren und
 *    jeden Schritt auf der Konsole protokollieren (WP-3-Nachweis).
 *  - Fehlervertrag (E.3): wirft `render`/`refresh`, wird der Fehler abgefangen,
 *    ein Platzhalter mit Widget-ID + Fehlercode gezeigt und der Poll dieses
 *    Widgets bis zur nächsten Config-Änderung gestoppt. Der Host stürzt nie ab.
 *  - Plugin-API `api` (E.5) mit Permission-Gate (E.4).
 */

import { ApiError, api } from "./api.js";

const WIDGET_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const SEMVER_PATTERN = /^\d+\.\d+\.\d+$/;
const CATEGORIES = new Set(["weather", "health", "productivity", "general"]);

/** Pfadmuster je Permission (E.4); identisch zur Server-Map `permissions.py`. */
const PERMISSION_RULES = {
  garmin_cache: (path) => path.startsWith("/api/garmin/"),
  weather: (path) => path.startsWith("/api/weather/"),
  tasks: (path) => path.startsWith("/api/tasks"),
  state: (path) => path.startsWith("/api/state/"),
  api_cache: (path) => path.startsWith("/api/cache/"),
  baikal: (path) => path.startsWith("/api/baikal/"),
  grocy: (path) => path.startsWith("/api/grocy/"),
  mealie: (path) => path.startsWith("/api/mealie/"),
};

export class WidgetError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "WidgetError";
    this.code = code;
  }
}

/** Prüft ein Manifest gegen die Pflichtfelder aus E.2. */
export function validateManifest(manifest, expectedId) {
  if (!manifest || typeof manifest !== "object") {
    throw new WidgetError("MANIFEST_INVALID", "Manifest ist kein Objekt");
  }
  if (!WIDGET_ID_PATTERN.test(manifest.id || "")) {
    throw new WidgetError("MANIFEST_INVALID", `Ungültige Widget-ID: ${manifest.id}`);
  }
  if (expectedId && manifest.id !== expectedId) {
    throw new WidgetError(
      "MANIFEST_INVALID",
      `Manifest-ID ${manifest.id} passt nicht zu ${expectedId}`,
    );
  }
  if (!manifest.name || typeof manifest.name !== "string") {
    throw new WidgetError("MANIFEST_INVALID", "Feld 'name' fehlt");
  }
  if (!SEMVER_PATTERN.test(manifest.version || "")) {
    throw new WidgetError("MANIFEST_INVALID", `Ungültige Version: ${manifest.version}`);
  }
  if (!CATEGORIES.has(manifest.category)) {
    throw new WidgetError("MANIFEST_INVALID", `Unbekannte Kategorie: ${manifest.category}`);
  }
  const size = manifest.default_size;
  if (!size || !Number.isInteger(size.w) || !Number.isInteger(size.h)) {
    throw new WidgetError("MANIFEST_INVALID", "Feld 'default_size' fehlt oder ist ungültig");
  }
  if (!Array.isArray(manifest.permissions || [])) {
    throw new WidgetError("MANIFEST_INVALID", "Feld 'permissions' muss ein Array sein");
  }
  return manifest;
}

export class WidgetRuntime {
  /**
   * @param {object} options
   * @param {object} [options.eventBus]  Pub/Sub für SSE-Events (`on(event, cb)`).
   * @param {object} [options.log]       Konsolen-Ersatz (Tests).
   * @param {string} [options.cacheEndpoint] Basis für `api.cacheGet` (E.5).
   */
  constructor({ eventBus = null, log = console, cacheEndpoint = "/api/cache/" } = {}) {
    this.eventBus = eventBus;
    this.log = log;
    this.cacheEndpoint = cacheEndpoint;
    this.instances = new Map();
    this.injectedStyles = new Set();
  }

  /** Manifest + Config eines Widgets: Registry zuerst, dann statische Datei. */
  async resolveWidget(widgetId, { manifest = null, config = {} } = {}) {
    if (manifest) {
      return { manifest: validateManifest(manifest, widgetId), config };
    }
    try {
      const detail = await api.getWidget(widgetId);
      return {
        manifest: validateManifest(detail.manifest, widgetId),
        config: detail.config || {},
      };
    } catch (error) {
      if (!error || error.status !== 404) {
        throw error;
      }
      // Backend-Registry führt das Widget (noch) nicht -> statisches Manifest.
      return { manifest: await this.fetchStaticManifest(widgetId), config: {} };
    }
  }

  async fetchStaticManifest(widgetId) {
    const response = await fetch(`/widgets/${widgetId}/manifest.json`);
    if (!response.ok) {
      throw new WidgetError(
        "MANIFEST_NOT_FOUND",
        `Manifest /widgets/${widgetId}/manifest.json nicht gefunden (${response.status})`,
      );
    }
    return validateManifest(await response.json(), widgetId);
  }

  /**
   * Lädt ein Widget: Manifest auflösen, Modul importieren, Lifecycle starten.
   * `widgetClass`/`manifest` erlauben es, den Host ohne Netz zu testen.
   */
  async loadWidget({
    instanceId,
    widgetId,
    container,
    config = {},
    pollMs = 0,
    manifest = null,
    widgetClass = null,
  }) {
    const resolved = await this.resolveWidget(widgetId, { manifest, config });
    const entry = {
      instanceId,
      widgetId,
      container,
      manifest: resolved.manifest,
      config: { ...resolved.config, ...config },
      pollMs,
      widget: null,
      timer: null,
      unsubscribers: [],
      state: "created",
      errorCode: null,
    };
    this.instances.set(instanceId, entry);

    try {
      let WidgetClass = widgetClass;
      if (!WidgetClass) {
        this.injectStyles(widgetId);
        const module = await import(`/widgets/${widgetId}/frontend/widget.js`);
        WidgetClass = module.default;
      }
      if (typeof WidgetClass !== "function") {
        throw new WidgetError("MODULE_INVALID", "Modul exportiert keine Widget-Klasse");
      }
      await this.mount(entry, WidgetClass);
    } catch (error) {
      this.failInstance(entry, error);
    }
    return entry;
  }

  /** Instantiiert und rendert — der eigentliche Lifecycle-Kern. */
  async mount(entry, WidgetClass) {
    this.log.info(`[widget:${entry.widgetId}] constructor`);
    entry.container.classList.add(`wg-${entry.widgetId}`);
    entry.container.dataset.widgetId = entry.widgetId;
    entry.widget = new WidgetClass(entry.container, entry.config, this.createPluginApi(entry));

    this.log.info(`[widget:${entry.widgetId}] render`);
    try {
      await entry.widget.render();
    } catch (error) {
      this.failInstance(entry, error);
      return entry;
    }

    entry.state = "ready";
    await this.refresh(entry);
    this.schedule(entry);
    return entry;
  }

  /** `refresh()` mit Fehlervertrag; im Fehlerzustand wird nicht mehr gepollt. */
  async refresh(entry) {
    if (entry.state === "error" || !entry.widget) {
      return;
    }
    this.log.info(`[widget:${entry.widgetId}] refresh`);
    try {
      await entry.widget.refresh();
      entry.state = "ready";
    } catch (error) {
      this.failInstance(entry, error);
    }
  }

  /** Config-Änderung: `onConfigChange` + `refresh`, Fehlerzustand wird geleert. */
  async applyConfig(entry, config) {
    entry.config = { ...entry.config, ...config };
    entry.state = entry.widget ? "ready" : entry.state;
    this.log.info(`[widget:${entry.widgetId}] onConfigChange`);
    try {
      if (typeof entry.widget.onConfigChange === "function") {
        entry.widget.onConfigChange(entry.config);
      }
    } catch (error) {
      this.failInstance(entry, error);
      return;
    }
    await this.refresh(entry);
    this.schedule(entry);
  }

  /** Entlädt ein Widget: Timer, Subscriptions, DOM (E.3). */
  destroy(entry) {
    this.log.info(`[widget:${entry.widgetId}] destroy`);
    this.stop(entry);
    for (const unsubscribe of entry.unsubscribers) {
      try {
        unsubscribe();
      } catch {
        /* Unsubscribe-Fehler darf den Host nicht brechen. */
      }
    }
    entry.unsubscribers = [];
    if (entry.widget && typeof entry.widget.destroy === "function") {
      try {
        entry.widget.destroy();
      } catch (error) {
        this.log.warn(`[widget:${entry.widgetId}] destroy fehlgeschlagen`, error);
      }
    }
    entry.container.replaceChildren();
    entry.container.classList.remove(`wg-${entry.widgetId}`);
    entry.state = "destroyed";
    this.instances.delete(entry.instanceId);
  }

  destroyAll() {
    for (const entry of [...this.instances.values()]) {
      this.destroy(entry);
    }
  }

  schedule(entry) {
    this.stop(entry);
    if (!entry.pollMs) {
      return;
    }
    entry.timer = setInterval(() => {
      this.refresh(entry);
    }, entry.pollMs);
  }

  stop(entry) {
    if (entry.timer) {
      clearInterval(entry.timer);
      entry.timer = null;
    }
  }

  /** Fehlervertrag: Platzhalter mit Widget-ID + Fehlercode, Poll gestoppt. */
  failInstance(entry, error) {
    const code = (error && error.code) || "WIDGET_ERROR";
    entry.state = "error";
    entry.errorCode = code;
    this.stop(entry);
    this.log.error(`[widget:${entry.widgetId}] error ${code}`, error);

    const placeholder = document.createElement("div");
    placeholder.className = "wg-error";
    placeholder.setAttribute("role", "alert");
    placeholder.dataset.errorCode = code;

    const title = document.createElement("p");
    title.className = "wg-error__title";
    title.textContent = `Widget „${entry.widgetId}“ nicht verfügbar`;

    const detail = document.createElement("p");
    detail.className = "wg-error__code";
    detail.textContent = `Fehlercode: ${code}`;

    placeholder.append(title, detail);
    entry.container.replaceChildren(placeholder);
  }

  /** Plugin-API (E.5) mit Permission-Gate (E.4). */
  createPluginApi(entry) {
    const permissions = new Set(entry.manifest.permissions || []);
    const isAllowed = (path) =>
      Object.entries(PERMISSION_RULES).some(
        ([permission, matches]) => permissions.has(permission) && matches(path),
      );

    return {
      /**
       * GET/PATCH/POST auf erlaubte Pfade. Die Signatur ist eine Erweiterung
       * von E.5 (`fetchJson(path)`), weil WP-7 einen PATCH mit Body verlangt.
       */
      fetchJson: async (path, options = {}) => {
        if (!isAllowed(path)) {
          throw new ApiError({
            code: "FORBIDDEN",
            message: `Widget ${entry.widgetId} hat keine Permission für ${path}`,
            status: 403,
          });
        }
        return api.request(path, {
          ...options,
          headers: { ...(options.headers || {}), "X-Widget-Id": entry.widgetId },
        });
      },
      subscribe: (event, callback) => {
        if (!this.eventBus) {
          return () => {};
        }
        const unsubscribe = this.eventBus.on(event, callback);
        entry.unsubscribers.push(unsubscribe);
        return unsubscribe;
      },
      getConfig: () => ({ ...entry.config }),
      cacheGet: async (key) => {
        if (!permissions.has("api_cache")) {
          throw new ApiError({
            code: "FORBIDDEN",
            message: `Widget ${entry.widgetId} hat keine Permission 'api_cache'`,
            status: 403,
          });
        }
        try {
          return await api.request(`${this.cacheEndpoint}${encodeURIComponent(key)}`, {
            headers: { "X-Widget-Id": entry.widgetId },
          });
        } catch (error) {
          if (error && error.status === 404) {
            return null;
          }
          throw error;
        }
      },
    };
  }

  injectStyles(widgetId) {
    if (this.injectedStyles.has(widgetId) || typeof document === "undefined") {
      return;
    }
    this.injectedStyles.add(widgetId);
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = `/widgets/${widgetId}/frontend/style.css`;
    document.head.append(link);
  }
}
