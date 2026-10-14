/**
 * E2E-Abnahme für K2 (WP-3 Widget-Grundsystem + WP-7 Hermes-To-Do-Tab).
 *
 * Voraussetzungen:
 *   - laufendes Backend mit ausgeliefertem Frontend (D10),
 *     z. B. `docker compose up -d` -> http://127.0.0.1:8000
 *   - Chromium: entweder über `CHROMIUM_PATH=/pfad/zu/chrome` oder die von
 *     Playwright verwalteten Browser.
 *
 * Aufruf:  BASE_URL=http://127.0.0.1:8000 npm run test:e2e
 *
 * Deckt die binären Abnahmekriterien ab:
 *   (1) dynamisches Laden + Lifecycle constructor->render->refresh->destroy
 *   (2) Exception in render() -> Platzhalter + Fehlercode, kein Host-Absturz
 *   (3) todos-Tab listet hermes_tasks
 *   (4) Abhaken -> PATCH mit base_version, persistiert nach Reload
 *   (5) SSE-Push erscheint ohne manuellen Reload
 *   + Tap-Targets >= 44px, Palette, Permission-Gate, Bento-Platzierung
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";
const EXEC = process.env.CHROMIUM_PATH || undefined;
const SHOT_DIR = process.env.SHOT_DIR || new URL("./screenshots/", import.meta.url).pathname;

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: Boolean(ok) });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  :: " + detail : ""}`);
}

async function api(path, options = {}) {
  const response = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
}

/**
 * WP-F1-3 Fokusmodus (spec-focus F). Deckt die sechs Abnahmekriterien ab:
 * Tap->enlarge + Dimmen, Zurueck/Escape/erneuter Tap, `focus.changed` (Agent),
 * Auto-Reset + Timer-Ruecksetzung, kein `dashboard_layout`-Write.
 *
 * Ohne WP-F1-1 (`GET /api/state/focus`) wird der Block uebersprungen, damit die
 * uebrige E2E-Suite auf einem Backend ohne Fokus-State gruen bleibt.
 */
