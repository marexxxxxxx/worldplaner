/**
 * Hermes-To-Dos — Widget (Widget-Plugin-Vertrag E.3).
 *
 * Zeigt `hermes_tasks` (REST: GET /api/tasks), hakt per PATCH /api/tasks/{id}
 * mit `base_version` ab (optimistische Sperre, D.4) und aktualisiert sich bei
 * SSE-Pushes (`task.created|updated|deleted`) ohne manuellen Reload (I.4).
 *
 * Es wird nur `api` benutzt (E.5) — kein direkter `fetch` im Widget.
 */

const FILTERS = [
  { value: "open", label: "Offen" },
  { value: "done", label: "Erledigt" },
  { value: "all", label: "Alle" },
];

const PRIORITY = {
  high: { label: "hoch", glyph: "▲" },
  normal: { label: "normal", glyph: "●" },
  low: { label: "niedrig", glyph: "▽" },
};

const SSE_EVENTS = ["task.created", "task.updated", "task.deleted"];

const VALID_FILTERS = new Set(FILTERS.map((entry) => entry.value));

function normalizeFilter(value) {
  return VALID_FILTERS.has(value) ? value : "open";
}

export default class HermesTodosWidget {
  constructor(container, config, api) {
    this.container = container;
    this.api = api;
    this.config = config || {};
    this.filter = normalizeFilter(this.config.filter);
    this.tasks = [];
    this.unsubscribers = [];
    this.refreshTimer = null;
    this.pendingToggles = new Set();
  }

  /** Baut das DOM einmalig auf (idempotent). */
  async render() {
    const root = document.createElement("div");
    root.className = "ht";

    this.statusEl = document.createElement("p");
    this.statusEl.className = "ht-status";
    this.statusEl.setAttribute("role", "status");
    this.statusEl.setAttribute("aria-live", "polite");

    this.filterButtons = new Map();
    const filterBar = document.createElement("div");
    filterBar.className = "ht-filters";
    filterBar.setAttribute("role", "group");
    filterBar.setAttribute("aria-label", "To-Do-Filter");
    for (const entry of FILTERS) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ht-filter";
      button.textContent = entry.label;
      button.addEventListener("click", () => this.setFilter(entry.value));
      this.filterButtons.set(entry.value, button);
      filterBar.append(button);
    }

    const toolbar = document.createElement("div");
    toolbar.className = "ht-toolbar";
    toolbar.append(filterBar, this.statusEl);

    this.listEl = document.createElement("ul");
    this.listEl.className = "ht-list";

    this.emptyEl = document.createElement("p");
    this.emptyEl.className = "ht-empty";

    root.append(toolbar, this.listEl, this.emptyEl);
    this.container.replaceChildren(root);
    this.syncFilterButtons();

