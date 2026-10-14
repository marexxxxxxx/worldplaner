# AGENTS.md — worldplaner installieren und betreiben

Diese Datei richtet sich an einen **KI-Agenten** (oder Menschen), der das
Kiosk-Dashboard **worldplaner** auf einem **Ubuntu-Tablet** installiert und
betreibt. Sie ist die kompakte Arbeitsanweisung; die ausführliche, abhakbare
Geräte-Checkliste — inklusive **Phase 2** (Zugangsdaten, Widget-Installation,
Garmin-Schreibpfad) — steht in [`docs/setup.md`](docs/setup.md).

Konventionen in diesem Dokument:

- `$` = Befehl als normaler Nutzer, `#` = `sudo`.
- Das Repo liegt auf dem Tablet unter **`/opt/worldplaner`** (systemd-Units und
  Tunnel verweisen fest darauf).
- Das Frontend wird lokal auf **`http://localhost:8000`** ausgeliefert, X11
  läuft auf `:0`.
- Die Spezifikation (`spec` / `spec-phase2`, Abschnitte A–L) ist **nicht Teil
  dieses Repos**; Abschnittsangaben wie „F.1" verweisen darauf.

---

## 1. Repo-Landkarte

| Pfad | Inhalt |
|---|---|
| `compose.yaml` | Docker-Compose-Stack mit den Services `backend`, `mcp-server`, `garmin-sync` und den Volumes `sqlite-data`, `garmin-token`, `widgets-data` |
| `.env.example` | Vorlage für alle Env-Variablen (Abschnitt 4); wird zu `.env` kopiert |
| `backend/` | FastAPI-App (`app/main.py`), SQLite-Schema (`app/schema.sql`), REST-API, Tests |
| `frontend/` | App-Shell, Bento-Grid (`js/bento.js`), Widget-Runtime (`js/widget-runtime.js`), REST-Client; keine Build-Stufe |
| `widgets/` | Eingebaute Widgets, je Ordner mit `manifest.json` + `frontend/` + `README.md` |
| `garmin-sync/` | Einziger Schreiber der Tabelle `garmin_cache` (`sync.py`, `write_server.py`) |
| `mcp-server/` | MCP-Server über Streamable HTTP (`app/main.py`, `app/tools.py`), Tests |
| `deploy/systemd/` | systemd-Units für Docker-Compose-Stack, X11, Chromium-Kiosk, Kiosk-Neustart |
| `deploy/scripts/` | Display-Skripte `screen-on.sh`, `screen-dim.sh`, `screen-off.sh` |
| `deploy/cloudflared/` | `config.yml.example` — Referenz für den Cloudflare-Tunnel (Host-Dienst) |
| `docs/setup.md` | Ausführliche Setup-/Betriebs-Checkliste (Phase 1 + Phase 2) |
| `README.md` | Projektüberblick, API-Tabelle |

Eingebaute Widgets in `widgets/` (jeweils `manifest.json`, `is_builtin=1`):
`weather`, `garmin-sleep`, `garmin-readiness`, `garmin-workout`, `garmin-vitals`,
`hermes-todos`, `baikal-calendar`, `grocy-stock`, `mealie-mealplan`.

Datenfluss (Kurzform): Backend liefert Frontend **und** Widgets unter derselben
Origin aus (D10). `garmin-sync` ist der **einzige** Schreiber von `garmin_cache`;
das Backend liest diese Tabelle nur. Der MCP-Server schreibt **nie** direkt in
SQLite und **nie** selbst nach Garmin — er delegiert an die Backend-REST-API.

---

## 2. Voraussetzungen

- **Host:** Ubuntu 22.04 LTS oder 24.04 LTS (Desktop oder minimal), Kernel ≥ 5.15.
- **Docker** mit **Compose v2** (`docker compose …`), Docker-Dienst aktiv.
- **Ports:** `8000` (Backend/Frontend), `8001` (MCP-Server) — beide nur an
  `127.0.0.1` gebunden (kein direkter LAN-Zugriff).
