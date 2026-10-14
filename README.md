# worldplaner — Kiosk-Dashboard-MVP

Lokales, berührungslos bedienbares Kiosk-Dashboard (Wetter, Garmin-Gesundheitsdaten,
Hermes-To-Dos) für ein Dell-Tablet. Grundlage ist die build-reife Spezifikation
`spec` Rev 2 (Abschnitte A–L).

Dieser Stand umfasst **K1 / WP-2** (Docker Compose, SQLite-Schema, FastAPI-Backend,
Abschnitte B, C, D, H), **K4** (Betriebsartefakte, Abschnitt J),
**K2 / WP-3 + WP-7** (Widget-Runtime und Hermes-To-Do-Tab, Abschnitte E, I),
**WP-4** (Wetter-Widget, D.6/E),
**WP-6** (Garmin-Widgets Schlaf/Readiness/Workout/Vitalwerte, D.5/G.5/E),
**K3 / WP-8** (MCP-Server über Streamable HTTP, Abschnitt F) und
**WP-9** (Widget-Verwaltung: Aktivieren/Deaktivieren, Abschnitt I.3/D.2).

## Umfang dieses Stands

Enthalten:

- `compose.yaml` — die Services `backend` (liefert Frontend + Widgets aus, D10),
  `mcp-server` (Streamable HTTP, F) und `garmin-sync` (WP-5)
- `.env.example` — Env-Template
- `backend/` — FastAPI-App, SQLite-Schema, REST-API, Tests
- `frontend/` — App-Shell, Bento-Grid, Widget-Runtime, REST-Client
- `widgets/hermes-todos/` — Hermes-To-Do-Widget
- `widgets/weather/` — Wetter-Widget (WP-4)
- `widgets/garmin-sleep/`, `widgets/garmin-readiness/`,
  `widgets/garmin-workout/`, `widgets/garmin-vitals/` — Garmin-Widgets (WP-6)
- `garmin-sync/` — periodischer Garmin-Abruf in `garmin_cache` (WP-5)
- `mcp-server/` — MCP-Server über Streamable HTTP mit den 8 MVP-Tools (F)
- `deploy/` — systemd-Units und Display-Skripte
- `docs/setup.md` — Setup-Checkliste

Noch **nicht** enthalten: Zip-/Git-Installation von Widgets (Phase 2, eigener
Endpunkt) — die Verwaltung beschränkt sich auf Aktivieren/Deaktivieren und
Config.

## Voraussetzungen

- Docker mit Compose (für den Stack)
- Python ≥ 3.12 (nur für Tests außerhalb von Docker)

## Start

```bash
cp .env.example .env        # optional: ohne .env gelten die Defaults
docker compose up -d
curl -s http://localhost:8000/api/health
# -> {"status":"ok","db":"ok"}
```

Die SQLite-Datei liegt im Volume `sqlite-data` unter `/data/dashboard.db`.
Das Schema wird beim Start idempotent angelegt; die drei Tabs
(`dashboard`, `todos`, `widgets`) werden beim Erststart geseedet. Ebenso
registriert der Start die eingebauten Widgets aus `widgets/*/manifest.json`
(Upsert per `id`, `is_builtin=1`) und legt aus ihren `default_size`-Angaben ein
initiales `dashboard_layout` an (Abschnitt C). Der Widget-Ordner lässt sich per
`WIDGETS_DIR` überschreiben (Default: Repo-/Container-Wurzel/`widgets`).

## Tests

```bash
cd backend
pip install -e ".[dev]"
pytest
```

## REST-API (Vertrag, Spezifikation D)

