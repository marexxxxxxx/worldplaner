"""Tests fuer `garmin-sync` (WP-5/B2): Payload-Schema G.5, Cache-Write,
Exponential-Backoff/Auth (G.4) und No-Credentials-Pfad (G.4/Abnahme B2.5)."""

import json
from datetime import datetime, timezone

import pytest
import sync as gs

DAY = "2026-10-14"


def _ms(iso: str) -> int:
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(moment.timestamp() * 1000)


# --- Rohantworten im Format von python-garminconnect (G.5-Herkunft) -----------

RAW_SLEEP = {
    "dailySleepDTO": {
        "sleepStartTimestampGMT": _ms("2026-10-13T22:31:00Z"),
        "sleepEndTimestampGMT": _ms("2026-10-14T06:42:00Z"),
        "sleepTimeSeconds": 29460,
        "deepSleepSeconds": 5280,
        "lightSleepSeconds": 15600,
        "remSleepSeconds": 6180,
        "awakeSleepSeconds": 2400,
        "sleepScores": {"overall": {"value": 84}},
    }
}

RAW_STRESS = {"avgStressLevel": 34, "maxStressLevel": 88}

RAW_BATTERY = [
    {
        "date": DAY,
        "bodyBatteryValuesArray": [
            [_ms("2026-10-14T07:00:00Z"), 23],
            [_ms("2026-10-14T12:00:00Z"), 72],
            [_ms("2026-10-14T21:00:00Z"), 61],
        ],
    }
]

RAW_SUMMARY = {
    "totalSteps": 8432,
    "restingHeartRate": 58,
    "floorsAscended": 6.0,
    "activeKilocalories": 312,
}

RAW_ACTIVITIES = [
    {
        "activityId": 1234567890,
        "activityType": {"typeKey": "running"},
        "activityName": "Morning Run",
        "startTimeGMT": "2026-10-13 07:15:00",
        "duration": 2412.0,
        "distance": 5210.5,
        "calories": 412,
        "averageHR": 149,
        "maxHR": 171,
    },
    {
        "activityId": 2222,
        "activityType": {"typeKey": "strength_training"},
        "activityName": None,
        "startTimeGMT": "2026-10-13T18:05:00.0",
        "duration": 1800.0,
        "distance": None,
        "calories": None,
        "averageHR": None,
        "maxHR": None,
    },
    {
        "activityId": 3333,
        "activityType": {"typeKey": "cycling"},
        "activityName": "Ride",
        "startTimeGMT": "2026-10-12 06:00:00",
        "duration": 3600.0,
        "distance": 30000.0,
        "calories": 900,
        "averageHR": 130,
        "maxHR": 160,
    },
]

EXPECTED_SLEEP = {
    "sleep_start": "2026-10-13T22:31:00Z",
    "sleep_end": "2026-10-14T06:42:00Z",
    "duration_s": 29460,
    "deep_sleep_s": 5280,
    "light_sleep_s": 15600,
    "rem_sleep_s": 6180,
    "awake_s": 2400,
    "sleep_score": 84,
}

EXPECTED_READINESS = {
    "body_battery": 61,
    "body_battery_high": 72,
    "body_battery_low": 23,
    "stress_avg": 34,
    "sleep_score": 84,
    "resting_hr": 58,
}

EXPECTED_VITALS = {
    "steps": 8432,
    "resting_hr": 58,
    "stress_avg": 34,
    "body_battery": 61,
    "floors_climbed": 6,
    "calories_active": 312,
}

WORKOUT_KEYS = {
    "date",
    "activity_id",
    "activity_type",
    "activity_name",
    "start_time",
    "duration_s",
    "distance_m",
    "calories",
    "avg_hr",
    "max_hr",
}

EXPECTED_WORKOUTS = {
    "2026-10-13": [
        {
            "date": "2026-10-13",
            "activity_id": "1234567890",
            "activity_type": "running",
            "activity_name": "Morning Run",
            "start_time": "2026-10-13T07:15:00Z",
            "duration_s": 2412.0,
            "distance_m": 5210.5,
            "calories": 412,
            "avg_hr": 149,
            "max_hr": 171,
        },
        {
            "date": "2026-10-13",
            "activity_id": "2222",
            "activity_type": "strength_training",
            "activity_name": None,
            "start_time": "2026-10-13T18:05:00Z",
            "duration_s": 1800.0,
            "distance_m": None,
            "calories": None,
            "avg_hr": None,
            "max_hr": None,
        },
    ],
    "2026-10-12": [
        {
            "date": "2026-10-12",
            "activity_id": "3333",
            "activity_type": "cycling",
            "activity_name": "Ride",
            "start_time": "2026-10-12T06:00:00Z",
            "duration_s": 3600.0,
            "distance_m": 30000.0,
            "calories": 900,
            "avg_hr": 130,
            "max_hr": 160,
        }
    ],
}