- **Tablet/Kiosk zusätzlich:** X11 (`xserver-xorg`, `xinit`,
  `x11-xserver-utils` für `xset`/`xrandr`), Chromium, `cloudflared`.
- **Nur für Tests außerhalb von Docker:** Python ≥ 3.12 (die drei Python-Pakete
  deklarieren `requires-python = ">=3.12"`) und Node.js/npm (Frontend-Tests).
- Die Kiosk-Units gehen von einem Kiosk-Benutzer `kiosk` und Xorg auf `:0`
  (VT7) aus.

Ports und Service-/Container-Namen: Compose leitet Container-Namen aus
Projekt- und Service-Namen ab (z. B. `worldplaner-backend-1`); Befehle unten
verwenden den **Service-Namen** (`docker compose exec backend …`).

---

## 3. Installation (exakte Befehle)

### 3.1 Repo bereitstellen

```bash
sudo mkdir -p /opt/worldplaner
sudo chown "$USER":"$USER" /opt/worldplaner
git clone https://github.com/marexxxxxxx/worldplaner /opt/worldplaner
# spätere Updates:
# git -C /opt/worldplaner pull --ff-only
```

### 3.2 `.env` erstellen

```bash
cd /opt/worldplaner
cp .env.example .env
$EDITOR .env            # Werte siehe Abschnitt 4
chmod +x /opt/worldplaner/deploy/scripts/screen-*.sh
```

Ohne `.env` gelten die Defaults aus `.env.example` (die Compose-Datei lädt
`.env` optional, `env_file: path: .env required: false`).

### 3.3 Stack starten

```bash
cd /opt/worldplaner
docker compose config          # Syntaxprüfung (sollte 0 liefern)
docker compose up -d
docker compose ps              # backend + mcp-server + garmin-sync = running/healthy
curl -s http://localhost:8000/api/health   # -> {"status":"ok","db":"ok"}
```

Beim Erststart legt das Backend das SQLite-Schema idempotent an, seedet die drei
Tabs (`dashboard`, `todos`, `widgets`) und registriert die eingebauten Widgets
aus `widgets/*/manifest.json` (Upsert per `id`, `is_builtin=1`).

### 3.4 Tablet-/Kiosk-Artefakte installieren

```bash
# Grundpakete (X11, Display-Tools, Browser, Docker, Tunnel)
sudo apt-get update
sudo apt-get install -y xserver-xorg xinit x11-xserver-utils \
                        chromium-browser docker.io docker-compose-v2 cloudflared

# Kiosk-Benutzer
sudo useradd -m -s /bin/bash kiosk
sudo usermod -aG video,audio,input,render kiosk
sudo systemctl enable --now docker.service

# systemd-Units
sudo cp /opt/worldplaner/deploy/systemd/*.service /etc/systemd/system/
sudo cp /opt/worldplaner/deploy/systemd/*.timer   /etc/systemd/system/
sudo chmod 644 /etc/systemd/system/kiosk-*.service \
               /etc/systemd/system/kiosk-*.timer \
               /etc/systemd/system/docker-compose.service
sudo systemctl daemon-reload

sudo systemctl enable --now docker-compose.service
sudo systemctl enable --now kiosk-x11.service
sudo systemctl enable --now kiosk-chromium.service
sudo systemctl enable --now kiosk-chromium-restart.timer
```

**X11-Zugriff für den MCP-Container:** `/tmp/.X11-unix` wird in den Container
gemountet (steht in `compose.yaml`); zusätzlich den Container-Zugriff erlauben:

```bash
DISPLAY=:0 xhost +local:root
```

**Tunnel** (Referenz: `deploy/cloudflared/config.yml.example`):