| Methode | Pfad | Kurzbeschreibung |
|---|---|---|
| GET | `/api/health` | Health + DB-Status |
| GET | `/api/config` | Grid, Tabs, aktiver Tab |
| GET | `/api/widgets` | installierte Widgets |
| GET | `/api/widgets/{id}` | Manifest + Config |
| PATCH | `/api/widgets/{id}` | `is_enabled` / `config` ändern |
| GET | `/api/layout?tab=` | Bento-Instanzen |
| POST/PUT/DELETE | `/api/layout[/{id}]` | Layout-Instanz anlegen/ändern/löschen |
| GET/POST | `/api/tasks` | To-Dos lesen/anlegen |
| PATCH/DELETE | `/api/tasks/{id}` | To-Do ändern/löschen |
| GET | `/api/garmin/{sleep,readiness,workouts,vitals}` | Garmin-Cache (read-only) |
| GET | `/api/weather/{current,forecast}` | Wetter (Open-Meteo, gecacht) |
| GET/PUT | `/api/state/{key}` | Dashboard-Zustand |
| GET | `/api/events` | SSE-Ereignisstrom |

Fehlerformat einheitlich: `{"error":{"code":"…","message":"…","retryable":false}}`.

## Frontend & Widgets (K2 / WP-3 + WP-7)

Das Backend liefert Frontend und Widgets unter derselben Origin aus (D10):
`/` → `frontend/`, `/widgets/<id>/…` → `widgets/<id>/`.

- `frontend/js/widget-runtime.js` — Plugin-Host: dynamisches Laden per
  `import()`, Lifecycle `constructor → render → (refresh)* → destroy`,
  Fehlervertrag (Platzhalter + Fehlercode statt Host-Absturz), Plugin-API mit
  Permission-Gate (E.4/E.5).
- `frontend/js/bento.js` — Bento-Grid (12 Spalten, 96 px Zeilenhöhe, 8 px Gap,
  Touch-Verschieben per Long-Press).
- `frontend/js/app.js` — Bootstrap, Tab-Routing, SSE-Client mit Backoff (D.8) und
  die Widget-Verwaltung im `widgets`-Tab (WP-9): listet `GET /api/widgets` und
  schaltet `is_enabled` über `PATCH /api/widgets/{id}` persistent um; ein
  deaktiviertes Widget wird im `dashboard`-Tab nicht mehr gerendert.
- `widgets/hermes-todos/` — To-Do-Liste mit Abhaken (`PATCH /api/tasks/{id}`
  inkl. `base_version`) und Live-Aktualisierung per SSE (WP-7).
- `widgets/weather/` — Wetter-Kachel (`GET /api/weather/current`: Temperatur,
  Zustand, Stand, Wohlfühlbereich als Ziel), Polling alle 10 min (WP-4).
- `widgets/garmin-{sleep,readiness,workout,vitals}/` — Garmin-Kacheln aus dem
  Cache (`GET /api/garmin/*`: Schlafdauer+Score, Body Battery+Stress+Ruhepuls,
  Workout-Liste, Schritte+Vitalwerte), jew. mit Ziel/Kontext, Polling alle
  5 min (WP-6, D.5/G.5).

### Tests (Frontend)

```bash
cd frontend
npm install
npm test        # Unit-Tests der Grid-Engine (node --test)

# E2E gegen den laufenden Stack:
BASE_URL=http://127.0.0.1:8000 CHROMIUM_PATH=/pfad/zu/chrome npm run test:e2e
# WP-9 Widget-Verwaltung (benötigt geseedete Registry, MARAAA-16):
BASE_URL=http://127.0.0.1:8000 CHROMIUM_PATH=/pfad/zu/chrome npm run test:e2e:admin
```

## MCP-Server (K3 / WP-8, Spezifikation F)

Der MCP-Server läuft über **Streamable HTTP**; einziger Endpunkt ist `POST /mcp`
(`GET /mcp` für den Server-Stream), dazu `GET /healthz` für den Healthcheck.
Er ist Teil von `docker compose` (Service `mcp-server`, Port `127.0.0.1:8001`).

Die 8 Tools (Schemas wörtlich aus F.2):

