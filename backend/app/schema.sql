-- Vollstaendiges SQLite-Schema (Spezifikation Abschnitt C).
-- Idempotent anwendbar beim Backend-Start (CREATE TABLE IF NOT EXISTS).

-- Wird pro Verbindung gesetzt (db.py), hier als verbindliche Vorgabe dokumentiert:
--   PRAGMA journal_mode = WAL;
--   PRAGMA busy_timeout = 5000;      -- Millisekunden
--   PRAGMA foreign_keys = ON;
--   PRAGMA synchronous = NORMAL;     -- im WAL-Modus sicher

-- Zeitzonenkonvention: alle Zeitstempel als UTC im ISO-8601-Format
-- '%Y-%m-%dT%H:%M:%SZ'. Daten ('date') als '%Y-%m-%d'.

CREATE TABLE IF NOT EXISTS widgets (
    id            TEXT PRIMARY KEY,                  -- z.B. 'garmin-sleep'
    name          TEXT NOT NULL,
    version       TEXT NOT NULL,                     -- SemVer
    author        TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT 'general',
    icon          TEXT NOT NULL DEFAULT '',
    is_builtin    INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0,1)),
    is_enabled    INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
    max_instances INTEGER NOT NULL DEFAULT 1 CHECK (max_instances >= 1),
    manifest_json TEXT NOT NULL,                     -- vollstaendiges manifest.json
    config_json   TEXT NOT NULL DEFAULT '{}',        -- aktuelle Default-Config (JSON)
    installed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS tabs (
    id       TEXT PRIMARY KEY,                       -- 'dashboard' | 'todos' | 'widgets'
    slug     TEXT NOT NULL UNIQUE,
    title    TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);

CREATE TABLE IF NOT EXISTS dashboard_layout (
    id              TEXT PRIMARY KEY,                -- Instanz-ID (UUID)
    widget_id       TEXT NOT NULL REFERENCES widgets(id) ON DELETE CASCADE,
    tab_id          TEXT NOT NULL REFERENCES tabs(id) ON DELETE CASCADE,
    x INTEGER NOT NULL DEFAULT 0 CHECK (x >= 0),
    y INTEGER NOT NULL DEFAULT 0 CHECK (y >= 0),
    w INTEGER NOT NULL DEFAULT 2 CHECK (w >= 1),
    h INTEGER NOT NULL DEFAULT 2 CHECK (h >= 1),
    config_override TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (tab_id, x, y)
);

CREATE TABLE IF NOT EXISTS hermes_tasks (
    id           TEXT PRIMARY KEY,                   -- UUID
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done')),
    priority     TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high')),
    source       TEXT NOT NULL DEFAULT 'hermes' CHECK (source IN ('hermes','local')),
    version      INTEGER NOT NULL DEFAULT 1,         -- optimistische Sperre
    due_at       TEXT,                               -- ISO-Datum oder NULL
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_hermes_tasks_status ON hermes_tasks(status);
CREATE INDEX IF NOT EXISTS idx_hermes_tasks_priority ON hermes_tasks(priority);

CREATE TABLE IF NOT EXISTS garmin_cache (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL CHECK (kind IN ('sleep','readiness','workout','vitals')),
    date         TEXT NOT NULL,                      -- '%Y-%m-%d' (Bezugsdatum)
    payload_json TEXT NOT NULL,
    fetched_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (kind, date)
);
CREATE INDEX IF NOT EXISTS idx_garmin_cache_kind_date ON garmin_cache(kind, date DESC);

CREATE TABLE IF NOT EXISTS api_cache (
    key         TEXT PRIMARY KEY,                    -- z.B. 'weather:current:<lat>,<lon>'
    payload_json TEXT NOT NULL,
    fetched_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dashboard_state (
    key        TEXT PRIMARY KEY,                     -- 'active_tab', 'brightness', 'screen'
    value      TEXT NOT NULL,                        -- JSON-kodiert
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
