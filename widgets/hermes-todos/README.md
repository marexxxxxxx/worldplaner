# hermes-todos

Widget für die Hermes-To-Dos (Tabelle `hermes_tasks`, Spezifikation D.4 / WP-7).

| Datei | Zweck |
|---|---|
| `manifest.json` | Widget-Metadaten (E.2), `permissions: ["tasks"]`, `default_size` 12×6 |
| `frontend/widget.js` | ES-Modul, `default`-Export der Widget-Klasse (E.3) |
| `frontend/style.css` | Styles, gescoped über `.wg-hermes-todos` |
| `frontend/icon.svg` | Icon |

## Verhalten

- `render()` baut die Liste **einmalig** auf und abonniert die SSE-Events
  `task.created`, `task.updated`, `task.deleted`.
- `refresh()` lädt `GET /api/tasks?status=<filter>&limit=100` und tauscht nur
  den Listeninhalt aus (kein Layout-Sprung, I.4).
- Abhaken ruft `PATCH /api/tasks/{id}` mit `{ status, base_version }`. Bei
  `409 CONFLICT` (Hermes war schneller) wird neu geladen und ein Hinweis gezeigt.
- Netzwerkzugriff läuft ausschließlich über das Plugin-API (`api.fetchJson`,
  `api.subscribe`), nie über direktes `fetch`.

## Konfiguration

`config_schema.filter` (`open` | `done` | `all`, Default `open`).