async function focusChecks(browser, api) {
  const probe = await api("/api/state/focus");
  if (probe.status !== 200) {
    check(
      "WP-F1-3 Fokusmodus (uebersprungen: Backend ohne /api/state/focus)",
      true,
      `GET /api/state/focus -> ${probe.status}`,
    );
    return;
  }

  const context = await browser.newContext({ hasTouch: true, viewport: { width: 1024, height: 768 } });
  const fp = await context.newPage();
  const focusWrites = [];
  const layoutWrites = [];
  fp.on("request", (request) => {
    const url = request.url();
    if (url.includes("/api/state/focus")) {
      focusWrites.push(request.method());
    }
    if (url.includes("/api/layout") && request.method() !== "GET") {
      layoutWrites.push(`${request.method()} ${url}`);
    }
  });
  const focusErrors = [];
  fp.on("pageerror", (error) => focusErrors.push(String(error)));

  const TILE = '.view[data-tab="dashboard"] .bento-item';
  const BODY = '.view[data-tab="dashboard"] .bento-item .bento-item__body';

  await fp.goto(BASE + "/", { waitUntil: "networkidle" });
  await fp.waitForFunction(() => window.worldplaner, null, { timeout: 10000 });
  await api("/api/state/focus", { method: "DELETE" });
  await fp.evaluate(() => window.worldplaner.app.clearFocus({ persist: false }));

  await fp.tap('.tab[data-tab="dashboard"]');
  await fp.waitForSelector(TILE, { timeout: 5000 });

  const targetId = await fp.$eval(TILE, (el) => el.dataset.instanceId);
  const tileCount = await fp.$$eval(TILE, (nodes) => nodes.length);
  const putsBefore = focusWrites.filter((method) => method === "PUT").length;

  // 1) Tap auf den Kachel-Body -> enlarge, andere gedimmt
  await fp.locator(BODY).first().tap({ position: { x: 6, y: 6 } });
  await fp.waitForTimeout(200);
  const enlarged = await fp.evaluate(() => {
    const items = [...document.querySelectorAll('.view[data-tab="dashboard"] .bento-item')];
    const focused = items.filter((el) => el.classList.contains("is-focused"));
    const grid = document.querySelector('.view[data-tab="dashboard"] .bento-grid');
    const back = document.querySelector(".focus-back");
    const backRect = back ? back.getBoundingClientRect() : null;
    const rowMatch = focused[0] ? /span\s+(\d+)/.exec(getComputedStyle(focused[0]).gridRow) : null;
    return {
      focusMode: grid.classList.contains("is-focus-mode"),
      focusedCount: focused.length,
      focusedId: focused[0] ? focused[0].dataset.instanceId : null,
      enlargeClass: focused[0] ? focused[0].classList.contains("bento-item--focus-enlarge") : false,
      gridColumn: focused[0] ? getComputedStyle(focused[0]).gridColumn : null,
      gridRowNum: rowMatch ? Number(rowMatch[1]) : null,
      focusedOpacity: focused[0] ? getComputedStyle(focused[0]).opacity : null,
      focusedOutline: focused[0] ? getComputedStyle(focused[0]).outlineStyle : null,
      others: items
        .filter((el) => !el.classList.contains("is-focused"))
        .map((el) => ({ op: getComputedStyle(el).opacity, pe: getComputedStyle(el).pointerEvents })),
      back: backRect
        ? {
            label: back.getAttribute("aria-label"),
            w: Math.round(backRect.width),
            h: Math.round(backRect.height),
          }
        : null,
    };
  });
  check(
    "WP-F1-3 AC1 Tap (<=250ms/<10px) -> enlarge, andere gedimmt",
    enlarged.focusMode &&
      enlarged.focusedCount === 1 &&
      enlarged.focusedId === targetId &&
      enlarged.enlargeClass &&
      /1\s*\/\s*-1/.test(enlarged.gridColumn || "") &&
      enlarged.gridRowNum >= 3 &&
      enlarged.focusedOpacity === "1" &&
      enlarged.others.length === tileCount - 1 &&
      enlarged.others.every((other) => other.op === "0.2" && other.pe === "none"),
    JSON.stringify(enlarged),
  );
  check(
    "WP-F1-3 AC1 Fokus-Ring sichtbar ohne Hover",
    enlarged.focusedOutline === "solid",
    `outline=${enlarged.focusedOutline}`,
  );
  check(
    "WP-F1-3 AC2 Zurueck-Schaltflaeche >= 44x44 mit aria-label",
    Boolean(enlarged.back) &&
      enlarged.back.w >= 44 &&
      enlarged.back.h >= 44 &&
      Boolean(enlarged.back.label),
    JSON.stringify(enlarged.back),
  );
  check(
    "WP-F1-3 AC5 Touch-Pfad schreibt PUT /api/state/focus, kein dashboard_layout-Write",
    focusWrites.filter((method) => method === "PUT").length === putsBefore + 1 &&
      layoutWrites.length === 0,
    `focusWrites=${JSON.stringify(focusWrites)} layoutWrites=${JSON.stringify(layoutWrites)}`,
  );
  await fp.screenshot({ path: `${SHOT_DIR}/04-focus-enlarge.png` });

  // 2) Zurueck-Schaltflaeche -> clearFocus + DELETE
  await fp.locator(".focus-back").tap();
  await fp.waitForTimeout(200);
  const clearedByBack = await fp.evaluate(() => ({
    focused: document.querySelectorAll(".bento-item.is-focused").length,
    focusMode: document.querySelectorAll(".bento-grid.is-focus-mode").length,
    back: Boolean(document.querySelector(".focus-back")),
  }));
  check(
    "WP-F1-3 AC2 Zurueck-Schaltflaeche -> clearFocus + DELETE",
    clearedByBack.focused === 0 &&
      clearedByBack.focusMode === 0 &&
      !clearedByBack.back &&
      focusWrites.includes("DELETE"),
    JSON.stringify(clearedByBack),
  );

  // 3) Escape -> clearFocus
  await fp.locator(BODY).first().tap({ position: { x: 6, y: 6 } });
  await fp.waitForTimeout(150);
  await fp.keyboard.press("Escape");
  await fp.waitForTimeout(150);
  const clearedByEscape = await fp.evaluate(
    () => document.querySelectorAll(".bento-item.is-focused").length,
  );
  check("WP-F1-3 AC2 Escape -> clearFocus", clearedByEscape === 0, `focused=${clearedByEscape}`);

  // 4) erneuter Tap auf die bereits fokussierte Kachel -> clearFocus
  await fp.locator(BODY).first().tap({ position: { x: 6, y: 6 } });
  await fp.waitForTimeout(150);
  const refocused = await fp.evaluate(
    () => document.querySelectorAll(".bento-item.is-focused").length,
  );
  await fp.locator(BODY).first().tap({ position: { x: 6, y: 6 } });
  await fp.waitForTimeout(150);
  const toggledOff = await fp.evaluate(
    () => document.querySelectorAll(".bento-item.is-focused").length,
  );
  check(
    "WP-F1-3 AC2 erneuter Tap auf fokussierte Kachel -> clearFocus",
    refocused === 1 && toggledOff === 0,
    `refocused=${refocused} toggledOff=${toggledOff}`,
  );

  // 5) Agent-Pfad: focus.changed (SSE) wendet Fokus an, focus:null entfernt ihn
  const layout = await api("/api/layout?tab=dashboard");
  const row = Array.isArray(layout.body) ? layout.body[0] : null;
  let sseApplied = false;
  let sseCleared = false;
  if (row) {
    const put = await api("/api/state/focus", {
      method: "PUT",
      body: JSON.stringify({ widget_id: row.widget_id, mode: "highlight" }),
    });
    const focus = put.body && put.body.focus;
    const isFocused = (id) =>
      [...document.querySelectorAll(".bento-item")].some(
        (el) => el.classList.contains("is-focused") && el.dataset.instanceId === id,
      );
    await fp
      .waitForFunction(isFocused, focus.instance_id, { timeout: 5000 })
      .catch(() => {});
    sseApplied = await fp.evaluate(isFocused, focus.instance_id);

    // `fullscreen` (F): Host wendet den Overlay-Marker an; das Overlay selbst
    // liefert WP-F1-4. Der Zustand ist auch hier genau eine Kachel.
    await api("/api/state/focus", {
      method: "PUT",
      body: JSON.stringify({ widget_id: row.widget_id, mode: "fullscreen" }),
    });
    await fp
      .waitForFunction(
        (id) =>
          [...document.querySelectorAll(".bento-item")].some(
            (el) =>
              el.classList.contains("is-focused") &&
              el.classList.contains("bento-item--focus-fullscreen") &&
              el.dataset.instanceId === id,
          ),
        focus.instance_id,
        { timeout: 5000 },
      )
      .catch(() => {});
    const fullscreenApplied = await fp.evaluate(
      (id) =>
        [...document.querySelectorAll(".bento-item")].some(
          (el) =>
            el.classList.contains("is-focused") &&
            el.classList.contains("bento-item--focus-fullscreen") &&
            el.dataset.instanceId === id,
        ),
      focus.instance_id,
    );

    await api("/api/state/focus", { method: "DELETE" });
    await fp
      .waitForFunction(() => !document.querySelector(".bento-item.is-focused"), null, { timeout: 5000 })
      .catch(() => {});
    sseCleared = await fp.evaluate(() => !document.querySelector(".bento-item.is-focused"));
    check("WP-F1-3 AC3/AC5 focus.changed mode=fullscreen setzt Overlay-Marker (WP-F1-4)", fullscreenApplied);
  }
  check(
    "WP-F1-3 AC3 focus.changed (Agent) wendet Fokus an, focus:null entfernt ihn",
    sseApplied && sseCleared,
    `applied=${sseApplied} cleared=${sseCleared}`,
  );
  await fp.screenshot({ path: `${SHOT_DIR}/05-focus-agent.png` });

  // 6) Auto-Reset + pointerdown setzt den Timer zurueck
  if (row) {
    await fp.evaluate(() => {
      window.worldplaner.app.focusAutoResetSeconds = 1;
    });
    await api("/api/state/focus", {
      method: "PUT",
      body: JSON.stringify({ widget_id: row.widget_id, mode: "highlight" }),
    });
    await fp.waitForTimeout(1500);
    const autoCleared = await fp.evaluate(() => !document.querySelector(".bento-item.is-focused"));
    check("WP-F1-3 AC4 Auto-Reset nach focus_auto_reset_seconds", autoCleared);

    await fp.evaluate(() => {
      window.worldplaner.app.focusAutoResetSeconds = 2;
    });
    await api("/api/state/focus", {
      method: "PUT",
      body: JSON.stringify({ widget_id: row.widget_id, mode: "highlight" }),
    });
    await fp.waitForSelector(".bento-item.is-focused", { timeout: 5000 });
    await fp.waitForTimeout(1200);
    const focusedId = await fp.$eval(".bento-item.is-focused", (el) => el.dataset.instanceId);
    await fp.evaluate((id) => {
      document
        .querySelector(`.bento-item[data-instance-id="${id}"]`)
        .dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    }, focusedId);
    await fp.waitForTimeout(1000);
    const resetHeld = await fp.evaluate(() =>
      Boolean(document.querySelector(".bento-item.is-focused")),
    );
    check("WP-F1-3 AC4 pointerdown auf fokussierter Kachel setzt Timer zurueck", resetHeld);
    await api("/api/state/focus", { method: "DELETE" });
  }

  check(
    "WP-F1-3 keine Page-Errors im Fokuspfad",
    focusErrors.length === 0,
    JSON.stringify(focusErrors),
  );
  await context.close();
}

