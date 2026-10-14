/**
 * E2E-Abnahme WP-9 (MARAAA-22) — Widget-Verwaltung im `widgets`-Tab.
 *
 * Voraussetzungen (wie e2e.mjs):
 *   - laufendes Backend mit ausgeliefertem Frontend (D10),
 *     z. B. `docker compose up -d` -> http://127.0.0.1:8000
 *   - geseedete Widget-Registry (MARAAA-16), sonst bleibt `GET /api/widgets` leer
 *   - Chromium: `CHROMIUM_PATH=/pfad/zu/chrome` oder Playwright-verwaltet
 *
 * Aufruf:  BASE_URL=http://127.0.0.1:8000 npm run test:e2e:admin
 *
 * Deckt die binären Abnahmekriterien ab:
 *   (1) `widgets`-Tab listet alle Zeilen aus `GET /api/widgets`
 *       (Name, Version, Kategorie, Zustand)
 *   (2) Umschalten ruft `PATCH /api/widgets/{id}`; Zustand persistiert nach Reload
 *   (3) deaktiviertes Widget verschwindet aus dem `dashboard`-Tab,
 *       Reaktivieren stellt es wieder her
 *   (4) Tap-Ziele >= 44x44 px, keine Hover-Abhängigkeit (I.3)
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";
const EXEC = process.env.CHROMIUM_PATH || undefined;
const SHOT_DIR = process.env.SHOT_DIR || new URL("./screenshots/", import.meta.url).pathname;
const TARGET = process.env.WP9_TARGET_WIDGET || "weather";

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

const registry = (await api("/api/widgets")).body;
if (!Array.isArray(registry) || registry.length === 0) {
  console.log("SKIP  Widget-Registry leer (MARAAA-16 nicht geseedet) — WP-9-E2E benötigt Seed.");
  process.exit(0);
}

const browser = await chromium.launch({ executablePath: EXEC, args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));
const patches = [];
page.on("request", (request) => {
  if (request.method() === "PATCH" && request.url().includes("/api/widgets/")) {
    patches.push({ url: request.url(), body: request.postData() });
  }
});

/** Liest die Verwaltungszeilen als Objekte aus der aktuell sichtbaren Ansicht. */
async function readAdminRows() {
  return page.$$eval('.view[data-tab="widgets"] .widget-admin__item', (nodes) =>
    nodes.map((node) => {
      const toggle = node.querySelector(".widget-admin__toggle");
      return {
        id: node.dataset.widgetId,
        name: node.querySelector(".widget-admin__name")?.textContent ?? "",
        meta: node.querySelector(".widget-admin__meta")?.textContent ?? "",
        checked: toggle?.getAttribute("aria-checked") ?? null,
        label: toggle?.textContent ?? "",
      };
    }),
  );
}

async function openWidgetsTab() {
  await page.click('.tab[data-tab="widgets"]');
  await page.waitForSelector('.view[data-tab="widgets"] .widget-admin__item', { timeout: 5000 });
}

async function openDashboardTab() {
  await page.click('.tab[data-tab="dashboard"]');
  await page.waitForSelector('.view[data-tab="dashboard"]', { timeout: 5000 });
}

async function dashboardCount(widgetId) {
  return page.locator(`.view[data-tab="dashboard"] .bento-item[data-widget-id="${widgetId}"]`).count();
}

