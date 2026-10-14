# garmin-workout

Workout-Widget (WP-6, Spezifikation D.5 / G.5 / E).

| Datei | Zweck |
|---|---|
| `manifest.json` | Widget-Metadaten (E.2), `category: "health"`, `permissions: ["garmin_cache"]`, `default_size` 6×5, `min_size` 4×3 |
| `frontend/widget.js` | ES-Modul, `default`-Export der Widget-Klasse (E.3) |
| `frontend/style.css` | Styles, gescoped über `.wg-garmin-workout` |
| `frontend/icon.svg` | Icon |

## Kachel-Aufbau (fünf Sekunden)

| Bestandteil | Quelle |
|---|---|
| **Label** „Workouts" | Widget |
| **Wert** „2" (Aktivitäten der letzten 7 Tage) | `start_time`/`date` der Einträge aus `GET /api/garmin/workouts` (G.5 `kind=workout`) |
| **Kontext** Liste „Di. 13.10. · 07:15 · Laufen · 40 min · 5,21 km · 412 kcal · Puls Ø 149/171" | `activity_name`, `activity_type`, `start_time`, `duration_s`, `distance_m`, `calories`, `avg_hr`, `max_hr` |
| **Ziel** „Ziel 3 pro Woche · ● Ziel erreicht (2/3)" | Config `weekly_target` (Standard 3) |

Jeder Eintrag trägt eine Typ-Glyphe **und** die Typ-Beschriftung als Text; die
Wochenbewertung zusätzlich Glyphe (▼ ● ▲) **und** Text (I.2, graustufenfest).
Die Liste scrollt im Widget, damit der Rahmen nicht springt (I.4).

## Verhalten (E.3)

- `render()` baut das DOM **einmalig** auf, ohne Netzwerkzugriff.
- `refresh()` lädt `GET /api/garmin/workouts?limit=<config.limit>` über
  `api.fetchJson` und tauscht nur die Inhalte aus (kein Layout-Sprung, I.4).
  Der Host pollt alle 5 min (`pollMs` in `frontend/js/app.js`, Spec I.4); der
  `⟳`-Knopf lädt sofort neu.
- `onConfigChange()` übernimmt ein geändertes Wochenziel/`limit`.
- `destroy()` leert den Container.
- **Fehlervertrag:** Leerer Cache (`{"workouts":[],"stale":true}`) zeigt den
  leeren Zustand („—", „Keine Workouts im Cache"), kein Fehler, der Poll läuft
  weiter. `stale:true` bei vorhandenen Einträgen kennzeichnet „Cache veraltet".
- Netzwerk läuft ausschließlich über `api.fetchJson` (E.5) — kein direktes
  `fetch` im Widget.

## Konfiguration

`config_schema`: `weekly_target` (Integer ≥ 1, Standard 3) und `limit`
(Integer 1–50, Standard 10; entspricht der Grenze aus D.5). Nicht gesetzte
Werte fallen auf die Standards in `frontend/widget.js` zurück.