| Tool | Art |
|---|---|
| `dashboard_status` | read — Gesamtzustand |
| `task_list` | read — max. 200 To-Dos |
| `task_create` | write — legt To-Do an (`source=hermes`) |
| `task_update` | write — überschreibt bedingungslos („Hermes gewinnt") |
| `task_delete` | write — verlangt `confirm:true` |
| `screen_off` / `screen_on` / `screen_dim` | write — `xset`/`xrandr` (J.2) |

Verbindlich: Task-CRUD wird an die Backend-REST-API delegiert (D3) — der
MCP-Server schreibt **nie** direkt in SQLite. Jede Antwort ist
`{ok:true, …}` oder `{ok:false, error:{code,message,retryable}}`, niemals eine
Exception. Bei fehlendem X11 liefern die Display-Tools `DISPLAY_UNAVAILABLE`.

Sicherheit (F.1): DNS-Rebinding-Schutz mit Host-Allowlist, Token-Bucket-Rate-Limit
(`MCP_RATE_LIMIT_PER_MINUTE`) und Cloudflare-Access-Prüfung des
`Cf-Access-Jwt-Assertion` über `CF_ACCESS_AUD` + `CF_ACCESS_TEAM_DOMAIN`.
`MCP_AUTH_MODE` steuert das: `auto` (prüfen, sobald konfiguriert — MVP-Default),
`enforce` (erzwingen) oder `edge` (bewusst an den Edge delegieren).

```bash
# Tests
cd mcp-server && pip install -e ".[dev]" && pytest

# Rohbeleg/Inspector-Ersatz gegen einen laufenden Server:
MCP_URL=http://127.0.0.1:8001/mcp python tests/inspect_client.py
```

## Garmin-Sync (WP-5 / B2, Spezifikation G)

`garmin-sync` ruft periodisch die Garmin-Gesundheitsdaten ab, normalisiert sie auf
das verbindliche Payload-Schema (G.5) und schreibt sie nach `garmin_cache` —
**einziger Schreiber** dieser Tabelle (A.2/D4); das Backend liest ausschließlich
(`GET /api/garmin/{sleep,readiness,workouts,vitals}`).

- Intervall, Backoff und Token-Verzeichnis aus der Umgebung (G.2/H.1); die
  Session wird im Named Volume `garmin-token:/data/token` persistiert (G.3).
- Fehlerverhalten (G.4): Netz/5xx → exponentieller Backoff `base * 2^attempt`,
  gedeckelt auf `min(GARMIN_BACKOFF_CAP_SECONDS, GARMIN_SYNC_INTERVAL_SECONDS)`;
  Auth/401 → genau ein Log-Eintrag, dann bis zum nächsten Intervall (kein Hammering).
- Ohne Credentials/Token startet der Container und loggt verständlich, ohne Crash.
- Der einmalige Garmin-Login inkl. MFA ist Auftraggeber-Setup (L.3.3, `docs/setup.md`).
  Beim Erst-Login muss das Token-Verzeichnis mitgegeben werden, damit die Session
  im Volume `garmin-token` landet und der Sync danach unbeaufsichtigt läuft:

  ```bash
  docker compose run --rm garmin-sync python -c \
    "from garminconnect import Garmin; import os; g=Garmin(os.environ['GARMIN_EMAIL'], os.environ['GARMIN_PASSWORD']); g.login(os.environ['GARMIN_TOKEN_DIR']); print('login ok')"
  ```

```bash
# Tests (Payload-Schema G.5, Backoff/Auth, No-Credentials-Pfad)
cd garmin-sync && pip install -e ".[dev]" && pytest

# Verifikation ohne echten Garmin-Account (schreibt synthetische Cache-Zeilen):
GARMIN_SYNC_MOCK=1 docker compose up -d garmin-sync
docker compose exec backend python -c \
  "import sqlite3;print(sqlite3.connect('/data/dashboard.db').execute('select kind,date,fetched_at from garmin_cache').fetchall())"
```