```bash
sudo mkdir -p /etc/cloudflared
sudo cloudflared tunnel login
sudo cloudflared tunnel create worldplaner-mcp          # -> Tunnel-UUID notieren
sudo cp /opt/worldplaner/deploy/cloudflared/config.yml.example /etc/cloudflared/config.yml
sudo $EDITOR /etc/cloudflared/config.yml                # <TUNNEL-UUID> + Hostname ersetzen
sudo cloudflared tunnel route dns worldplaner-mcp mcp.example.com
cloudflared tunnel ingress validate
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

Die vollständige Reihenfolge (Ubuntu-/Modellprüfung, `DISPLAY_OUTPUT` ermitteln,
Garmin-MFA, Wetter-Standort, Standard-Layout) steht in
[`docs/setup.md`](docs/setup.md), Schritte 1–9.

---

## 4. Env-Variablen-Referenz

Alle Schlüssel aus [`.env.example`](.env.example) mit Zweck. Werte sind die
jeweiligen Defaults; leere Zellen werden pro Installation gefüllt.

### Backend

| Variable | Default | Zweck |
|---|---|---|
| `BACKEND_PORT` | `8000` | Host-Port des Backends (`127.0.0.1:${BACKEND_PORT}` → Container `8000`). |
| `SQLITE_PATH` | `/data/dashboard.db` | SQLite-Pfad im Container (im Volume `sqlite-data`); Compose setzt ihn zusätzlich fest. |

### MCP

| Variable | Default | Zweck |
|---|---|---|
| `MCP_PORT` | `8001` | Host-Port des MCP-Servers (`127.0.0.1:${MCP_PORT}` → Container `8001`). |
| `DISPLAY` | `:0` | X11-Display für die Display-Steuerung; Compose reicht ihn an `mcp-server` weiter. |
| `DISPLAY_OUTPUT` | `eDP-1` | xrandr-Ausgabename für Dimmen/Helligkeit (am Gerät per `DISPLAY=:0 xrandr --query` ermitteln). |
| `MCP_RATE_LIMIT_PER_MINUTE` | `60` | Token-Bucket-Rate-Limit pro Identität; `0` deaktiviert die Bremse. |

### Cloudflare Access

| Variable | Default | Zweck |
|---|---|---|
| `CF_ACCESS_TEAM_DOMAIN` | — | Team-Domain (`<team>.cloudflareaccess.com`) für die JWKS-/Assertion-Prüfung. |
| `CF_ACCESS_AUD` | — | Audience-Tag der Access-Application. Sind beide gesetzt, prüft der MCP-Server das `Cf-Access-Jwt-Assertion`. |

### Garmin (`garmin-sync`)

| Variable | Default | Zweck |
|---|---|---|
| `GARMIN_EMAIL` | — | Garmin-Account (Login). |
| `GARMIN_PASSWORD` | — | Garmin-Passwort (Login). |
| `GARMIN_SYNC_INTERVAL_SECONDS` | `1200` | Abrufintervall des Syncs. |
| `GARMIN_BACKOFF_BASE_SECONDS` | `60` | Basis des exponentiellen Backoffs (`base * 2^attempt`). |
| `GARMIN_BACKOFF_MAX_ATTEMPTS` | `5` | Wiederholungen je Intervall vor dem Aufgeben. |
| `GARMIN_BACKOFF_CAP_SECONDS` | `3600` | Obergrenze der Wartezeit (gedeckelt auf `min(cap, interval)`). |
| `GARMIN_TOKEN_DIR` | `/data/token` | Token-Verzeichnis; muss auf das Volume `garmin-token` zeigen (Session-Persistenz). |
| `GARMIN_SYNC_MOCK` | `0` | `1` = synthetische Cache-Zeilen ohne echten Garmin-Login (nur Verifikation). |
| `GARMIN_SYNC_WRITE_URL` *(kommentiert)* | `http://garmin-sync:8002` | Forward-Ziel des internen Write-Endpunkts; Default genügt. |
| `GARMIN_SYNC_WRITE_TIMEOUT_SECONDS` *(kommentiert)* | `10` | Timeout des Forward-Clients. |
| `GARMIN_TIMEZONE` *(nicht in `.env.example`)* | `UTC` | Lokale Zeitzone beim Anlegen einer Aktivität (`garmin-sync/sync.py`). |

