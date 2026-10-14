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