try {
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.waitForFunction(() => window.worldplaner, null, { timeout: 10000 });

  // (3a) Ausgangslage: Ziel-Widget ist auf dem dashboard-Tab gerendert.
  const before = await dashboardCount(TARGET);
  check(`AC3a dashboard rendert „${TARGET}" vor dem Deaktivieren`, before === 1, `count=${before}`);

  // (1) widgets-Tab listet alle Registry-Zeilen mit Name, Version, Kategorie, Zustand
  await openWidgetsTab();
  const rows = await readAdminRows();
  const byId = new Map(rows.map((row) => [row.id, row]));
  const missing = registry.filter((entry) => !byId.has(entry.id));
  const complete = registry.every((entry) => {
    const row = byId.get(entry.id);
    if (!row) return false;
    const hasVersion = row.meta.includes(`v${entry.version}`);
    const hasEnabled = row.checked === (entry.is_enabled ? "true" : "false");
    return Boolean(row.name) && hasVersion && hasEnabled;
  });
  check(
    "AC1 widgets-Tab listet alle GET /api/widgets-Zeilen (Name, Version, Kategorie, Zustand)",
    rows.length === registry.length && missing.length === 0 && complete,
    `rows=${rows.length}/${registry.length} missing=${JSON.stringify(missing.map((e) => e.id))}`,
  );
  await page.screenshot({ path: `${SHOT_DIR}/10-widget-admin.png` });

  // (4a) Tap-Ziele >= 44x44 px innerhalb der Verwaltungsliste
  const targets = await page.$$eval('.view[data-tab="widgets"] .widget-admin__toggle', (nodes) =>
    nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      return { w: Math.round(rect.width), h: Math.round(rect.height) };
    }),
  );
  const tooSmall = targets.filter((target) => target.w < 44 || target.h < 44);
  check(
    "AC4a Tap-Ziele der Schalter >= 44x44 px",
    targets.length === registry.length && tooSmall.length === 0,
    `${targets.length} geprüft, zu klein: ${JSON.stringify(tooSmall)}`,
  );

  // (2a) Deaktivieren ruft PATCH /api/widgets/{id}
  await page
    .locator(`.view[data-tab="widgets"] .widget-admin__item[data-widget-id="${TARGET}"] .widget-admin__toggle`)
    .click();
  await page.waitForFunction(
    (widgetId) =>
      document
        .querySelector(`.widget-admin__item[data-widget-id="${widgetId}"] .widget-admin__toggle`)
        ?.getAttribute("aria-checked") === "false",
    TARGET,
    { timeout: 5000 },
  );
  const offlinePatch = patches.find(
    (entry) => entry.url.includes(`/api/widgets/${TARGET}`) && entry.body?.includes('"is_enabled":false'),
  );
  check("AC2a Deaktivieren ruft PATCH /api/widgets/{id} mit is_enabled=false", Boolean(offlinePatch), JSON.stringify(patches));

  // (3b) Deaktiviertes Widget wird auf dem dashboard-Tab nicht mehr gerendert
  await openDashboardTab();
  const afterDisable = await dashboardCount(TARGET);
  check(`AC3b „${TARGET}" verschwindet aus dem dashboard-Tab`, afterDisable === 0, `count=${afterDisable}`);
  await page.screenshot({ path: `${SHOT_DIR}/11-dashboard-ohne-widget.png` });

  // (2b) Zustand persistiert nach Reload
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForFunction(() => window.worldplaner, null, { timeout: 10000 });
  await openWidgetsTab();
  const persistedRows = await readAdminRows();
  const persisted = persistedRows.find((row) => row.id === TARGET);
  check(
    "AC2b Zustand ist nach Reload unverändert (persistiert)",
    persisted?.checked === "false" && persisted?.label === "Deaktiviert",
    JSON.stringify(persisted),
  );
  const serverAfterDisable = (await api("/api/widgets")).body.find((entry) => entry.id === TARGET);
  check(
    "AC2b Backend meldet is_enabled=false",
    serverAfterDisable?.is_enabled === false,
    JSON.stringify(serverAfterDisable),
  );

  // (3c) Reaktivieren stellt das Widget wieder her
  await page
    .locator(`.view[data-tab="widgets"] .widget-admin__item[data-widget-id="${TARGET}"] .widget-admin__toggle`)
    .click();
  await page.waitForFunction(
    (widgetId) =>
      document
        .querySelector(`.widget-admin__item[data-widget-id="${widgetId}"] .widget-admin__toggle`)
        ?.getAttribute("aria-checked") === "true",
    TARGET,
    { timeout: 5000 },
  );
  const restorePatch = patches.find(
    (entry) => entry.url.includes(`/api/widgets/${TARGET}`) && entry.body?.includes('"is_enabled":true'),
  );
  check("AC2a Reaktivieren ruft PATCH mit is_enabled=true", Boolean(restorePatch), JSON.stringify(patches));

  await openDashboardTab();
  const afterEnable = await dashboardCount(TARGET);
  check(`AC3c Reaktivieren stellt „${TARGET}" wieder her`, afterEnable === 1, `count=${afterEnable}`);

  // (4b) Keine Hover-Abhängigkeit: Umschalten per Tap im Touch-Kontext
  const touchContext = await browser.newContext({
    hasTouch: true,
    viewport: { width: 1024, height: 768 },
  });
  const touchPage = await touchContext.newPage();
  const touchPatches = [];
  touchPage.on("request", (request) => {
    if (request.method() === "PATCH" && request.url().includes("/api/widgets/")) {
      touchPatches.push(request.postData());
    }
  });
  await touchPage.goto(BASE + "/", { waitUntil: "networkidle" });
  await touchPage.waitForFunction(() => window.worldplaner, null, { timeout: 10000 });
  await touchPage.tap('.tab[data-tab="widgets"]');
  await touchPage.waitForSelector('.view[data-tab="widgets"] .widget-admin__item', { timeout: 5000 });
  const touchItem = touchPage.locator(
    `.view[data-tab="widgets"] .widget-admin__item[data-widget-id="${TARGET}"] .widget-admin__toggle`,
  );
  await touchItem.tap();
  await touchPage.waitForFunction(
    (widgetId) =>
      document
        .querySelector(`.widget-admin__item[data-widget-id="${widgetId}"] .widget-admin__toggle`)
        ?.getAttribute("aria-checked") === "false",
    TARGET,
    { timeout: 5000 },
  );
  check("AC4b Umschalten per Tap (Touch-Kontext), keine Hover-Abhängigkeit", touchPatches.length > 0, JSON.stringify(touchPatches));
  // Zustand wiederherstellen, damit der Lauf den Stack wie vorgefunden hinterlässt.
  await touchItem.tap();
  await touchPage.waitForTimeout(600);
  await touchContext.close();

  const restored = (await api("/api/widgets")).body.find((entry) => entry.id === TARGET);
  check("Nachbereinigung: Ziel-Widget wieder aktiv", restored?.is_enabled === true, JSON.stringify(restored));

  check("keine unbehandelten Page-Errors", pageErrors.length === 0, JSON.stringify(pageErrors));
} finally {
  await browser.close();
}

const failed = results.filter((result) => !result.ok);
console.log(`\nSUMMARY ${results.length - failed.length}/${results.length} bestanden`);
process.exit(failed.length === 0 ? 0 : 1);
