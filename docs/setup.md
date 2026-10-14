# worldplaner — Setup am Dell-Tablet (K4, Kiosk-/Betriebsartefakte)

Diese Anleitung ist die **abhakbare Checkliste der Geräteschritte** aus der
Spezifikation (`spec`, Rev 2). „Abschnitt 7 des Plans" entspricht dort
**Abschnitt L.3 (Restfragen / Setup-Abhängigkeiten)**; die Nummerierung unten
folgt den Punkten 1–7 aus L.3.

Die Abschnitte 0–9 decken **Phase 1** (Kiosk-Betrieb des MVP) ab. **Phase 2**
ist in den Abschnitten 10–14 nachgezogen: Zugangsdaten für Baikal, Grocy und
Mealie, die Zip-/Git-Widget-Installation und das Garmin-Schreibtool
(`garmin_add_workout`).

- **Alle** Schritte laufen **auf dem Dell-Tablet** (nicht in der Agentur-Umgebung).
- Die Artefakte in `deploy/` sind vorbereitet und geprüft; die **Installation und
  Verifikation am Gerät** erledigt der Auftraggeber.
- Annahmen: Ubuntu (Desktop/minimal), Repo liegt unter **`/opt/worldplaner`**,
  Frontend wird auf **`http://localhost:8000`** ausgeliefert, X11 auf `:0`.

> Konvention: `$` = Befehl als normaler Nutzer, `#` = `sudo`. Kästchen `[ ]`
> abhaken, sobald der Schritt am Gerät nachweislich erledigt ist.

---

## 0. Repo bereitstellen (Arbeitsverzeichnis `/opt/worldplaner`)

- [ ] Repo auf das Gerät bringen (der Pfad **muss** `/opt/worldplaner` sein —
      die systemd-Units und der Tunnel verweisen darauf):

  ```bash
  sudo mkdir -p /opt/worldplaner
  sudo chown "$USER":"$USER" /opt/worldplaner
  git clone https://github.com/marexxxxxxx/worldplaner /opt/worldplaner
  # spätere Updates:
  # git -C /opt/worldplaner pull --ff-only
  ```

- [ ] `.env` aus der Vorlage erstellen und ausfüllen (die Vorlage kommt mit WP-2,
      Abschnitt H.2 der Spezifikation):

  ```bash
  cd /opt/worldplaner
  cp .env.example .env
  $EDITOR .env
  ```

- [ ] Skripte ausführbar machen:

  ```bash
  chmod +x /opt/worldplaner/deploy/scripts/screen-*.sh
  ```

---

## 1. Ubuntu-Version & Gerätemodell feststellen  *(L.3.1)*

- [ ] Version und Kernel protokollieren:

  ```bash
  lsb_release -a
  uname -r
  ```

- [ ] Dell-Tablet-Modell auslesen:

  ```bash
  sudo dmidecode -s system-product-name
  ```

- [ ] Erwartung dokumentieren/abhaken: **Ubuntu 22.04 LTS oder 24.04 LTS**
      (Desktop oder minimal), Kernel ≥ 5.15. Bei anderer Version die
      Paketnamen in Schritt 2 prüfen.

> Ergebnis in die Issue-Notiz eintragen: `Ubuntu <Version>`, `Modell <...>`.

---

## 2. Grundpakete & Kiosk-Benutzer

- [ ] Pakete installieren (X11, Display-Tools, Browser, Docker):

  ```bash
  sudo apt-get update
  # X-Server + X-Tools (xset/xrandr):
  sudo apt-get install -y xserver-xorg xinit x11-xserver-utils
  # Kiosk-Browser (Ubuntu liefert chromium als Snap; das Paket legt
  # /usr/bin/chromium als Wrapper an):
  sudo apt-get install -y chromium-browser
  # Docker Engine + Compose-Plugin:
  sudo apt-get install -y docker.io docker-compose-v2
  # Tunnel-Binary:
  sudo apt-get install -y cloudflared
  ```

- [ ] Prüfen, dass die ExecStart-Pfade der Units existieren:

  ```bash
  ls -l /usr/bin/Xorg /usr/bin/chromium /usr/bin/docker
  ```

  Fehlt `/usr/bin/chromium` (z. B. bei Snap-only-Installation liegt es unter
  `/snap/bin/chromium`), dann in `deploy/systemd/kiosk-chromium.service` den
  `ExecStart`-Pfad entsprechend anpassen — die Datei ist der einzige Ort dafür.

