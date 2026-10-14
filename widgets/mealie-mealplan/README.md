# mealie-mealplan

Wochenplan-Widget (spec-phase2 D/WP-P2-3, Pull-Adapter nach D12).

| Datei | Zweck |
|---|---|
| `manifest.json` | Widget-Metadaten (E.2), `category: "general"`, `permissions: ["mealie"]`, `default_size` 4×5, `min_size` 3×3 |
| `frontend/widget.js` | ES-Modul, `default`-Export der Widget-Klasse (E.3) |
| `frontend/style.css` | Styles, gescoped über `.wg-mealie-mealplan` |
| `frontend/icon.svg` | Icon |

## Datenquelle

- **Endpunkt:** `GET /api/mealie/mealplan?days=<1..7>` (Backend-Adapter, Pull).
- **Quelle (Backend):** `GET {MEALIE_URL}/api/groups/mealplans/this-week` (Mealie v2),
  Auth per `Authorization: Bearer <MEALIE_API_TOKEN>`, gepuffert in `api_cache`
  (Schlüssel `mealie:mealplan:<YYYY-MM-DD>`, TTL `MEALIE_CACHE_TTL_SECONDS` = 600 s).
- **Antwort-Schema:** `{ "meals": [ { "date", "meal_type", "recipe_id",
  "recipe_name", "servings" } ], "fetched_at": "…Z" }`; bei nicht erreichbarer
  Quelle ohne Cache: `{"meals": [], "error_code": "MEALIE_UNAVAILABLE"}`.

## Kachel-Aufbau (fünf Sekunden)

| Bestandteil | Quelle |
|---|---|
| **Label** „Wochenplan" | Widget |
| **Wert** z. B. „12" (geplante Mahlzeiten) | Länge der `meals`-Liste aus `GET /api/mealie/mealplan` |
| **Kontext** Liste „Mi 14.10. · Frühstück · Overnight Oats · 1 Portion" | `date`, `meal_type`, `recipe_name`, `servings` |
| **Ziel** „Ziel ≥ 7 Mahlzeiten · unter Ziel (4/7)" | Config `min_meals` gegen die Anzahl |

Jeder Eintrag trägt eine Typ-Glyphe **und** den Typ als Text (I.2 — nie allein
über Farbe). Die Bewertung „unter Ziel" / „Ziel erreicht" wird ebenfalls als
Glyphe (▼ ●) **und** Text ausgegeben; die Zeitspanne jedes Tages steht als
`<time datetime>` (Accessibility). Die Liste ist nach Tag und dann nach
Mahlzeitentyp (Frühstück → Mittag → Abend → …) sortiert und scrollt im Widget,
damit der Rahmen nicht springt (I.4).

Die Ziel-Linie ist eine **Untergrenze** („mindestens N geplante Mahlzeiten"),
darum gibt es kein „über Ziel": mehr Planung ist keine Abweichung.

## Verhalten (E.3)

- `render()` baut das DOM **einmalig** auf, ohne Netzwerkzugriff.
- `refresh()` lädt `GET /api/mealie/mealplan?days=<config.days>` über
  `api.fetchJson` und tauscht nur die Inhalte aus (kein Layout-Sprung, I.4). Der
  `⟳`-Knopf lädt sofort neu. Ein Poll-Takt wird im Widget nicht gesetzt; der Host
  bestimmt ihn (`pollMs` in `frontend/js/app.js`, Integrationsschritt).
- `onConfigChange()` übernimmt geänderte `days`/`min_meals`/`limit` und rendert neu.
- `destroy()` leert den Container.
- **Fehlervertrag:** Bei nicht erreichbarer Quelle
  (`{"error_code":"MEALIE_UNAVAILABLE"}`) oder Netzfehler wirft `refresh()` nicht,
  sondern zeigt den zuletzt bekannten Wert (als „Cache veraltet"/„Quelle nicht
  erreichbar" gekennzeichnet) oder einen leeren Zustand („—", „Keine Mahlzeiten
  geplant"). Ein leeres Ergebnis (`meals: []`) ist **kein** Fehler. Der Host
  stürzt nicht ab.
- Netzwerk läuft ausschließlich über `api.fetchJson` (E.5) — kein direktes
  `fetch` im Widget.

## Konfiguration

`config_schema` (E.2):

| Feld | Typ | Standard | Bedeutung |
|---|---|---|---|
| `days` | integer (1–7) | `7` | Zeitraum ab heute für Wert und Liste |
| `min_meals` | integer (≥ 1) | `7` | Ziel-Linie: mindestens so viele geplante Mahlzeiten |
| `limit` | integer (1–50) | `12` | maximal in der Liste angezeigte Mahlzeiten |

Sind `days` und `min_meals` nicht gesetzt, gelten die Standards (7 / 7).
