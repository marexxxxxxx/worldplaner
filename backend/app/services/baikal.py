"""Baikal-Kalender (spec-phase2 D/WP-P2-1), Pull + api_cache-Pufferung.

Pull-Adapter nach dem Wetter-/Grocy-Muster (D12): das Backend fragt die
CalDAV-Quelle per `REPORT calendar-query` (RFC 4791) ab, normalisiert die
VEVENTs und puffert sie in `api_cache` (Schluessel `baikal:events:<YYYY-MM-DD>`,
ein Schluessel je Tag). Das Widget pollt `GET /api/baikal/events`.

Bei Provider-Fehler (nicht erreichbar, 401, fehlende Credentials) wird der
letzte Cache-Wert geliefert, sonst `200` mit `error_code`
`BAIKAL_UNAVAILABLE` — nie ein 5xx-Absturz. Auth ist HTTP Basic; kein OAuth,
kein Token-Refresh.

Dieser Service ist selbststaendig (D13): die gemeinsame `config.py` wird laut
Plan 10.2 additiv erst im Integrationsschritt erweitert; bis dahin liest der
Adapter die dokumentierten Env-Variablen direkt (Vorrang hat ein spaeterer
Settings-Wert).
"""

import base64
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from ..config import get_settings

DEFAULT_CACHE_TTL_SECONDS = 600
DEFAULT_DAYS_AHEAD = 7
DEFAULT_TIMEOUT_SECONDS = 10

# CalDAV: Muster fuer den Cache-Schluessel (ein Schluessel je Tag, C.2).
CACHE_KEY_PREFIX = "baikal:events"

# Fehlerobjekt ohne 5xx (spec-phase2 D/WP-P2-1). `events` bleibt eine Liste.
UNAVAILABLE: dict[str, Any] = {"events": [], "error_code": "BAIKAL_UNAVAILABLE"}

# CalDAV-REPORT (RFC 4791): alle VEVENTs im Zeitfenster [start, end).
CALDAV_QUERY = """<?xml version="1.0" encoding="utf-8" ?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
    <c:calendar-data/>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start}" end="{end}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

_DAV = "{DAV:}"
_CALDAV = "{urn:ietf:params:xml:ns:caldav}"

_SUMMARY_FALLBACK = "(ohne Titel)"

# iCalendar-Textwerte: Escapes nach RFC 5545.
_TEXT_ESCAPES = {"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    """UTC-ISO-8601 in der verbindlichen Konvention '%Y-%m-%dT%H:%M:%SZ'."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _caldav_stamp(moment: datetime) -> str:
    """CalDAV-time-range-Format (UTC): '%Y%m%dT%H%M%SZ'."""
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _config() -> dict[str, Any]:
    """Baikal-Einstellungen (spec-phase2 D/WP-P2-1).

    Nutzt einen vorhandenen Settings-Wert, sonst die dokumentierte
    Env-Variable, sonst den Default.
    """
    settings = get_settings()

    def text(attribute: str, env_name: str, default: str) -> str:
        value = getattr(settings, attribute, None)
        if value is None:
            value = os.environ.get(env_name, default)
        return str(value)

    def number(attribute: str, env_name: str, default: int) -> int:
        try:
            return int(text(attribute, env_name, str(default)))
        except ValueError:
            return default

    return {
        "url": text("baikal_url", "BAIKAL_URL", ""),
        "username": text("baikal_username", "BAIKAL_USERNAME", ""),
        "password": text("baikal_password", "BAIKAL_PASSWORD", ""),
        "cache_ttl_seconds": number(
            "baikal_cache_ttl_seconds", "BAIKAL_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS
        ),
        "days_ahead": number("baikal_days_ahead", "BAIKAL_DAYS_AHEAD", DEFAULT_DAYS_AHEAD),
        "timeout_seconds": number(
            "baikal_timeout_seconds", "BAIKAL_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
        ),
    }


# --- iCalendar-Auswertung ---------------------------------------------------

