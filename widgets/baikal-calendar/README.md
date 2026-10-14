# baikal-calendar

Kalender-Widget (spec-phase2 D/WP-P2-1, Pull-Adapter nach D12).

| Datei | Zweck |
|---|---|
| `manifest.json` | Widget-Metadaten (E.2), `category: "productivity"`, `permissions: ["baikal"]`, `default_size` 4×4, `min_size` 3×3 |
| `frontend/widget.js` | ES-Modul, `default`-Export der Widget-Klasse (E.3) |
| `frontend/style.css` | Styles, gescoped über `.wg-baikal-calendar` |
| `frontend/icon.svg` | Icon |

## Kachel-Aufbau (fünf Sekunden)

| Bestandteil | Quelle |
|---|---|
| **Label** „Kalender" | Widget |
| **Wert** z. B. „4" (kommende Termine) | Länge der `events`-Liste aus `GET /api/baikal/events` |
| **Kontext** Liste „Zahnarzt · Di 20.10. · 09:00 · Praxis" | `summary`, `start`, `all_day`, `location` |
| **Ziel** „Nächster Termin: Di 20.10. · 09:00 · Zahnarzt" | erster Eintrag der sortierten Liste |

Jeder Eintrag zeigt **Titel und Startzeit**; Ganztagstermine werden mit eigener
Glyphe (◼) und dem Text „ganztägig" ausgewiesen (I.2 — nie allein über Farbe).
Die Liste ist nach Startzeit sortiert (Backend), scrollt im Widget und springt
dadurch nicht (I.4).

## Verhalten (E.3)

- `render()` baut das DOM **einmalig** auf, ohne Netzwerkzugriff.
- `refresh()` lädt `GET /api/baikal/events` über `api.fetchJson` und tauscht nur
  die Inhalte aus (kein Layout-Sprung, I.4). Der `⟳`-Knopf lädt sofort neu.
- `onConfigChange()` übernimmt ein geändertes `limit`.
- `destroy()` entfernt den Takt-Timer und leert den Container.
- **Fehlervertrag:** Bei nicht erreichbarer Quelle
  (`{"error_code":"BAIKAL_UNAVAILABLE"}`) wirft `refresh()` nicht, sondern zeigt
  den zuletzt bekannten Wert (als „Cache veraltet"/„Quelle nicht erreichbar"
  gekennzeichnet) oder einen leeren Zustand („—", „Keine Termine"). Eine leere
  Liste (`events: []`) ist **kein** Fehler. Der Host stürzt nicht ab.
- Netzwerk läuft ausschließlich über `api.fetchJson` (E.5) — kein direktes
  `fetch` im Widget.

## Konfiguration

`config_schema` (E.2): `limit` (Integer 1–20, Standard 6; maximal angezeigte
Termine). Nicht gesetzte Werte fallen auf den Standard in `frontend/widget.js`
zurück.

## Hinweis Integration (Plan 10.2)

Der Adapter ist pull-only: das Backend ruft Baikal per CalDAV (`REPORT
calendar-query`) und puffert in `api_cache` (`baikal:events:<YYYY-MM-DD>`,
TTL 600 s, Env `BAIKAL_URL`/`BAIKAL_USERNAME`/`BAIKAL_PASSWORD`). Der Router
`backend/app/routers/baikal.py`, die Registrierung in `backend/app/main.py`, der
Settings-Block in `backend/app/config.py`, der Env-Block in `.env.example` und
das Polling (`frontend/js/app.js`, `BUILTIN_WIDGETS`) sind gemeinsame Schritte
und werden laut Plan im Integrationsschritt zusammengeführt.
