# weather

Wetter-Widget (WP-4, Spezifikation D.6 / E).

| Datei | Zweck |
|---|---|
| `manifest.json` | Widget-Metadaten (E.2), `category: "weather"`, `permissions: ["weather"]`, `default_size` 4×3, `min_size` 3×3 |
| `frontend/widget.js` | ES-Modul, `default`-Export der Widget-Klasse (E.3) |
| `frontend/style.css` | Styles, gescoped über `.wg-weather` |
| `frontend/icon.svg` | Icon |

## Kachel-Aufbau (fünf Sekunden)

| Bestandteil | Quelle |
|---|---|
| **Label** „Temperatur" | Widget |
| **Wert** z. B. „12,3 °C" | `temperature_c` aus `/api/weather/current` |
| **Kontext** „Stand vor 3 min · Open-Meteo" + Luftfeuchte/Wind | `fetched_at`, `humidity`, `wind_kmh` |
| **Ziel** „Wohlfühlbereich 18–26 °C · im Bereich" | Config `comfort_min_c`/`comfort_max_c` (Standard 18/26), Bewertung des Werts |

Der Richtungs-/Statusanteil (unter / im / über dem Wohlfühlbereich) wird
**immer** als Glyphe (▼ ● ▲) **und** Text ausgegeben, nie allein über Farbe
(I.2: rot/grün allein ist für ~8 % der Männer unbrauchbar).

## Verhalten (E.3)

- `render()` baut das DOM **einmalig** auf, ohne Netzwerkzugriff.
- `refresh()` lädt `GET /api/weather/current` über `api.fetchJson` und tauscht
  nur die Inhalte aus (kein Layout-Sprung, I.4). Der Host pollt alle 10 min
  (`pollMs` in `frontend/js/app.js`, Spec I.4); der `⟳`-Knopf lädt sofort neu.
- `onConfigChange()` übernimmt einen geänderten Wohlfühlbereich und rendert neu.
- `destroy()` entfernt den Takt-Timer und leert den Container.
- **Fehlervertrag:** Bei Provider-Ausfall (`{"error_code":
  "WEATHER_UNAVAILABLE"}`) oder fehlender `temperature_c` wirft `refresh()`
  nicht, sondern zeigt den zuletzt bekannten Wert (als „Cache
  veraltet"/„Provider nicht erreichbar" gekennzeichnet) oder einen leeren
  Zustand (`—`, „Keine Wetterdaten"). Der Poll läuft weiter, der Host stürzt
  nicht ab.
- Netzwerk läuft ausschließlich über `api.fetchJson` (E.5) — kein direktes
  `fetch` im Widget.

## Konfiguration

`config_schema` (E.2, frei): `comfort_min_c` und `comfort_max_c` (Zahl, °C,
Standard 18/26). Sie setzen die Ziel-Linie der Kachel; sind sie nicht gesetzt,
gelten die Standards aus `frontend/widget.js`.