- [ ] Kiosk-Benutzer anlegen und Gruppen zuweisen:

  ```bash
  sudo useradd -m -s /bin/bash kiosk
  sudo usermod -aG video,audio,input,render kiosk
  sudo -u kiosk mkdir -p /home/kiosk
  ```

- [ ] Docker-Dienst aktivieren (der Compose-Stack hängt via `Requires=docker.service` davon ab):

  ```bash
  sudo systemctl enable --now docker.service
  ```

---

## 3. Cloudflare Access / Tunnel  *(L.3.2)*

- [ ] Tunnel einrichten (Referenz: `deploy/cloudflared/config.yml.example`):

  ```bash
  sudo mkdir -p /etc/cloudflared
  sudo cloudflared tunnel login
  sudo cloudflared tunnel create worldplaner-mcp
  # -> ausgegebene Tunnel-UUID notieren
  sudo cp /opt/worldplaner/deploy/cloudflared/config.yml.example /etc/cloudflared/config.yml
  sudo $EDITOR /etc/cloudflared/config.yml   # <TUNNEL-UUID> und Hostname ersetzen
  sudo cloudflared tunnel route dns worldplaner-mcp mcp.example.com   # Ziel-Domain
  ```

- [ ] Konfiguration validieren:

  ```bash
  cloudflared tunnel ingress validate
  cloudflared tunnel ingress rule https://mcp.example.com/mcp
  ```

- [ ] **Cloudflare Access** für den MCP-Hostnamen einrichten: Self-hosted
      Application auf `mcp.example.com` legen, Policy „Service Auth" mit einem
      **Service Token** (Client-ID/Secret werden in **Hermes** hinterlegt, nicht
      hier). Notieren:

  ```bash
  # Team-Domain und Audience-Tag der Access-Application:
  #   CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
  #   CF_ACCESS_AUD=<Audience-Tag>
  ```

- [ ] `CF_ACCESS_TEAM_DOMAIN` und `CF_ACCESS_AUD` in `/opt/worldplaner/.env` eintragen
      (der MCP-Server validiert das `Cf-Access-Jwt-Assertion`-Header, Spez. F.1).

- [ ] Tunnel als Dienst starten:

  ```bash
  sudo cloudflared service install
  sudo systemctl enable --now cloudflared
  ```

---

## 4. Display-Ausgabe bestimmen → `DISPLAY_OUTPUT`  *(L.3.4)*

- [ ] Nach dem Start des X-Servers (Schritt 6) die **Ausgabe (Output)** ermitteln:

  ```bash
  DISPLAY=:0 xrandr --query
  # gesuchte Zeile: <OUTPUT> connected ...   z. B. "eDP-1 connected primary"
  ```

- [ ] Verfügbarkeit der Display-Steuerung prüfen:

  ```bash
  DISPLAY=:0 xset dpms force off && sleep 2 && DISPLAY=:0 xset dpms force on
  DISPLAY=:0 xrandr --output eDP-1 --brightness 0.3 && sleep 2 \
    && DISPLAY=:0 xrandr --output eDP-1 --brightness 1.0
  ```

- [ ] `DISPLAY_OUTPUT` in `/opt/worldplaner/.env` auf den gefundenen Namen setzen
      (Default der Skripte: `eDP-1`):

  ```bash
  # .env
  DISPLAY=:0
  DISPLAY_OUTPUT=eDP-1
  ```

- [ ] **Fallback:** Fehlen `xset`/`xrandr` am Gerät (oder ohne Wirkung), dann als
      dokumentierte Alternative `vbetool` (benötigt Root) installieren:

  ```bash
  sudo apt-get install -y vbetool
  # vbetool dpms off   /   vbetool dpms on
  ```

---

## 5. X11-Zugriff für den MCP-Container  *(L.3.7)*

- [ ] X-Socket-Verzeichnis existiert und wird in den Container gemountet
      (Mount steht bereits in `compose.yaml`, Spez. H.1: `/tmp/.X11-unix`):

  ```bash
  ls -l /tmp/.X11-unix/
  # erwartet: X0  (Socket des X-Servers auf :0)
  ```

