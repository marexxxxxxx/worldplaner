# garmin-readiness

Readiness-Widget (WP-6, Spezifikation D.5 / G.5 / E; Readiness-Ersatz nach D11).

| Datei | Zweck |
|---|---|
| `manifest.json` | Widget-Metadaten (E.2), `category: "health"`, `permissions: ["garmin_cache"]`, `default_size` 4×3, `min_size` 3×3 |
| `frontend/widget.js` | ES-Modul, `default`-Export der Widget-Klasse (E.3) |
| `frontend/style.css` | Styles, gescoped über `.wg-garmin-readiness` |
| `frontend/icon.svg` | Icon |

## Kachel-Aufbau (fünf Sekunden)

| Bestandteil | Quelle |
|---|---|
| **Label** „Readiness" | Widget |
| **Wert** „72 / 100" | `body_battery` aus `GET /api/garmin/readiness` (G.5 `kind=readiness`) |
| **Kontext** „14.10.2026 · Stress 34/100 · Ruhepuls 58/min · Sleep-Score 84/100 · Garmin · Cache aktuell" | `stress_avg`, `resting_hr`, `sleep_score`, `date`, `stale` |
| **Ziel** „Ziel ≥ 50 · ● Ziel erreicht" | Config `body_battery_target` (Standard 50), Bewertung des Werts |

Die Tagesstreuung (`body_battery_low` … `body_battery_high`) steht als Balken
auf einer 0–100-Achse: Spanne, Ziel-Linie und Marke des aktuellen Werts sind
zusätzlich **per Position** unterscheidbar. Die Zielbewertung wird immer als
Glyphe (▼ ● ▲) **und** Text ausgegeben (I.2, graustufenfest).

## Verhalten (E.3)

- `render()` baut das DOM **einmalig** auf, ohne Netzwerkzugriff.
- `refresh()` lädt `GET /api/garmin/readiness` über `api.fetchJson` und tauscht
  nur die Inhalte aus (kein Layout-Sprung, I.4). Der Host pollt alle 5 min
  (`pollMs` in `frontend/js/app.js`, Spec I.4); der `⟳`-Knopf lädt sofort neu.
- `onConfigChange()` übernimmt ein geändertes Body-Battery-Ziel.
- `destroy()` leert den Container.
- **Fehlervertrag:** Leerer Cache (`{"data":null,"stale":true}`) oder
  fehlendes `body_battery` zeigen den leeren Zustand (`—`, „Keine
  Readiness-Daten"), der Poll läuft weiter. `stale:true` bei vorhandenen Daten
  kennzeichnet „Cache veraltet" — kein Fehler.
- Netzwerk läuft ausschließlich über `api.fetchJson` (E.5) — kein direktes
  `fetch` im Widget.

## Konfiguration

`config_schema`: `body_battery_target` (Zahl, 0–100, Standard 50). Er setzt die
Ziel-Linie der Kachel; ist er nicht gesetzt, gilt der Standard aus
`frontend/widget.js`.