NOON = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    """Minimaler Ersatz-Garmin-Client (Abnahme B2.2)."""

    def __init__(self, **overrides):
        self.calls: list[tuple] = []
        self._overrides = overrides

    def login(self) -> None:
        self.calls.append(("login",))

    def _result(self, name, default):
        return self._overrides.get(name, default)

    def get_sleep_data(self, date):
        self.calls.append(("sleep", date))
        return self._result("sleep", RAW_SLEEP)

    def get_stress_data(self, date):
        self.calls.append(("stress", date))
        return self._result("stress", RAW_STRESS)

    def get_body_battery(self, date):
        self.calls.append(("battery", date))
        return self._result("battery", RAW_BATTERY)

    def get_user_summary(self, date):
        self.calls.append(("summary", date))
        return self._result("summary", RAW_SUMMARY)

    def get_activities(self, start, limit):
        self.calls.append(("activities", start, limit))
        return self._result("activities", RAW_ACTIVITIES)


def _config(**overrides) -> gs.Config:
    base = dict(
        email="user@example.com",
        password="secret",
        sync_interval_seconds=1200,
        backoff_base_seconds=60,
        backoff_max_attempts=5,
        backoff_cap_seconds=3600,
        token_dir="/data/token",
        db_path="/data/db/dashboard.db",
    )
    base.update(overrides)
    return gs.Config(**base)


# --- G.5: Payload-Schema exakt ------------------------------------------------


def test_normalize_sleep_matches_g5_exactly():
    payload = gs.normalize_sleep(RAW_SLEEP)
    assert set(payload) == {
        "sleep_start",
        "sleep_end",
        "duration_s",
        "deep_sleep_s",
        "light_sleep_s",
        "rem_sleep_s",
        "awake_s",
        "sleep_score",
    }
    assert payload == EXPECTED_SLEEP


def test_normalize_readiness_matches_g5_exactly():
    payload = gs.normalize_readiness(RAW_SUMMARY, RAW_BATTERY, RAW_STRESS, 84)
    assert set(payload) == {
        "body_battery",
        "body_battery_high",
        "body_battery_low",
        "stress_avg",
        "sleep_score",
        "resting_hr",
    }
    assert payload == EXPECTED_READINESS


def test_normalize_vitals_matches_g5_exactly():
    payload = gs.normalize_vitals(RAW_SUMMARY, RAW_BATTERY, RAW_STRESS)
    assert set(payload) == {
        "steps",
        "resting_hr",
        "stress_avg",
        "body_battery",
        "floors_climbed",
        "calories_active",
    }
    assert payload == EXPECTED_VITALS


def test_normalize_workouts_groups_by_date_and_matches_g5():
    grouped = gs.normalize_workouts(RAW_ACTIVITIES)
    assert grouped == EXPECTED_WORKOUTS
    for items in grouped.values():
        for item in items:
            assert set(item) == WORKOUT_KEYS


def test_optional_fields_are_present_but_null():
    # G.5: Felder mit optional = ja sind immer vorhanden, duerfen aber null sein.
    sleep = gs.normalize_sleep(
        {"dailySleepDTO": dict(RAW_SLEEP["dailySleepDTO"], sleepScores=None)}
    )
    assert "sleep_score" in sleep and sleep["sleep_score"] is None

    readiness = gs.normalize_readiness(RAW_SUMMARY, [], RAW_STRESS, None)
    assert readiness["body_battery"] is None
    assert readiness["body_battery_high"] is None
    assert readiness["body_battery_low"] is None

    workouts = gs.normalize_workouts(
        [
            {
                "activityId": None,
                "activityType": {},
                "startTimeGMT": "2026-10-13 07:15:00",
            }
        ]
    )
    assert set(workouts["2026-10-13"][0]) == WORKOUT_KEYS


# --- Cache-Write (Abnahme B2.2/B2.3) -----------------------------------------