- [ ] Container-Zugriff auf den X-Server erlauben (Container läuft als root):

  ```bash
  DISPLAY=:0 xhost +local:root
  # alternativ restriktiver: xhost +SI:localuser:root
  ```

- [ ] Optional testen (nach `docker compose up -d`, Schritt 6):

  ```bash
  docker compose -f /opt/worldplaner/compose.yaml exec mcp-server \
    sh -lc 'DISPLAY=:0 DISPLAY_OUTPUT=eDP-1 /bin/sh -c "xset dpms force off"'
  ```

> Ohne diesen Schritt liefern `screen_off/on/dim` korrekt `DISPLAY_UNAVAILABLE`
> (Spez. F.2) — das Dashboard läuft trotzdem.

---

## 6. systemd-Units & Skripte installieren

- [ ] Unit-Dateien an ihren Platz kopieren:

  ```bash
  sudo cp /opt/worldplaner/deploy/systemd/*.service /etc/systemd/system/
  sudo cp /opt/worldplaner/deploy/systemd/*.timer   /etc/systemd/system/
  sudo chmod 644 /etc/systemd/system/kiosk-*.service \
                 /etc/systemd/system/kiosk-*.timer \
                 /etc/systemd/system/docker-compose.service
  sudo systemctl daemon-reload
  ```

- [ ] Units vor dem Aktivieren syntaktisch prüfen:

  ```bash
  systemd-analyze verify /etc/systemd/system/kiosk-x11.service \
                         /etc/systemd/system/kiosk-chromium.service \
                         /etc/systemd/system/kiosk-chromium-restart.service \
                         /etc/systemd/system/kiosk-chromium-restart.timer \
                         /etc/systemd/system/docker-compose.service
  # -> keine Ausgabe für diese Units = ok
  ```

- [ ] Compose-Stack starten und dauerhaft aktivieren:

  ```bash
  sudo systemctl enable --now docker-compose.service
  sudo systemctl status docker-compose.service --no-pager
  curl -sf http://localhost:8000/api/health   # {"status":"ok","db":"ok"} (WP-2)
  ```

- [ ] Kiosk aktivieren (X11 → Chromium; nur einmalig nötig, `WantedBy=graphical.target`):

  ```bash
  sudo systemctl enable --now kiosk-x11.service
  sudo systemctl enable --now kiosk-chromium.service
  ```

- [ ] Täglichen 04:00-Neustart aktivieren:

  ```bash
  sudo systemctl enable --now kiosk-chromium-restart.timer
  systemctl list-timers kiosk-chromium-restart.timer --no-pager
  ```

- [ ] Kiosk startet nach Reboot: neu starten und prüfen (WP-1):

  ```bash
  sudo reboot
  # nach dem Boot:
  systemctl is-active kiosk-x11.service kiosk-chromium.service
  systemctl status kiosk-chromium.service --no-pager | head
  ```

- [ ] Watchdog prüfen — `kill -9` auf Chromium, Neustart muss in ≤ 5 s erfolgen (WP-1):

  ```bash
  sudo pkill -9 -f 'chromium --kiosk'
  sleep 6
  pgrep -af 'chromium --kiosk'   # Prozess muss wieder laufen
  ```

---

## 7. Garmin-MFA einmalig durchlaufen  *(L.3.3)*

- [ ] Ersten interaktiven Login inkl. MFA durchführen, damit die Session im
      Volume `garmin-token` persistiert (danach läuft der Sync unbeaufsichtigt):

  ```bash
  cd /opt/worldplaner
  docker compose run --rm garmin-sync python -c \
    "from garminconnect import Garmin; import os; g=Garmin(os.environ['GARMIN_EMAIL'], os.environ['GARMIN_PASSWORD']); g.login(); print('login ok')"
  docker volume inspect worldplaner_garmin-token
  ```

- [ ] Prüfen, dass der Sync schreibt (`garmin_cache`-Zeile nach einem Intervall):

  ```bash
  docker compose logs --tail=50 garmin-sync
  docker compose exec backend python -c \
    "import sqlite3;print(sqlite3.connect('/data/dashboard.db').execute('select count(*) from garmin_cache').fetchone())"
  ```

