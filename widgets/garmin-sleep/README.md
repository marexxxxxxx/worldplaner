# garmin-sleep

Schlaf-Widget (WP-6, Spezifikation D.5 / G.5 / E).

| Datei | Zweck |
|---|---|
| `manifest.json` | Widget-Metadaten (E.2), `category: "health"`, `permissions: ["garmin_cache"]`, `default_size` 4×3, `min_size` 3×3 |
| `frontend/widget.js` | ES-Modul, `default`-Export der Widget-Klasse (E.3) |
| `frontend/style.css` | Styles, gescoped über `.wg-garmin-sleep` |
| `frontend/icon.svg` | Icon |

## Kachel-Aufbau (fünf Sekunden)

| Bestandteil | Quelle |
|---|---|
| **Label** „Schlaf" | Widget |
| **Wert** „7 h 50 min" | `duration_s` aus `GET /api/garmin/sleep` (G.5 `kind=sleep`) |
| **Kontext** „13.10. 22:31 – 14.10. 06:42 · Sleep-Score 84/100 · Garmin · Cache aktuell" | `sleep_start`, `sleep_end`, `sleep_score`, `stale` |
| **Ziel** „Ziel 8 h · ● Ziel erreicht (98 %)" | Config `sleep_target_h` (Standard 8), Bewertung des Werts |

Die Schlafphasen (`deep_sleep_s`, `light_sleep_s`, `rem_sleep_s`, `awake_s`)
stehen als gestapelter Balken (vier Segmente) **mit Text-Legende** darunter —
die Aussage bleibt in Graustufen erkennbar. Die Zielbewertung wird immer als
Glyphe (▼ ● ▲) **und** Text ausgegeben, nie allein über Farbe (I.2).

## Verhalten (E.3)

- `render()` baut das DOM **einmalig** auf, ohne Netzwerkzugriff.
- `refresh()` lädt `GET /api/garmin/sleep` über `api.fetchJson` und tauscht nur
  die Inhalte aus (kein Layout-Sprung, I.4). Der Host pollt alle 5 min
  (`pollMs` in `frontend/js/app.js`, Spec I.4); der `⟳`-Knopf lädt sofort neu.
- `onConfigChange()` übernimmt ein geändertes Schlafziel und rendert neu.
- `destroy()` leert den Container.
- **Fehlervertrag:** Bei leerem Cache (`{"date":…,"data":null,"stale":true}`)
  oder fehlendem `duration_s` zeigt das Widget den leeren Zustand (`—`,
  „Keine Schlafdaten"), der Poll läuft weiter. `stale:true` bei vorhandenen
  Daten dämpft den Wert und kennzeichnet „Cache veraltet" — kein Fehler.
- Netzwerk läuft ausschließlich über `api.fetchJson` (E.5) — kein direktes
  `fetch` im Widget.

## Konfiguration

`config_schema`: `sleep_target_h` (Zahl, Stunden, Standard 8). Er setzt die
Ziel-Linie der Kachel; ist er nicht gesetzt, gilt der Standard aus
`frontend/widget.js`.
