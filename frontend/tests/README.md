# Frontend-Tests

| Datei | Art | Abhängigkeit |
|---|---|---|
| `bento.test.mjs` | Unit-Test der Bento-Grid-Engine (E.6) | keine (`node --test`) |
| `e2e.mjs` | Browser-E2E gegen den laufenden Stack (WP-3 + WP-7) | `playwright-core` + Chromium |
| `e2e-widget-admin.mjs` | Browser-E2E Widget-Verwaltung (WP-9 / MARAAA-22) | `playwright-core` + Chromium |

```bash
cd frontend
npm install                 # holt playwright-core (nur für Tests)
npm test                    # Unit-Tests, kein Server nötig

# E2E: Stack muss laufen (docker compose up -d) und das Frontend ausliefern (D10)
CHROMIUM_PATH=/pfad/zu/chrome BASE_URL=http://127.0.0.1:8000 npm run test:e2e
CHROMIUM_PATH=/pfad/zu/chrome BASE_URL=http://127.0.0.1:8000 npm run test:e2e:admin
```

`e2e-widget-admin.mjs` prüft die binären Abnahmekriterien von WP-9 und setzt
eine geseedete Widget-Registry voraus (MARAAA-16; sonst bricht der Lauf mit
`SKIP` ab):

1. `widgets`-Tab listet alle Zeilen aus `GET /api/widgets` (Name, Version,
   Kategorie, Zustand)
2. Umschalten ruft `PATCH /api/widgets/{id}`; Zustand persistiert nach Reload
3. deaktiviertes Widget verschwindet aus dem `dashboard`-Tab, Reaktivieren
   stellt es wieder her
4. Tap-Ziele ≥ 44 px, Umschalten auch per Tap (kein Hover)

`e2e.mjs` prüft die binären Abnahmekriterien und legt Screenshots in
`tests/screenshots/` ab:

1. dynamisches Laden + Lifecycle `constructor -> render -> refresh -> destroy`
2. provozierte `render()`-Exception → Platzhalter + Fehlercode, kein Host-Absturz
3. `todos`-Tab listet `hermes_tasks`
4. Abhaken ruft `PATCH /api/tasks/{id}` mit `base_version`, Persistenz nach Reload
5. SSE-Push erscheint ohne manuellen Reload
6. zusätzlich: Tap-Targets ≥ 44 px, Palette, Plugin-API-Permission-Gate, Bento-Platzierung

Ohne `CHROMIUM_PATH` versucht `playwright-core`, einen verwalteten Browser zu
finden; alternativ `npx playwright install chromium`.