---

## 8. Wetter-Standort setzen  *(L.3.6)*

- [ ] `WEATHER_LATITUDE`, `WEATHER_LONGITUDE`, `WEATHER_TIMEZONE` in
      `/opt/worldplaner/.env` eintragen (Default München: `48.1374`, `11.5755`,
      `Europe/Berlin`), danach neu starten:

  ```bash
  docker compose -f /opt/worldplaner/compose.yaml up -d backend
  curl -s http://localhost:8000/api/weather/current
  ```

---

## 9. Standard-Layout festlegen  *(L.3.5)*

- [ ] Startplatzierung der MVP-Widgets (Wetter + 4 Garmin-Widgets) im
      `dashboard`-Tab festlegen. Bento-Grid: 12 Spalten, Zeilenhöhe 96 px,
      Gap 8 px (Spez. E.6). Vorschlag als Ausgangspunkt:

  | Widget | x | y | w | h |
  |---|---|---|---|---|
  | weather | 0 | 0 | 4 | 2 |
  | garmin-sleep | 4 | 0 | 4 | 2 |
  | garmin-readiness | 8 | 0 | 4 | 2 |
  | garmin-workout | 0 | 2 | 6 | 2 |
  | garmin-vitals | 6 | 2 | 6 | 2 |

- [ ] Über die API setzen (Beispiel `weather`; `tab_id=dashboard`) und Ergebnis prüfen:

  ```bash
  curl -s -X POST http://localhost:8000/api/layout \
    -H 'Content-Type: application/json' \
    -d '{"widget_id":"weather","tab_id":"dashboard","x":0,"y":0,"w":4,"h":2}'
  curl -s 'http://localhost:8000/api/layout?tab=dashboard'
  ```

- [ ] Gewünschte Anordnung in der Issue-Notiz bestätigen (oder Abweichung eintragen).

---

## Phase 2 — Zugangsdaten, Widget-Installation und Garmin-Schreibpfad

Die folgenden Schritte gehören zu **Phase 2** (`spec-phase2`) und erweitern die
Checkliste oben. Sie laufen ebenfalls auf dem Tablet im Repo unter
`/opt/worldplaner`. Alle Werte kommen in `/opt/worldplaner/.env`; nach jeder
Änderung das Backend neu starten:

```bash
docker compose -f /opt/worldplaner/compose.yaml up -d backend
```

Die Adapter fragen ihre Quelle ab und puffern das Ergebnis; bei Fehlern
(unerreichbar, 401, fehlende Zugangsdaten) wird der letzte Cache-Wert geliefert,
sonst `200` mit `error_code` — nie ein 5xx-Absturz.

---

## 10. Baikal-Kalender anbinden *(WP-P2-1)*

CalDAV-Pull-Adapter (HTTP Basic), Widget `baikal-calendar`, Endpunkt
`GET /api/baikal/events`.

- [ ] `BAIKAL_URL` auf die **CalDAV-Sammlung** des gewünschten Kalenders setzen
      (die URL wird direkt als `REPORT`-Ziel verwendet), dazu Benutzer/Passwort:

  ```bash
  # .env
  BAIKAL_URL=https://baikal.example.com/remote.php/dav/calendars/<benutzer>/<kalender>/
  BAIKAL_USERNAME=<benutzer>
  BAIKAL_PASSWORD=<passwort>
  BAIKAL_CACHE_TTL_SECONDS=600
  BAIKAL_DAYS_AHEAD=7
  BAIKAL_TIMEOUT_SECONDS=10
  ```

- [ ] Backend neu starten (siehe oben) und das Ergebnis prüfen:

  ```bash
  curl -s http://localhost:8000/api/baikal/events
  # erwartet: {"events":[...]} ; bei Fehler {"events":[],"error_code":"BAIKAL_UNAVAILABLE"}
  ```

> `BAIKAL_DAYS_AHEAD` begrenzt das Vorausschau-Fenster; ein leeres Ergebnis ist
> kein Fehler.

---

## 11. Grocy-Bestand anbinden *(WP-P2-2)*

REST-Pull-Adapter (Header `GROCY-API-KEY`), Widget `grocy-stock`, Endpunkt
`GET /api/grocy/stock`. Voraussetzung: Grocy ≥ 3.3.