    // SSE-Push -> Liste neu laden (D6/I.4).
    for (const event of SSE_EVENTS) {
      this.unsubscribers.push(this.api.subscribe(event, () => this.scheduleRefresh()));
    }
  }

  /** Lädt die Aufgaben und aktualisiert nur die Liste (kein Layout-Sprung). */
  async refresh() {
    const tasks = await this.api.fetchJson(
      `/api/tasks?status=${encodeURIComponent(this.filter)}&limit=100`,
    );
    this.tasks = Array.isArray(tasks) ? tasks : [];
    this.renderList();
  }

  onConfigChange(newConfig) {
    if (newConfig && newConfig.filter !== undefined) {
      this.filter = normalizeFilter(newConfig.filter);
      this.syncFilterButtons();
    }
  }

  destroy() {
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
    for (const unsubscribe of this.unsubscribers) {
      try {
        unsubscribe();
      } catch {
        /* Unsubscribe darf das Entladen nie brechen. */
      }
    }
    this.unsubscribers = [];
    this.container.replaceChildren();
  }

  // --- intern ---------------------------------------------------------------

  scheduleRefresh() {
    if (this.refreshTimer) {
      return;
    }
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      this.refresh().catch((error) => this.setStatus(this.describe(error), "error"));
    }, 50);
  }

  setFilter(value) {
    const next = normalizeFilter(value);
    if (next === this.filter) {
      return;
    }
    this.filter = next;
    this.syncFilterButtons();
    this.refresh().catch((error) => this.setStatus(this.describe(error), "error"));
  }

  syncFilterButtons() {
    for (const [value, button] of this.filterButtons) {
      button.setAttribute("aria-pressed", value === this.filter ? "true" : "false");
    }
  }

  renderList() {
    this.listEl.replaceChildren();
    this.emptyEl.hidden = this.tasks.length > 0;
    this.emptyEl.textContent =
      this.tasks.length === 0 ? this.emptyMessage() : "";
    for (const task of this.tasks) {
      this.listEl.append(this.renderItem(task));
    }
    this.setStatus(`${this.tasks.length} ${this.tasks.length === 1 ? "Eintrag" : "Einträge"}`);
  }

  emptyMessage() {
    if (this.filter === "done") {
      return "Noch nichts erledigt.";
    }
    if (this.filter === "open") {
      return "Keine offenen To-Dos.";
    }
    return "Keine To-Dos.";
  }

  renderItem(task) {
    const item = document.createElement("li");
    item.className = "ht-item";
    item.dataset.status = task.status;
    item.dataset.priority = task.priority || "normal";

    const check = document.createElement("label");
    check.className = "ht-check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.className = "ht-check__input";
    input.checked = task.status === "done";
    input.setAttribute(
      "aria-label",
      `${task.title} als ${task.status === "done" ? "offen" : "erledigt"} markieren`,
    );
    input.addEventListener("change", () => this.toggle(task, input));
    const box = document.createElement("span");
    box.className = "ht-check__box";
    box.setAttribute("aria-hidden", "true");
    check.append(input, box);

    const main = document.createElement("div");
    main.className = "ht-item__main";
    const title = document.createElement("span");
    title.className = "ht-item__title";
    title.textContent = task.title;
    const meta = document.createElement("span");
    meta.className = "ht-item__meta";
    meta.append(this.metaPrefix(task));
    main.append(title, meta);

    item.append(check, main);
    return item;
  }

  /** Zustand und Priorität als Glyphe UND Text (nie nur Farbe, I.2). */
  metaPrefix(task) {
    const fragment = document.createDocumentFragment();
    const state = document.createElement("span");
    state.className = "ht-item__state";
    state.textContent = task.status === "done" ? "✓ erledigt" : "○ offen";
    fragment.append(state);

    const priority = PRIORITY[task.priority] || PRIORITY.normal;
    fragment.append(` · ${priority.glyph} ${priority.label}`);

    if (task.due_at) {
      const overdue = task.status === "open" && task.due_at < todayIso();
      const due = document.createElement("span");
      if (overdue) {
        due.className = "ht-item__overdue";
      }
      due.textContent = ` · fällig ${task.due_at}${overdue ? " (überfällig)" : ""}`;
      fragment.append(due);
    }

    fragment.append(` · Quelle: ${task.source === "hermes" ? "Hermes" : "lokal"}`);
    return fragment;
  }

  /** Abhaken -> PATCH mit base_version (D.4, "Hermes gewinnt immer"). */
  async toggle(task, input) {
    if (this.pendingToggles.has(task.id)) {
      return;
    }
    this.pendingToggles.add(task.id);
    input.disabled = true;
    const status = input.checked ? "done" : "open";
    try {
      await this.api.fetchJson(`/api/tasks/${encodeURIComponent(task.id)}`, {
        method: "PATCH",
        body: { status, base_version: task.version },
      });
      await this.refresh();
    } catch (error) {
      input.checked = !input.checked;
      if (error && error.code === "CONFLICT") {
        this.setStatus("Von Hermes geändert – neu geladen.", "error");
        try {
          await this.refresh();
        } catch {
          /* Der Konflikt-Hinweis bleibt sichtbar. */
        }
      } else {
        this.setStatus(this.describe(error), "error");
      }
    } finally {
      this.pendingToggles.delete(task.id);
      input.disabled = false;
    }
  }

  setStatus(text, kind = "info") {
    this.statusEl.textContent = text || "";
    if (kind === "error") {
      this.statusEl.dataset.kind = "error";
    } else {
      delete this.statusEl.dataset.kind;
    }
  }

  describe(error) {
    if (error && error.code) {
      return `Fehler (${error.code}): ${error.message || ""}`.trim();
    }
    return "Unbekannter Fehler beim Laden der To-Dos.";
  }
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}