### Wetter (Open-Meteo)

| Variable | Default | Zweck |
|---|---|---|
| `WEATHER_LATITUDE` | `48.1374` | Breitengrad des Standorts. |
| `WEATHER_LONGITUDE` | `11.5755` | Längengrad des Standorts. |
| `WEATHER_TIMEZONE` | `Europe/Berlin` | Zeitzone für die Wetterabfrage. |
| `WEATHER_CACHE_TTL_SECONDS` | `600` | Cache-Dauer der Wetterantworten. |

### Baikal-Kalender (Phase 2, Pull-Adapter, HTTP Basic)

| Variable | Default | Zweck |
|---|---|---|
| `BAIKAL_URL` | — | Kalender-URL der Baikal-Instanz. |
| `BAIKAL_USERNAME` | — | Benutzername (HTTP Basic). |
| `BAIKAL_PASSWORD` | — | Passwort (HTTP Basic). |
| `BAIKAL_CACHE_TTL_SECONDS` | `600` | Cache-Dauer. |
| `BAIKAL_DAYS_AHEAD` | `7` | Vorausschau-Fenster in Tagen. |
| `BAIKAL_TIMEOUT_SECONDS` | `10` | HTTP-Timeout. |

### Grocy-Bestand (Phase 2, Pull-Adapter)

| Variable | Default | Zweck |
|---|---|---|
| `GROCY_URL` | — | Basis-URL der Grocy-Instanz (Grocy ≥ 3.3). |
| `GROCY_API_KEY` | — | API-Key (Header `GROCY-API-KEY`). |
| `GROCY_CACHE_TTL_SECONDS` | `600` | Cache-Dauer. |
| `GROCY_TIMEOUT_SECONDS` | `10` | HTTP-Timeout. |

### Mealie-Wochenplan (Phase 2, Pull-Adapter, Bearer-Token)

| Variable | Default | Zweck |
|---|---|---|
| `MEALIE_URL` | — | Basis-URL der Mealie-Instanz (Mealie v2). |
| `MEALIE_API_TOKEN` | — | Bearer-Token für die API. |
| `MEALIE_CACHE_TTL_SECONDS` | `600` | Cache-Dauer. |
| `MEALIE_DAYS_AHEAD` | `7` | Vorausschau-Fenster in Tagen. |
| `MEALIE_TIMEOUT_SECONDS` | `10` | HTTP-Timeout. |

### Widget-Installation (Phase 2)

| Variable | Default | Zweck |
|---|---|---|
| `WIDGETS_INSTALL_DIR` | `/data/widgets` | Zielordner installierter Widgets (Volume `widgets-data`). |
| `WIDGETS_MAX_ZIP_BYTES` | `10485760` | Maximale Zip-Größe (10 MiB). |
| `WIDGETS_MAX_UNCOMPRESSED_BYTES` | `52428800` | Maximale entpackte Größe (50 MiB). |

### Grid

| Variable | Default | Zweck |
|---|---|---|
| `GRID_COLUMNS` | `12` | Bento-Grid-Spalten. |
| `GRID_ROW_HEIGHT_PX` | `96` | Zeilenhöhe in Pixeln. |
| `GRID_GAP_PX` | `8` | Zellenabstand in Pixeln. |

---

## 5. Tablet-/Kiosk-Schritte

Die Artefakte liegen vorbereitet im Repo und werden auf das Gerät kopiert.

### 5.1 X11 (ein einziger X-Server auf `:0`)

`deploy/systemd/kiosk-x11.service` startet `/usr/bin/Xorg :0 -nolisten tcp vt7`
(VT7) und schließt einen Display-Manager aus (`Conflicts=display-manager.service`).
Ist `Xorg` nicht unter `/usr/bin/Xorg`, den `ExecStart`-Pfad der Unit anpassen.