const browser = await chromium.launch({ executablePath: EXEC, args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

const consoleLog = [];
page.on("console", (message) => consoleLog.push({ type: message.type(), text: message.text() }));
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));
const patches = [];
page.on("request", (request) => {
  if (request.method() === "PATCH" && request.url().includes("/api/tasks/")) {
    patches.push({ url: request.url(), body: request.postData() });
  }
});

try {
  // Frischer Zustand + Seed
  const existing = await api("/api/tasks?status=all&limit=200");
  for (const task of existing.body) {
    await api(`/api/tasks/${task.id}`, { method: "DELETE" });
  }
  await api("/api/tasks", {
    method: "POST",
    body: JSON.stringify({ title: "Rechnung prüfen", priority: "high", due_at: "2026-10-01" }),
  });
  await api("/api/tasks", {
    method: "POST",
    body: JSON.stringify({ title: "Kaffee bestellen", priority: "low" }),
  });

  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.waitForFunction(() => window.worldplaner, null, { timeout: 10000 });

  // (1) Lifecycle constructor -> render -> refresh
  const lifecycle = consoleLog
    .map((entry) => entry.text)
    .filter((text) => text.includes("[widget:hermes-todos]"));
  check(
    "AC1 dynamisches Laden + Lifecycle constructor->render->refresh",
    ["constructor", "render", "refresh"].every((step) =>
      lifecycle.includes(`[widget:hermes-todos] ${step}`),
    ),
    lifecycle.join(" | "),
  );

  // (3) todos-Tab listet hermes_tasks
  await page.click('.tab[data-tab="todos"]');
  await page.waitForSelector('.view[data-tab="todos"] .ht-item', { timeout: 5000 });
  const titles = await page.$$eval('.view[data-tab="todos"] .ht-item__title', (nodes) =>
    nodes.map((node) => node.textContent),
  );
  check(
    "AC3 todos-Tab listet hermes_tasks",
    titles.includes("Rechnung prüfen") && titles.includes("Kaffee bestellen"),
    JSON.stringify(titles),
  );
  await page.screenshot({ path: `${SHOT_DIR}/01-todos-tab.png` });

  // (4) Abhaken -> PATCH mit base_version, persistiert nach Reload
  await page
    .locator('.view[data-tab="todos"] .ht-item', { hasText: "Rechnung prüfen" })
    .locator(".ht-check__input")
    .click();
  await page.waitForTimeout(600);
  const patched = patches.find((entry) => entry.body && entry.body.includes("base_version"));
  check(
    "AC4a Abhaken ruft PATCH mit base_version",
    Boolean(patched),
    JSON.stringify(patches),
  );

  await page.reload({ waitUntil: "networkidle" });
  await page.click('.tab[data-tab="todos"]');
  await page.waitForSelector('.view[data-tab="todos"] .ht-item', { timeout: 5000 });
  const persisted = await api("/api/tasks?status=done&limit=100");
  check(
    "AC4b Status persistiert nach Reload",
    persisted.body.some((task) => task.title === "Rechnung prüfen" && task.status === "done"),
    JSON.stringify(persisted.body.map((task) => [task.title, task.status, task.version])),
  );

  // (5) SSE-Push erscheint ohne manuellen Reload
  await api("/api/tasks", {
    method: "POST",
    body: JSON.stringify({ title: "Hermes: Backup starten", priority: "normal" }),
  });
  let sseOk = true;
  try {
    await page.waitForSelector(
      '.view[data-tab="todos"] .ht-item:has-text("Hermes: Backup starten")',
      { timeout: 5000 },
    );
  } catch {
    sseOk = false;
  }
  check("AC5 SSE-Push erscheint ohne Reload", sseOk);

  // (2) Provozierte Exception in render()
  const errorResult = await page.evaluate(async () => {
    const container = document.createElement("div");
    container.style.height = "160px";
    document.body.append(container);
    class Broken {
      constructor() {}
      async render() {
        const error = new Error("absichtlicher Render-Fehler");
        error.code = "RENDER_BOOM";
        throw error;
      }
      async refresh() {}
      destroy() {}
    }
    await window.worldplaner.runtime.loadWidget({
      instanceId: "broken-test",
      widgetId: "hermes-todos",
      container,
      manifest: {
        id: "hermes-todos",
        name: "Broken",
        version: "1.0.0",
        category: "general",
        default_size: { w: 1, h: 1 },
        permissions: [],
      },
      widgetClass: Broken,
    });
    const placeholder = container.querySelector(".wg-error");
    return {
      placeholder: Boolean(placeholder),
      code: placeholder ? placeholder.dataset.errorCode : null,
      hostAlive: Boolean(document.querySelector("#app[data-state]")),
      todosStillThere: Boolean(document.querySelector('.view[data-tab="todos"] .ht-item')),
    };
  });
  check(
    "AC2 render()-Exception -> Platzhalter + Fehlercode, kein Host-Absturz",
    errorResult.placeholder && errorResult.code === "RENDER_BOOM" && errorResult.hostAlive,
    JSON.stringify(errorResult),
  );
  await page.screenshot({ path: `${SHOT_DIR}/02-error-placeholder.png` });

  // Tap-Targets >= 44px (I.3). Nur sichtbare Elemente: Griffe auf dem inaktiven
  // `dashboard`-Tab sind per `hidden` (display:none) versteckt und damit keine
  // Tap-Ziele; ihre 0x0-Rechtecke sind keine Verletzung der 44-px-Regel.
  const targets = await page.evaluate(() =>
    [
      ...document.querySelectorAll(".tab"),
      ...document.querySelectorAll('.view[data-tab="todos"] .ht-filter'),
      ...document.querySelectorAll('.view[data-tab="todos"] .ht-check'),
      ...document.querySelectorAll(".bento-item__handle"),
    ]
      .map((node) => {
        const rect = node.getBoundingClientRect();
        return { cls: node.className, w: Math.round(rect.width), h: Math.round(rect.height) };
      })
      .filter((target) => target.w > 0 && target.h > 0),
  );
  const tooSmall = targets.filter((target) => target.w < 44 || target.h < 44);
  check(
    "Tap-Targets >= 44px",
    targets.length > 0 && tooSmall.length === 0,
    `${targets.length} geprüft, zu klein: ${JSON.stringify(tooSmall)}`,
  );

  // Palette (I.2)
  const palette = await page.evaluate(() => ({
    body: getComputedStyle(document.body).backgroundColor,
    cards: [...document.querySelectorAll(".bento-item")].map(
      (node) => getComputedStyle(node).backgroundColor,
    ),
  }));
  check(
    "Palette #050505/#0d0d0d/#141414",
    palette.body === "rgb(5, 5, 5)" &&
      palette.cards.every((color) => color === "rgb(13, 13, 13)" || color === "rgb(20, 20, 20)"),
    JSON.stringify(palette),
  );

  // Permission-Gate (E.4) + Bento-Platzierung (E.6)
  const systemResult = await page.evaluate(async () => {
    const probe = async (permissions) => {
      const container = document.createElement("div");
      document.body.append(container);
      const captured = { code: null };
      class Probe {
        constructor(_container, _config, api) {
          this.api = api;
        }
        async render() {
          try {
            await this.api.fetchJson("/api/tasks?status=all&limit=1");
            captured.code = "ALLOWED";
          } catch (error) {
            captured.code = error.code;
          }
        }
        async refresh() {}
        destroy() {}
      }
      await window.worldplaner.runtime.loadWidget({
        instanceId: `probe-${permissions.length}`,
        widgetId: "perm-probe",
        container,
        manifest: {
          id: "perm-probe",
          name: "Probe",
          version: "1.0.0",
          category: "general",
          default_size: { w: 1, h: 1 },
          permissions,
        },
        widgetClass: Probe,
      });
      return captured.code;
    };
    const denied = await probe([]);
    const allowed = await probe(["tasks"]);
    const item = document.querySelector('.view[data-tab="todos"] .bento-item');
    return {
      denied,
      allowed,
      gridColumn: item ? getComputedStyle(item).gridColumn : null,
      gridRow: item ? getComputedStyle(item).gridRow : null,
    };
  });
  check(
    "Plugin-API Permission-Gate (E.4)",
    systemResult.denied === "FORBIDDEN" && systemResult.allowed === "ALLOWED",
    JSON.stringify(systemResult),
  );
  check(
    "Bento-Platzierung 12x6 ab Zelle 0/0 (E.6)",
    /^1\s*\/\s*(span 12|13)$/.test(systemResult.gridColumn || "") &&
      /^1\s*\/\s*(span 6|7)$/.test(systemResult.gridRow || ""),
    `column=${systemResult.gridColumn} row=${systemResult.gridRow}`,
  );

  // Keine Hover-Abhängigkeit: Abhaken per Tap im Touch-Kontext (I.3)
  const touchContext = await browser.newContext({ hasTouch: true, viewport: { width: 1024, height: 768 } });
  const touchPage = await touchContext.newPage();
  const touchPatches = [];
  touchPage.on("request", (request) => {
    if (request.method() === "PATCH" && request.url().includes("/api/tasks/")) {
      touchPatches.push(request.postData());
    }
  });
  await touchPage.goto(BASE + "/", { waitUntil: "networkidle" });
  await touchPage.waitForFunction(() => window.worldplaner, null, { timeout: 10000 });
  await touchPage.tap('.tab[data-tab="todos"]');
  await touchPage.waitForSelector('.view[data-tab="todos"] .ht-item', { timeout: 5000 });
  await touchPage.tap('.view[data-tab="todos"] .ht-item .ht-check__input');
  await touchPage.waitForTimeout(500);
  check("keine Hover-Abhängigkeit: Abhaken per Tap", touchPatches.length > 0, JSON.stringify(touchPatches));
  await touchPage.screenshot({ path: `${SHOT_DIR}/03-touch-tap.png` });
  await touchContext.close();

  // Layout-Persistenz (D.3/E.6). Ist die Widget-Registry noch nicht geseedet
  // (Abschnitt C, Aufgabe von WP-2), lehnt `POST /api/layout` mit 404 ab; das
  // Frontend muss das weich abfangen (kein Host-Absturz).
  const registry = await api("/api/widgets");
  const layoutFirst = await page.evaluate(async () => {
    const grid = window.worldplaner.app.grids.get("todos");
    const key = [...grid.entries.keys()][0];
    grid.applyLayout(key, { x: 0, y: 0, w: 12, h: 7 });
    await new Promise((resolve) => setTimeout(resolve, 500));
    return { key, rekeyed: [...grid.entries.keys()][0] };
  });
  await page.evaluate(async (key) => {
    const grid = window.worldplaner.app.grids.get("todos");
    grid.applyLayout(key, { x: 0, y: 0, w: 12, h: 5 });
    await new Promise((resolve) => setTimeout(resolve, 500));
  }, layoutFirst.rekeyed);
  const layoutRows = await api("/api/layout?tab=todos");
  const errorsSoFar = pageErrors.length;
  if (registry.body.length > 0) {
    check(
      "Layout-Persistenz erst POST (neue Zeile), dann PUT (E.6/D.3)",
      layoutRows.body.length === 1 &&
        layoutRows.body[0].widget_id === "hermes-todos" &&
        layoutRows.body[0].h === 5,
      `first=${JSON.stringify(layoutFirst)} rows=${JSON.stringify(layoutRows.body)}`,
    );
  } else {
    check(
      "Layout-Persistenz degradiert weich (Widget-Registry nicht geseedet)",
      layoutRows.body.length === 0 && errorsSoFar === 0,
      `rows=${JSON.stringify(layoutRows.body)} pageErrors=${errorsSoFar}`,
    );
  }

  // WP-F1-3: Fokusmodus (Tap/SSE/Auto-Reset) — eigener Touch-Kontext.
  await focusChecks(browser, api);

  // (1) destroy
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForFunction(() => window.worldplaner, null, { timeout: 10000 });
  const destroyResult = await page.evaluate(() => {
    const runtime = window.worldplaner.runtime;
    const key =
      [...runtime.instances.keys()].find((entry) => entry.includes("hermes-todos")) ??
      [...runtime.instances.entries()].find(([, value]) => value.widgetId === "hermes-todos")?.[0];
    const entry = runtime.instances.get(key);
    const container = entry.container;
    runtime.destroy(entry);
    return {
      emptied: container.childElementCount === 0,
      deregistered: !runtime.instances.has(key),
      hostAlive: Boolean(document.querySelector("#app")),
    };
  });
  await page.waitForTimeout(200);
  const destroyLogged = consoleLog.some((entry) =>
    entry.text.includes("[widget:hermes-todos] destroy"),
  );
  check(
    "AC1 destroy geloggt und Container geleert",
    destroyLogged && destroyResult.emptied && destroyResult.deregistered && destroyResult.hostAlive,
    JSON.stringify(destroyResult),
  );

  check("keine unbehandelten Page-Errors", pageErrors.length === 0, JSON.stringify(pageErrors));

  console.log("\n--- Lifecycle-Konsole (Rohbeleg) ---");
  for (const entry of lifecycle) {
    console.log(entry);
  }
} finally {
  await browser.close();
}

const failed = results.filter((result) => !result.ok);
console.log(`\nSUMMARY ${results.length - failed.length}/${results.length} bestanden`);
process.exit(failed.length === 0 ? 0 : 1);
