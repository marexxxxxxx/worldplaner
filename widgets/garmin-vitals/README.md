# garmin-vitals

Vitalwerte-Widget (WP-6, Spezifikation D.5 / G.5 / E).

| Datei | Zweck |
|---|---|
| `manifest.json` | Widget-Metadaten (E.2), `category: "health"`, `permissions: ["garmin_cache"]`, `default_size` 4×3, `min_size` 3×3 |
| `frontend/widget.js` | ES-Modul, `default`-Export der Widget-Klasse (E.3) |
| `frontend/style.css` | Styles, gescoped über `.wg-garmin-vitals` |
| `frontend/icon.svg` | Icon |

## Kachel-Aufbau (fünf Sekunden)

| Bestandteil | Quelle |
|---|---|
| **Label** „Vitalwerte" | Widget |
| **Wert** „8.432" (Schritte) | `steps` aus `GET /api/garmin/vitals` (G.5 `kind=vitals`) |
| **Kontext** „14.10.2026 · Ruhepuls 58/min · Stress 34/100 · Body Battery 72 · Etagen 6 · Aktiv 312 kcal · Garmin · Cache aktuell" | `resting_hr`, `stress_avg`, `body_battery`, `floors_climbed`, `calories_active`, `date`, `stale` |
| **Ziel** „Ziel 10.000 Schritte · ● Ziel erreicht (84 %)" | Config `steps_target` (Standard 10000) mit Fortschrittsbalken |

Der Fortschrittsbalken zeigt die Schritte gegen das Ziel; die Bewertung wird
zusätzlich als Glyphe (▼ ● ▲) **und** Text ausgegeben (I.2, graustufenfest).

## Verhalten (E.3)

- `render()` baut das DOM **einmalig** auf, ohne Netzwerkzugriff.
- `refresh()` lädt `GET /api/garmin/vitals` über `api.fetchJson` und tauscht nur
  die Inhalte aus (kein Layout-Sprung, I.4). Der Host pollt alle 5 min
  (`pollMs` in `frontend/js/app.js`, Spec I.4); der `⟳`-Knopf lädt sofort neu.
- `onConfigChange()` übernimmt ein geändertes Schrittziel.
- `destroy()` leert den Container.
- **Fehlervertrag:** Leerer Cache (`{"data":null,"stale":true}`) oder fehlende
  `steps` zeigen den leeren Zustand (`—`, „Keine Vitalwerte"), der Poll läuft
  weiter. `stale:true` bei vorhandenen Daten kennzeichnet „Cache veraltet" —
  kein Fehler.
- Netzwerk läuft ausschließlich über `api.fetchJson` (E.5) — kein direktes
  `fetch` im Widget.

## Konfiguration

`config_schema`: `steps_target` (Integer ≥ 1, Standard 10000). Er setzt die
Ziel-Linie der Kachel; ist er nicht gesetzt, gilt der Standard aus
`frontend/widget.js`.