### 5.2 Chromium-Kiosk

`deploy/systemd/kiosk-chromium.service` startet als Benutzer `kiosk` mit
`DISPLAY=:0`:

```
/usr/bin/chromium --kiosk --noerrdialogs --disable-infobars \
  --touch-events=enabled --disable-pinch http://localhost:8000
```

`Restart=always` / `RestartSec=5` = Watchdog (ein `kill -9` auf Chromium wird in
≤ 5 s neu gestartet). Liegt Chromium nur als Snap unter `/snap/bin/chromium`,
den `ExecStart`-Pfad in dieser Unit anpassen — sie ist der einzige Ort dafür.

### 5.3 Täglicher Neustart

`deploy/systemd/kiosk-chromium-restart.timer` (`OnCalendar=*-*-* 04:00:00`,
`Persistent=true`) startet über `kiosk-chromium-restart.service` den Kiosk
nächtlich neu (gegen Chromium-Speicherlecks).

### 5.4 Compose-Stack als Unit

`deploy/systemd/docker-compose.service` hat `WorkingDirectory=/opt/worldplaner`,
`Requires=docker.service` und `ExecStart=/usr/bin/docker compose up -d`
(`ExecStop=… down`).

### 5.5 Display-Skripte

`deploy/scripts/` (werden von den MCP-Display-Tools verwendet):

| Skript | Wirkung |
|---|---|
| `screen-off.sh` | `DISPLAY=${DISPLAY:-:0}` `xset dpms force off` |
| `screen-on.sh` | `xset dpms force on` + `xrandr --output $DISPLAY_OUTPUT --brightness 1.0` |
| `screen-dim.sh` | `xrandr --output $DISPLAY_OUTPUT --brightness ${1:-0.3}` |

`DISPLAY` und `DISPLAY_OUTPUT` kommen aus der Umgebung (Defaults `:0` / `eDP-1`).
Ohne X11 liefern die MCP-Tools `DISPLAY_UNAVAILABLE` statt eines Absturzes — das
Dashboard läuft trotzdem.

### 5.6 Cloudflare-Tunnel

`deploy/cloudflared/config.yml.example` exponiert **nur** den MCP-Server
(`http://127.0.0.1:8001`) unter einem Hostnamen (Beispiel `mcp.example.com`);
alles andere wird per `http_status:404` abgewiesen. Das Frontend bleibt lokal auf
`:8000`. cloudflared läuft **nicht** in `docker compose`, sondern als Host-Dienst
(siehe 3.4).

---

## 6. MCP-Anbindung

- **Endpunkt (Streamable HTTP):** `POST /mcp` (Server-Stream: `GET /mcp`).
- **Healthcheck:** `GET /healthz` → `{"status":"ok","server":"worldplaner-mcp"}`.
- **Im Container:** `0.0.0.0:8001`; **am Host:** nur `127.0.0.1:8001`. Exposition
  ausschließlich über Tunnel + Cloudflare Access.
- **Tunnel-Ziel:** `https://<hostname>/mcp` → `http://127.0.0.1:8001/mcp`.
- **Sicherheit:** DNS-Rebinding-Schutz, Rate-Limit (`MCP_RATE_LIMIT_PER_MINUTE`)
  und Access-Prüfung des `Cf-Access-Jwt-Assertion` über `CF_ACCESS_AUD` +
  `CF_ACCESS_TEAM_DOMAIN`. Der Client sendet das Service Token
  (`Cf-Access-Client-Id`/`Cf-Access-Client-Secret`); Cloudflare Access setzt
  daraus das Assertion-Header.

**Tools (9):**