def test_sync_once_writes_one_row_per_kind_with_fetched_at(conn):
    written = gs.sync_once(conn, FakeClient(), now=NOON)

    rows = [
        (row["kind"], row["date"], row["fetched_at"])
        for row in conn.execute("SELECT kind, date, fetched_at FROM garmin_cache")
    ]
    assert {kind for kind, _, _ in rows} == {"sleep", "readiness", "workout", "vitals"}
    for _, _, fetched_at in rows:
        assert fetched_at and fetched_at.endswith("Z")  # fetched_at ist gesetzt

    # Workouts: je Workout-Datum eine Zeile; die uebrigen kinds auf `DAY`.
    assert {kind for kind, date, _ in rows if date == DAY} == {
        "sleep",
        "readiness",
        "vitals",
    }
    assert {date for kind, date, _ in rows if kind == "workout"} == {
        "2026-10-13",
        "2026-10-12",
    }
    assert len(written) == len(rows) == 5


def test_sync_once_payloads_match_g5_in_db(conn):
    gs.sync_once(conn, FakeClient(), now=NOON)
    payloads = {
        (row["kind"], row["date"]): json.loads(row["payload_json"])
        for row in conn.execute("SELECT kind, date, payload_json FROM garmin_cache")
    }
    assert payloads[("sleep", DAY)] == EXPECTED_SLEEP
    assert payloads[("readiness", DAY)] == EXPECTED_READINESS
    assert payloads[("vitals", DAY)] == EXPECTED_VITALS
    assert payloads[("workout", "2026-10-13")] == EXPECTED_WORKOUTS["2026-10-13"]
    assert payloads[("workout", "2026-10-12")] == EXPECTED_WORKOUTS["2026-10-12"]


def test_sync_once_is_idempotent_upsert(conn, monkeypatch):
    gs.sync_once(conn, FakeClient(), now=NOON)
    monkeypatch.setattr(gs, "utc_now", lambda: "2026-10-14T13:00:00Z")
    gs.sync_once(conn, FakeClient(), now=NOON)
    rows = conn.execute("SELECT kind, date, fetched_at FROM garmin_cache").fetchall()
    assert len(rows) == 5  # kein Duplikat trotz zweitem Lauf
    assert {row["fetched_at"] for row in rows} == {"2026-10-14T13:00:00Z"}


def test_sync_once_propagates_client_errors(conn):
    class Failing(FakeClient):
        def get_sleep_data(self, date):
            raise gs.TransientError("573")

    with pytest.raises(gs.TransientError):
        gs.sync_once(conn, Failing(), now=NOON)

    class Unauthorized(FakeClient):
        def get_sleep_data(self, date):
            raise gs.AuthError("401")

    with pytest.raises(gs.AuthError):
        gs.sync_once(conn, Unauthorized(), now=NOON)


def test_sync_once_wraps_missing_table_as_transient(tmp_path):
    conn = gs.connect_db(str(tmp_path / "empty.db"))  # ohne schema.sql
    with pytest.raises(gs.TransientError):
        gs.sync_once(conn, FakeClient(), now=NOON)
    conn.close()


# --- G.4: Intervall & Exponential-Backoff ------------------------------------


def test_backoff_waits_are_exponential_and_capped_by_interval():
    config = _config()
    # min(cap=3600, interval=1200) = 1200; base*2^a fuer a=0..4
    assert gs.backoff_waits(config) == [60, 120, 240, 480, 960]


def test_backoff_waits_respect_smaller_cap():
    config = _config(backoff_base_seconds=600, backoff_cap_seconds=500)
    assert gs.backoff_waits(config) == [500, 500, 500, 500, 500]


class _StopLoop(Exception):
    pass


def _recorder(stop_after: int):
    calls: list[float] = []

    def sleeper(seconds: float) -> None:
        calls.append(seconds)
        if len(calls) >= stop_after:
            raise _StopLoop

    return calls, sleeper


class _FailingTransient(FakeClient):
    def get_sleep_data(self, date):
        raise gs.TransientError("Netzwerk/5xx")


class _FailingLoginTransient:
    def login(self):
        raise gs.TransientError("Login 503")


class _FailingLoginAuth:
    def login(self):
        raise gs.AuthError("401 Unauthorized")


def test_transient_error_logs_increasing_backoff_then_interval(conn, caplog):
    calls, sleeper = _recorder(6)  # 5 Backoff-Stufen + Intervall
    config = _config()
    with caplog.at_level("WARNING"), pytest.raises(_StopLoop):
        gs.run(config, conn, lambda _: _FailingTransient(), sleeper=sleeper)

    assert calls == [60, 120, 240, 480, 960, 1200]
    assert calls[:5] == sorted(calls[:5])  # Log zeigt steigende Wartezeit
    assert sum("Backoff" in record.getMessage() for record in caplog.records) == 5
    assert any(
        "Stop bis zum naechsten Intervall" in record.getMessage()
        for record in caplog.records
    )


