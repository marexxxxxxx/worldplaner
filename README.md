# worldplaner — Kiosk-Dashboard-MVP

Lokales, berührungslos bedienbares Kiosk-Dashboard (Wetter, Garmin-Gesundheitsdaten,
Hermes-To-Dos) für ein Dell-Tablet. Grundlage ist die build-reife Spezifikation
`spec` Rev 2 (Abschnitte A–L).

Dieser Stand umfasst **K1 / WP-2** (Docker Compose, SQLite-Schema, FastAPI-Backend,
Abschnitte B, C, D, H), **K4** (Betriebsartefakte, Abschnitt J),
**K2 / WP-3 + WP-7** (Widget-Runtime und Hermes-To-Do-Tab, Abschnitte E, I) und
**K3 / WP-8** (MCP-Server über Streamable HTTP, Abschnitt F).

## Umfang dieses Stands

Enthalten:

- `compose.yaml` — die Services `backend` (liefert Frontend + Widgets aus, D10)
  und `mcp-server` (Streamable HTTP, F)
- `.env.example` — Env-Template
- `backend/` — FastAPI-App, SQLite-Schema, REST-API, Tests
- `frontend/` — App-Shell, Bento-Grid, Widget-Runtime, REST-Client
- `widgets/hermes-todos/` — Hermes-To-Do-Widget
- `mcp-server/` — MCP-Server über Streamable HTTP mit den 8 MVP-Tools (F)
- `deploy/` — systemd-Units und Display-Skripte
- `docs/setup.md` — Setup-Checkliste

Noch **nicht** enthalten (eigene Work-Packages): Wetter-Widget (WP-4),
Garmin-Widgets (WP-6), `garmin-sync/` (WP-5), Widget-Verwaltung (WP-9).

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
(`dashboard`, `todos`, `widgets`) werden beim Erststart geseedet.

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
- `frontend/js/app.js` — Bootstrap, Tab-Routing, SSE-Client mit Backoff (D.8).
- `widgets/hermes-todos/` — To-Do-Liste mit Abhaken (`PATCH /api/tasks/{id}`
  inkl. `base_version`) und Live-Aktualisierung per SSE (WP-7).

### Tests (Frontend)

```bash
cd frontend
npm install
npm test        # Unit-Tests der Grid-Engine (node --test)

# E2E gegen den laufenden Stack:
BASE_URL=http://127.0.0.1:8000 CHROMIUM_PATH=/pfad/zu/chrome npm run test:e2e
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