| Tool | Art | Zweck |
|---|---|---|
| `dashboard_status` | read | aktiver Tab, Bildschirmzustand, offene/erledigte To-Dos, Backend-Gesundheit |
| `task_list` | read | Hermes-To-Dos (max. 200) |
| `task_create` | write | To-Do anlegen (`source=hermes`) |
| `task_update` | write | To-Do ändern („Hermes gewinnt") |
| `task_delete` | write, destruktiv | verlangt `confirm:true` (ggf. `expected_title`) |
| `screen_off` | write | Display aus |
| `screen_on` | write | Display an, Helligkeit 1.0 |
| `screen_dim` | write | Helligkeit `0.0`–`1.0` (Default `0.3`) |
| `garmin_add_workout` | write | Aktivität im Garmin-Account anlegen (Phase 2) |

Jede Antwort ist `{ok:true, …}` oder `{ok:false, error:{code,message,retryable}}`
— niemals eine Exception. Task-CRUD wird an die Backend-REST-API delegiert;
`garmin_add_workout` läuft über Backend → `garmin-sync` (siehe Abschnitt 8).

---

## 7. Test-Befehle je Komponente

**Backend:**

```bash
cd backend
pip install -e ".[dev]"
pytest
```

**MCP-Server:**

```bash
cd mcp-server
pip install -e ".[dev]"
pytest

# Rohbeleg/Inspector-Ersatz gegen einen laufenden Server (alle 9 Tools):
MCP_URL=http://127.0.0.1:8001/mcp python tests/inspect_client.py
```

**garmin-sync:**

```bash
cd garmin-sync
pip install -e ".[dev]"
pytest

# Verifikation ohne echten Garmin-Account (schreibt synthetische Cache-Zeilen):
GARMIN_SYNC_MOCK=1 docker compose up -d garmin-sync
```

**Frontend:**

```bash
cd frontend
npm install
npm test        # Unit-Tests (node --test tests/), kein Server nötig

# E2E gegen den laufenden Stack:
BASE_URL=http://127.0.0.1:8000 CHROMIUM_PATH=/pfad/zu/chromium npm run test:e2e
BASE_URL=http://127.0.0.1:8000 CHROMIUM_PATH=/pfad/zu/chromium npm run test:e2e:admin
```

Die drei Python-Pakete brauchen Python ≥ 3.12. Die Frontend-E2E-Tests setzen den
laufenden Stack voraus (D10: Backend liefert Frontend + Widgets aus).

---

## 8. Phase 2 — Betrieb (Widget-Installation, Adapter, Garmin-Schreibpfad)

### 8.1 Adapter (Pull, nur lesend)

Die Adapter Baikal, Grocy und Mealie liefern Daten, sobald ihre `*_URL` + Token
in `.env` gesetzt und das Backend neu gestartet ist. Endpunkte:
`GET /api/baikal/events`, `GET /api/grocy/stock`, `GET /api/mealie/mealplan`.
Zugangsdaten und Prüfschritte: [`docs/setup.md`](docs/setup.md), Phase 2.

Nach Änderung von `.env` das Backend neu starten:

```bash
docker compose -f /opt/worldplaner/compose.yaml up -d backend
```

### 8.2 Zip-/Git-Widget-Installation

Zielordner ist das Schreib-Volume `widgets-data` (`WIDGETS_INSTALL_DIR=/data/widgets`)
— eingebaute Widgets bleiben read-only.

```bash
# Zip (multipart-Feld `file`) -> 201 {widget:{…}}
curl -s -X POST http://localhost:8000/api/widgets/install \
     -F 'file=@mein-widget.zip'

# Git (JSON {url, ref}) -> 201 {widget:{…}}
curl -s -X POST http://localhost:8000/api/widgets/install/git \
     -H 'Content-Type: application/json' \
     -d '{"url":"https://github.com/example/widget","ref":"main"}'

# Deinstallation eines nicht eingebauten Widgets -> 204
curl -s -X DELETE http://localhost:8000/api/widgets/<id>
```

Regeln: Zip/Clone müssen **genau einen** Widget-Ordner mit `manifest.json`
enthalten (direkt oder in einem Unterordner, dessen Name der `id` entspricht);
`id` = `^[a-z0-9][a-z0-9-]{0,63}$`, `version` = SemVer `x.y.z`, `category` aus
`weather|health|productivity|general`, `default_size` = `{w:1-12, h:≥1}`,
`permissions` ⊆ `garmin_cache, weather, tasks, state, api_cache, baikal, grocy,
mealie`. Grenzen: `WIDGETS_MAX_ZIP_BYTES` / `WIDGETS_MAX_UNCOMPRESSED_BYTES`.
Eine bereits registrierte `id` → `409`; eingebaute Widgets → `403`; unbekannte
`id` → `404`.

### 8.3 Garmin-Schreibpfad (`garmin_add_workout`)

Der MCP-Aufruf `garmin_add_workout` (`activity_type`, `start_time` ISO-8601 UTC,
`duration_s` ≥ 1; optional `distance_m`, `calories`, `title`) läuft über die
Backend-REST-API `POST /api/garmin/workouts`; das Backend reicht ihn an den
internen, **nicht** nach außen exponierten Write-Endpunkt `POST /write` von
`garmin-sync` (Port `8002`, nur Compose-Netz) weiter. `garmin-sync` bleibt
einziger Inhaber der Garmin-Session und einziger Schreiber von `garmin_cache`.

Verifikation ohne echten Garmin-Account:

```bash
GARMIN_SYNC_MOCK=1 docker compose up -d garmin-sync
docker compose logs --tail=50 garmin-sync
```

---

## 9. Verifikations-Checkliste

- [ ] `docker compose config` läuft fehlerfrei (Exit 0).
- [ ] `docker compose ps` zeigt `backend`, `mcp-server`, `garmin-sync` als
      laufend/healthy.
- [ ] `curl -s http://localhost:8000/api/health` → `{"status":"ok","db":"ok"}`.
- [ ] `curl -s http://localhost:8001/healthz` → `{"status":"ok","server":"worldplaner-mcp"}`.
- [ ] `curl -s http://localhost:8000/api/widgets` listet die 9 eingebauten
      Widgets.
- [ ] MCP gegen laufenden Server: `MCP_URL=http://127.0.0.1:8001/mcp python
      mcp-server/tests/inspect_client.py` verbindet, listet die Tools.
- [ ] Skripte syntaktisch fehlerfrei: `for s in deploy/scripts/*.sh; do bash -n "$s"; done`.
- [ ] Units syntaktisch valide:
      `for u in deploy/systemd/*.service deploy/systemd/*.timer; do systemd-analyze verify "$u"; done`.
- [ ] Nach Reboot zeigt der Chromium-Kiosk das Frontend; Watchdog startet
      Chromium nach `kill -9` neu.
- [ ] Phase-2-Zugangsdaten gesetzt (`BAIKAL_*`, `GROCY_*`, `MEALIE_*`) und
      Endpunkte liefern Daten.
- [ ] Zip- und Git-Installation liefern `201`; `DELETE /api/widgets/<id>`
      liefert `204`.

Offene Gerätewerte (Ubuntu-Version, Tablet-Modell, `DISPLAY_OUTPUT`,
`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, Layout) in die Issue-Notiz eintragen —
Tabelle in [`docs/setup.md`](docs/setup.md).

---

## 10. Verweise

- Betriebs-Checkliste (Phase 1 + Phase 2): [`docs/setup.md`](docs/setup.md)
- Projektüberblick & REST-API-Vertrag: [`README.md`](README.md)
- Compose-Stack: [`compose.yaml`](compose.yaml) · Env-Vorlage: [`.env.example`](.env.example)
- Kiosk-Artefakte: [`deploy/systemd/`](deploy/systemd/),
  [`deploy/scripts/`](deploy/scripts/),
  [`deploy/cloudflared/config.yml.example`](deploy/cloudflared/config.yml.example)