def _unfold(text: str) -> list[str]:
    """Faltet ICS-Zeilen nach RFC 5545 auf (Folgezeilen beginnen mit Space/Tab)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded: list[str] = []
    for line in lines:
        if line[:1] in (" ", "\t") and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _unescape(value: str) -> str:
    """Loest iCalendar-Text-Escapes auf (\\n, \\,, \\;, \\\\)."""
    result: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            result.append(_TEXT_ESCAPES.get(value[index + 1], value[index + 1]))
            index += 2
        else:
            result.append(char)
            index += 1
    return "".join(result)


def _param(params: str, name: str) -> str | None:
    """Wert eines Inhaltszeilen-Parameters, z. B. `TZID` aus `;TZID=Europe/Berlin`."""
    for part in params.split(";"):
        key, separator, value = part.partition("=")
        if separator and key.strip().upper() == name.upper():
            return value
    return None


def parse_ics_events(ics: str) -> list[dict[str, list[tuple[str, str]]]]:
    """Alle VEVENTs eines ICS-Texts als `{PROPERTY: [(params, value), ...]}`.

    Nur Eigenschaften direkt auf VEVENT-Ebene (Tiefe 1) werden erfasst, damit
    verschachtelte Komponenten (VALARM) die Terminfelder nicht ueberschreiben.
    """
    events: list[dict[str, list[tuple[str, str]]]] = []
    depth = 0
    current: list[str] | None = None
    for line in _unfold(ics):
        marker = line.strip().upper()
        if marker.startswith("BEGIN:"):
            if current is None and marker == "BEGIN:VEVENT":
                current = []
                depth = 1
            elif current is not None:
                depth += 1
            continue
        if marker.startswith("END:"):
            if current is not None:
                depth -= 1
                if marker == "END:VEVENT" and depth <= 0:
                    events.append(_to_props(current))
                    current = None
                    depth = 0
            continue
        if current is not None and depth == 1:
            current.append(line)
    return events


def _to_props(lines: list[str]) -> dict[str, list[tuple[str, str]]]:
    props: dict[str, list[tuple[str, str]]] = {}
    for line in lines:
        if ":" not in line:
            continue
        head, _, value = line.partition(":")
        name, _, params = head.partition(";")
        props.setdefault(name.strip().upper(), []).append((params, value))
    return props


def _first(
    props: dict[str, list[tuple[str, str]]], name: str
) -> tuple[str, str] | None:
    values = props.get(name)
    return values[0] if values else None


def _parse_datetime(params: str, value: str) -> tuple[datetime, bool]:
    """`DTSTART`/`DTEND` -> (UTC-Zeitpunkt, ganztags).

    Datumswerte (`VALUE=DATE`) werden auf Mitternacht UTC normalisiert; `Z`-Werte
    sind bereits UTC; `TZID`-Werte werden ueber zoneinfo nach UTC gerechnet.
    """
    value = value.strip()
    if "VALUE=DATE" in params.upper() or (len(value) == 8 and value.isdigit()):
        moment = datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
        return moment, True
    if value.endswith("Z"):
        moment = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc), False
    naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    zone = timezone.utc
    tzid = _param(params, "TZID")
    if tzid:
        try:
            zone = ZoneInfo(tzid)
        except (KeyError, ValueError):
            zone = timezone.utc
    return naive.replace(tzinfo=zone).astimezone(timezone.utc), False


def normalize_event(
    props: dict[str, list[tuple[str, str]]], href: str = ""
) -> dict[str, Any] | None:
    """Ein VEVENT -> Normalform (spec-phase2 D/WP-P2-1).

    Pflichtfeld `start`: fehlt `DTSTART`, wird der Termin verworfen. `end` fehlt
    -> `start`; `summary` leer -> "(ohne Titel)"; `location` fehlt -> "".
    `uid` faellt auf den `href` zurueck.
    """
    start_prop = _first(props, "DTSTART")
    if start_prop is None:
        return None
    start, all_day = _parse_datetime(*start_prop)

    end_prop = _first(props, "DTEND")
    end = _parse_datetime(*end_prop)[0] if end_prop is not None else start

    uid_prop = _first(props, "UID")
    uid = _unescape(uid_prop[1]).strip() if uid_prop is not None else href
    summary_prop = _first(props, "SUMMARY")
    summary = _unescape(summary_prop[1]).strip() if summary_prop is not None else ""
    location_prop = _first(props, "LOCATION")
    location = _unescape(location_prop[1]).strip() if location_prop is not None else ""

    return {
        "uid": uid,
        "summary": summary or _SUMMARY_FALLBACK,
        "start": _iso(start),
        "end": _iso(end),
        "all_day": all_day,
        "location": location,
    }


def normalize_events(xml_text: str) -> list[dict[str, Any]]:
    """CalDAV-`multistatus`-Antwort -> sortierte Ereignisliste.

    Je `<response>` wird `calendar-data` ausgewertet; ohne `UID` dient der
    `href` als `uid`. Sortierung: Start, dann Ende, dann Titel.
    """
    root = ElementTree.fromstring(xml_text)
    events: list[dict[str, Any]] = []
    for response in root.iter(f"{_DAV}response"):
        href = (response.findtext(f"{_DAV}href") or "").strip()
        data = response.find(f".//{_CALDAV}calendar-data")
        if data is None or not data.text:
            continue
        for props in parse_ics_events(data.text):
            event = normalize_event(props, href)
            if event is not None:
                events.append(event)
    events.sort(key=lambda item: (item["start"], item["end"], item["summary"]))
    return events


# --- Provider (in Tests ersetzbar) -----------------------------------------

def fetch_events(
    base_url: str,
    username: str,
    password: str,
    days: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Fragt die CalDAV-Quelle per `REPORT calendar-query` ab (RFC 4791).

    Fehlende `BAIKAL_URL` -> `ValueError` (derselbe „leer statt Absturz"-Pfad).
    """
    if not base_url:
        raise ValueError("BAIKAL_URL ist nicht gesetzt")

    now = _now()
    body = CALDAV_QUERY.format(
        start=_caldav_stamp(now),
        end=_caldav_stamp(now + timedelta(days=int(days))),
    )
    credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    response = httpx.request(
        "REPORT",
        base_url,
        content=body.encode("utf-8"),
        headers={
            "Authorization": f"Basic {credentials}",
            "Depth": "1",
            "Content-Type": "application/xml; charset=utf-8",
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return {"events": normalize_events(response.text), "fetched_at": _iso(now)}


# --- api_cache --------------------------------------------------------------

def _cache_key() -> str:
    """Ein Schluessel je Tag (C.2)."""
    return f"{CACHE_KEY_PREFIX}:{_now().strftime('%Y-%m-%d')}"


def _read_cache(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload_json FROM api_cache WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"])


def _read_fresh(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload_json, expires_at FROM api_cache WHERE key = ?", (key,)
    ).fetchone()
    if row is None or row["expires_at"] <= _iso(_now()):
        return None
    return json.loads(row["payload_json"])


def _store(conn: sqlite3.Connection, key: str, payload: Any, ttl_seconds: int) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO api_cache (key, payload_json, fetched_at, expires_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET payload_json = excluded.payload_json, "
        "fetched_at = excluded.fetched_at, expires_at = excluded.expires_at",
        (key, json.dumps(payload), _iso(now), _iso(now + timedelta(seconds=ttl_seconds))),
    )


# --- oeffentliche API -------------------------------------------------------

def events(conn: sqlite3.Connection, days: int | None = None) -> dict[str, Any]:
    """Kommende Ereignisse: frischer Cache, sonst Provider; bei Fehler letzter Cache."""
    config = _config()
    if days is None:
        days = config["days_ahead"]
    key = _cache_key()
    cached = _read_fresh(conn, key)
    if cached is not None:
        return cached
    try:
        payload = fetch_events(
            config["url"],
            config["username"],
            config["password"],
            days,
            config["timeout_seconds"],
        )
    except Exception:
        last = _read_cache(conn, key)
        return last if last is not None else dict(UNAVAILABLE)
    _store(conn, key, payload, config["cache_ttl_seconds"])
    return payload