- [ ] `GROCY_URL` (Basis-URL, ohne `/api/stock` — der Pfad wird ergänzt) und den
      API-Key aus Grocy (Einstellungen → API) eintragen:

  ```bash
  # .env
  GROCY_URL=https://grocy.example.com
  GROCY_API_KEY=<api-key>
  GROCY_CACHE_TTL_SECONDS=600
  GROCY_TIMEOUT_SECONDS=10
  ```

- [ ] Backend neu starten und prüfen:

  ```bash
  curl -s http://localhost:8000/api/grocy/stock
  # erwartet: {"products":[...]} ; bei Fehler {"products":[],"error_code":"GROCY_UNAVAILABLE"}
  ```

---

## 12. Mealie-Wochenplan anbinden *(WP-P2-3)*

REST-Pull-Adapter (Bearer-Token), Widget `mealie-mealplan`, Endpunkt
`GET /api/mealie/mealplan`. Voraussetzung: Mealie v2 (der Adapter ruft
`GET {MEALIE_URL}/api/groups/mealplans/this-week`).

- [ ] `MEALIE_URL` (Basis-URL, ohne den API-Pfad) und den API-Token eintragen
      (Mealie: Profil → API-Token):

  ```bash
  # .env
  MEALIE_URL=https://mealie.example.com
  MEALIE_API_TOKEN=<bearer-token>
  MEALIE_CACHE_TTL_SECONDS=600
  MEALIE_DAYS_AHEAD=7
  MEALIE_TIMEOUT_SECONDS=10
  ```

- [ ] Backend neu starten und prüfen:

  ```bash
  curl -s http://localhost:8000/api/mealie/mealplan
  # erwartet: {"meals":[...]} ; bei Fehler {"meals":[],"error_code":"MEALIE_UNAVAILABLE"}
  ```