def test_login_transient_error_uses_same_backoff(conn):
    calls, sleeper = _recorder(6)
    with pytest.raises(_StopLoop):
        gs.run(_config(), conn, lambda _: _FailingLoginTransient(), sleeper=sleeper)
    assert calls == [60, 120, 240, 480, 960, 1200]


def test_auth_error_logs_once_and_waits_interval(conn, caplog):
    calls, sleeper = _recorder(1)
    with caplog.at_level("ERROR"), pytest.raises(_StopLoop):
        gs.run(_config(), conn, lambda _: _FailingLoginAuth(), sleeper=sleeper)

    assert calls == [1200]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "Authentifizierung fehlgeschlagen" in errors[0].getMessage()


def test_translate_error_classification():
    class WithStatus(Exception):
        status_code = 401

    assert isinstance(gs.translate_error(WithStatus()), gs.AuthError)

    class Response:
        status_code = 503

    class WithResponse(Exception):
        response = Response()

    assert isinstance(gs.translate_error(WithResponse()), gs.TransientError)

    AuthClone = type("GarminConnectAuthenticationError", (Exception,), {})
    assert isinstance(gs.translate_error(AuthClone()), gs.AuthError)
    assert isinstance(gs.translate_error(RuntimeError("boom")), gs.TransientError)


# --- Abnahme B2.5: ohne Credentials kein Crash -------------------------------


def test_without_credentials_logs_once_and_idles(conn, caplog, tmp_path):
    calls, sleeper = _recorder(1)
    built: list[int] = []
    config = _config(email="", password="", token_dir=str(tmp_path / "no-token"))
    with caplog.at_level("WARNING"), pytest.raises(_StopLoop):
        gs.run(
            config,
            conn,
            lambda _: built.append(1) or FakeClient(),
            require_credentials=True,
            sleeper=sleeper,
        )

    assert calls == [1200]
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Keine Garmin-Credentials" in warnings[0].getMessage()
    assert built == []  # kein Client gebaut, kein Crash


def test_token_file_is_accepted_without_credentials(conn, tmp_path):
    token_dir = tmp_path / "token"
    token_dir.mkdir()
    (token_dir / "garmin_tokens.json").write_text("{}", encoding="utf-8")
    config = _config(email="", password="", token_dir=str(token_dir))
    # require_credentials greift nicht -> es wird ein echter Sync versucht.
    client = FakeClient()
    calls, sleeper = _recorder(1)
    with pytest.raises(_StopLoop):
        gs.run(config, conn, lambda _: client, sleeper=sleeper)
    assert ("login",) in client.calls
    assert calls == [1200]


# --- Konfiguration (G.2) -----------------------------------------------------

def test_load_config_defaults():
    config = gs.load_config({})
    assert config.sync_interval_seconds == 1200
    assert config.backoff_base_seconds == 60
    assert config.backoff_max_attempts == 5
    assert config.backoff_cap_seconds == 3600
    assert config.token_dir == "/data/token"
    assert config.db_path == "/data/db/dashboard.db"


def test_load_config_reads_env():
    config = gs.load_config(
        {
            "GARMIN_EMAIL": "a@b.c",
            "GARMIN_PASSWORD": "pw",
            "GARMIN_SYNC_INTERVAL_SECONDS": "900",
            "GARMIN_TOKEN_DIR": "/custom/token",
            "GARMIN_DB_PATH": "/custom/db.db",
        }
    )
    assert (config.email, config.password) == ("a@b.c", "pw")
    assert config.sync_interval_seconds == 900
    assert config.token_dir == "/custom/token"
    assert config.db_path == "/custom/db.db"


# --- Mock-Client (Verifikationspfad ohne echten Account, Abnahme B2.2) --------


def test_mock_client_produces_g5_payloads():
    client = gs.MockGarminClient()
    client.login()
    day = "2026-10-14"

    sleep = gs.normalize_sleep(client.get_sleep_data(day))
    assert set(sleep) == set(EXPECTED_SLEEP)
    assert sleep["sleep_start"] < sleep["sleep_end"]  # Einschlafen vor Aufwachen

    assert set(gs.normalize_readiness(
        client.get_user_summary(day),
        client.get_body_battery(day),
        client.get_stress_data(day),
        sleep["sleep_score"],
    )) == set(EXPECTED_READINESS)
    assert set(gs.normalize_vitals(
        client.get_user_summary(day),
        client.get_body_battery(day),
        client.get_stress_data(day),
    )) == set(EXPECTED_VITALS)
    for items in gs.normalize_workouts(client.get_activities(0, 50)).values():
        for item in items:
            assert set(item) == WORKOUT_KEYS
