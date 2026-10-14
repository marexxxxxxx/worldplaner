# grocy-stock

Bestands-Widget (spec-phase2 D/WP-P2-2, Pull-Adapter nach D12).

| Datei | Zweck |
|---|---|
| `manifest.json` | Widget-Metadaten (E.2), `category: "general"`, `permissions: ["grocy"]`, `default_size` 4×4, `min_size` 3×3 |
| `frontend/widget.js` | ES-Modul, `default`-Export der Widget-Klasse (E.3) |
| `frontend/style.css` | Styles, gescoped über `.wg-grocy-stock` |
| `frontend/icon.svg` | Icon |

## Kachel-Aufbau (fünf Sekunden)

| Bestandteil | Quelle |
|---|---|
| **Label** „Vorrat" | Widget |
| **Wert** z. B. „12" (Produkte im Bestand) | Länge der `products`-Liste aus `GET /api/grocy/stock` |
| **Kontext** Liste „Milch · 2 l · MHD 20.10.2026 · in Ordnung" | `name`, `amount`, `unit`, `best_before_date` |
| **Ziel** „Ziel nichts abgelaufen · 1 abgelaufen, 2 bald fällig" | `is_expired` + `best_before_date` gegen `expiring_days` |

Jeder Eintrag trägt eine Zustands-Glyphe (⚠ abgelaufen, ⏳ bald fällig, ● in
Ordnung, ○ ohne MHD) **und** den Status als Text (I.2 — nie allein über Farbe).
Die Liste ist nach frühestem MHD sortiert (ohne MHD zuletzt) und scrollt im
Widget, damit der Rahmen nicht springt (I.4).

## Verhalten (E.3)

- `render()` baut das DOM **einmalig** auf, ohne Netzwerkzugriff.
- `refresh()` lädt `GET /api/grocy/stock` über `api.fetchJson` und tauscht nur
  die Inhalte aus (kein Layout-Sprung, I.4). Der `⟳`-Knopf lädt sofort neu.
- `onConfigChange()` übernimmt ein geändertes `expiring_days`/`limit`.
- `destroy()` entfernt den Takt-Timer und leert den Container.
- **Fehlervertrag:** Bei nicht erreichbarer Quelle
  (`{"error_code":"GROCY_UNAVAILABLE"}`) wirft `refresh()` nicht, sondern zeigt
  den zuletzt bekannten Wert (als „Cache veraltet"/„Quelle nicht erreichbar"
  gekennzeichnet) oder einen leeren Zustand („—", „Kein Bestand"). Ein leeres
  Ergebnis (`products: []`) ist **kein** Fehler (leerer Zustand). Der Host
  stürzt nicht ab.
- Netzwerk läuft ausschließlich über `api.fetchJson` (E.5) — kein direktes
  `fetch` im Widget.

## Konfiguration

`config_schema` (E.2): `expiring_days` (Integer ≥ 0, Standard 3; MHD innerhalb
dieser Tage gilt als „bald fällig") und `limit` (Integer 1–50, Standard 8;
maximal angezeigte Produkte). Nicht gesetzte Werte fallen auf die Standards in
`frontend/widget.js` zurück.

## Hinweis Integration (Plan 10.2)

Der Adapter ist pull-only: das Backend ruft Grocy und puffert in `api_cache`
(`grocy:stock`, TTL 600 s, Env `GROCY_URL`/`GROCY_API_KEY`). Der Router
`backend/app/routers/grocy.py` und die Registrierung in `backend/app/main.py`
sowie die Permission-Spiegelung `grocy` in `frontend/js/widget-runtime.js` und
das Polling (`frontend/js/app.js`, `BUILTIN_WIDGETS`) sind gemeinsame Schritte
und werden laut Plan im Integrationsschritt zusammengeführt.