> `MEALIE_DAYS_AHEAD` liegt im Bereich 1–7 (Mealie liefert „this week").

---

## 13. Zip-/Git-Widget-Installation *(WP-P2-4)*

Installierte Widgets landen im Schreib-Volume `widgets-data`
(`WIDGETS_INSTALL_DIR=/data/widgets`, im Container `/data/widgets`); die
eingebauten bleiben read-only. Nach der Installation wird das Widget mit
`is_builtin=0` registriert und lässt sich im `widgets`-Tab aktivieren.

- [ ] Zip installieren (`multipart`-Feld **`file`**; Antwort `201 {widget:{…}}`):

  ```bash
  curl -s -X POST http://localhost:8000/api/widgets/install \
       -F 'file=@mein-widget.zip'
  ```

- [ ] Aus Git installieren (`ref` Default `main`; Antwort `201 {widget:{…}}`):

  ```bash
  curl -s -X POST http://localhost:8000/api/widgets/install/git \
       -H 'Content-Type: application/json' \
       -d '{"url":"https://github.com/example/widget","ref":"main"}'
  ```

- [ ] Ergebnis kontrollieren und ggf. deinstallieren (nicht eingebaute Widgets;
      Antwort `204`):

  ```bash
  curl -s http://localhost:8000/api/widgets
  curl -s -X DELETE http://localhost:8000/api/widgets/<id>
  ```

**Manifest-Vertrag** (Zip/Clone müssen genau einen Widget-Ordner mit
`manifest.json` enthalten — direkt oder in einem Unterordner, dessen Name der
`id` entspricht):

| Feld | Regel |
|---|---|
| `id` | `^[a-z0-9][a-z0-9-]{0,63}$` |
| `name` | 1–80 Zeichen |
| `version` | SemVer `x.y.z` |
| `category` | `weather`, `health`, `productivity` oder `general` |
| `default_size` | `{w: 1–12, h: ≥ 1}` |
| `permissions` *(optional)* | Teilmenge von `garmin_cache, weather, tasks, state, api_cache, baikal, grocy, mealie` |

Grenzen: `WIDGETS_MAX_ZIP_BYTES` (Zip, Default 10 MiB) und
`WIDGETS_MAX_UNCOMPRESSED_BYTES` (entpackt, Default 50 MiB). Eine bereits
registrierte `id` → `409`, ein eingebautes Widget → `403`, unbekannte `id` →
`404`.

---

## 14. Garmin-Schreibtool `garmin_add_workout` *(WP-P2-5)*

Der MCP-Aufruf `garmin_add_workout` legt eine **abgeschlossene Aktivität** im
Garmin-Account an (Pflicht: `activity_type`, `start_time` in ISO-8601 UTC,
`duration_s` ≥ 1; optional `distance_m`, `calories`, `title`). Der Aufruf läuft
über die Backend-REST-API `POST /api/garmin/workouts`; das Backend reicht ihn an
den internen Write-Endpunkt `POST /write` von `garmin-sync` (Port `8002`, **kein**
Host-Port) weiter. `garmin-sync` bleibt einziger Inhaber der Garmin-Session und
einziger Schreiber von `garmin_cache`.

- [ ] Ohne echten Garmin-Account verifizieren (schreibt synthetische Cache-Zeilen
      im Schema G.5):

  ```bash
  GARMIN_SYNC_MOCK=1 docker compose -f /opt/worldplaner/compose.yaml up -d garmin-sync
  docker compose -f /opt/worldplaner/compose.yaml logs --tail=50 garmin-sync
  ```

- [ ] Mit echtem Account: einen MCP-Client gegen `http://127.0.0.1:8001/mcp`
      verbinden (siehe `docs/setup.md` bzw. `AGENTS.md`, Abschnitt 6) und das
      Tool `garmin_add_workout` aufrufen. Antwort: `{ok:true, workout:{...}}` mit
      `activity_id`; Fehler `GARMIN_UNAVAILABLE` (retryable) oder
      `GARMIN_AUTH_FAILED` (nicht retryable).

---

## Abschluss-Verifikation (Abnahme-Belege)

- [ ] **Units** syntaktisch valide (keine Ausgabe/Exit 0 für die 5 Units):

  ```bash
  for u in /opt/worldplaner/deploy/systemd/*.service /opt/worldplaner/deploy/systemd/*.timer; do
    echo "## $u"; systemd-analyze verify "$u"
  done
  ```

- [ ] **Skripte** syntaktisch fehlerfrei:

  ```bash
  for s in /opt/worldplaner/deploy/scripts/*.sh; do bash -n "$s" && echo "ok: $s"; done
  ```

- [ ] **Ende-zu-Ende:** Reboot → Chromium-Kiosk zeigt das Frontend; `curl
      http://localhost:8000/api/health` liefert `{"status":"ok","db":"ok"}`.

**Phase 2:**

- [ ] **Adapter** liefern Daten (kein 5xx; ggf. `error_code` statt Absturz):

  ```bash
  curl -s http://localhost:8000/api/baikal/events
  curl -s http://localhost:8000/api/grocy/stock
  curl -s http://localhost:8000/api/mealie/mealplan
  ```

- [ ] **Widget-Installation:** Zip- und Git-Installation liefern `201`; ein
      installiertes Widget erscheint in `GET /api/widgets` (`is_builtin=0`) und
      lässt sich per `DELETE /api/widgets/<id>` wieder entfernen (`204`).

- [ ] **Garmin-Schreibpfad:** mit `GARMIN_SYNC_MOCK=1` läuft `garmin-sync`, und
      das MCP-Tool `garmin_add_workout` antwortet `{ok:true, workout:{...}}`.

---

## Offene Gerätewerte (in die Issue-Notiz eintragen)

| Wert | Bedeutung | hier eintragen |
|---|---|---|
| Ubuntu-Version | L.3.1 | |
| Tablet-Modell | L.3.1 | |
| `DISPLAY_OUTPUT` | L.3.4 | |
| `CF_ACCESS_TEAM_DOMAIN` | L.3.2 | |
| `CF_ACCESS_AUD` | L.3.2 | |
| Layout bestätigt | L.3.5 | |
| `BAIKAL_URL` | Phase 2 (WP-P2-1) | |
| `GROCY_URL` | Phase 2 (WP-P2-2) | |
| `MEALIE_URL` | Phase 2 (WP-P2-3) | |
| Widget-Installation geprüft (Zip/Git/Delete) | Phase 2 (WP-P2-4) | |
| `garmin_add_workout` geprüft | Phase 2 (WP-P2-5) | |
