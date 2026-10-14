# worldplaner — Kiosk-Dashboard-MVP

Lokales, berührungslos bedienbares Kiosk-Dashboard (Wetter, Garmin-Gesundheitsdaten,
Hermes-To-Dos) für ein Dell-Tablet. Grundlage ist die build-reife Spezifikation
`spec` Rev 2 (Abschnitte A–L).

Dieser Stand ist **K1 / WP-2**: Docker Compose, SQLite-Schema und das
FastAPI-Backend-Skeleton (Abschnitte B, C, D, H).

## Umfang dieses Stands

Enthalten:

- `compose.yaml` — der `backend`-Service
- `.env.example` — Env-Template
- `backend/` — FastAPI-App, SQLite-Schema, REST-API, Tests

Noch **nicht** enthalten (eigene Work-Packages): `frontend/`, `widgets/`,
`mcp-server/`, `garmin-sync/`, `deploy/`.

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
