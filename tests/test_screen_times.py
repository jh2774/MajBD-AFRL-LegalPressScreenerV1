"""Scheduled screens only at a few set times a day: 9 am, 1 pm and 5 pm
Eastern unless the site says otherwise. Nothing here waits for a clock: the
time is given."""
from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from foci_screen import scopes, screen_times
from foci_screen.screen_times import ScreenTimesError

TIMES = screen_times.parse_times(None)


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("raw,expected", [
    (None, [(9, 0), (13, 0), (17, 0)]),
    ("9am, 1pm, 5:30pm", [(9, 0), (13, 0), (17, 30)]),
    ("17:00,09:00", [(9, 0), (17, 0)]),
    ("any", None), ("", None),
])
def test_times_are_read_as_people_write_them(raw, expected):
    assert screen_times.parse_times(raw) == expected


def test_a_time_that_is_not_one_is_refused():
    with pytest.raises(ScreenTimesError):
        screen_times.parse_times("lunchtime")
    with pytest.raises(ScreenTimesError):
        screen_times.parse_times("25:00")


@pytest.mark.parametrize("when,local", [
    ("2026-07-01T13:10:00", "2026-07-01T09:10:00"),     # daylight time: UTC-4
    ("2026-12-01T14:10:00", "2026-12-01T09:10:00"),     # standard time: UTC-5
    ("2026-03-08T07:00:00", "2026-03-08T03:00:00"),     # clocks go forward at 2 am
    ("2026-11-01T06:00:00", "2026-11-01T01:00:00"),     # and back
])
def test_eastern_time_follows_daylight_saving(when, local, monkeypatch):
    import zoneinfo

    def no_database(name):
        raise zoneinfo.ZoneInfoNotFoundError(name)
    monkeypatch.setattr(zoneinfo, "ZoneInfo", no_database)     # the built-in rules
    assert screen_times.local_now("America/New_York", utc(when)).isoformat() == local


def test_screens_run_only_in_the_hour_after_each_time():
    at = {t: datetime.fromisoformat(f"2026-07-01T{t}") for t in
          ("08:59:00", "09:00:00", "09:58:00", "09:59:00", "13:30:00", "17:05:00")}
    assert [screen_times.slot(at[t], TIMES) for t in sorted(at)] == [None, 0, 0, None, 1, 2]


def test_a_whole_offices_runs_are_shared_across_the_day():
    day = "2026-07-01T"
    allowed = [screen_times.runs_allowed(datetime.fromisoformat(day + t), TIMES, 12)
               for t in ("08:00:00", "09:10:00", "13:10:00", "17:10:00")]
    assert allowed == [0, 4, 8, 12]
    assert screen_times.next_time(datetime.fromisoformat(day + "10:00:00"), TIMES) == "1:00 pm"
    assert screen_times.next_time(datetime.fromisoformat(day + "18:00:00"),
                                  TIMES) == "9:00 am tomorrow"
    assert screen_times.describe(TIMES, "America/New_York") == (
        "9:00 am, 1:00 pm and 5:00 pm Eastern time")


# ------------------------------------------------------------- the site

@pytest.fixture()
def site(tmp_path, monkeypatch):
    def make(clock: str, **env):
        base = {"FOCI_API_KEYS": "default:admin-key", "FOCI_DB": str(tmp_path / "t.db"),
                "FOCI_CACHE": str(tmp_path / "cache"), "FOCI_OUT": str(tmp_path / "out"),
                "FOCI_PUBLIC": "true"}
        for name in ("DATABASE_URL", "REDIS_URL", "FOCI_SCREEN_TIMES", "FOCI_TIMEZONE",
                     "FOCI_SCOPE_RUNS_PER_DAY"):
            monkeypatch.delenv(name, raising=False)
        for k, v in {**base, **env}.items():
            monkeypatch.setenv(k, v)
        from foci_screen.api import app as app_module
        importlib.reload(app_module)
        local = datetime.fromisoformat(clock)
        monkeypatch.setattr(app_module.screen_times, "local_now", lambda zone: local)
        started = []
        monkeypatch.setattr(app_module.queue, "enqueue",
                            lambda tenant, run_id, options, **kw: started.append(options))
        return TestClient(app_module.app), app_module, started
    return make


def test_the_sweep_waits_for_a_screening_time(site):
    c, app_module, started = site("2026-07-01T10:30:00")
    app_module.store_for("default").create_watchlist("Navy", {"agency": "Navy"})
    r = c.post("/v1/schedule/watchlists").json()
    assert (r["status"], r["next"]) == ("not a screening time", "1:00 pm")
    assert r["times"] == "9:00 am, 1:00 pm and 5:00 pm Eastern time"
    assert started == []


def test_at_a_screening_time_it_runs(site):
    c, app_module, started = site("2026-07-01T13:05:00")
    app_module.store_for("default").create_watchlist("Navy", {"agency": "Navy"})
    assert c.post("/v1/schedule/watchlists").json()["status"] == "queued"
    assert len(started) == 1


def test_a_whole_office_uses_only_its_share_of_the_days_runs(site, monkeypatch):
    c, app_module, started = site("2026-07-01T09:05:00", FOCI_SCOPE_RUNS_PER_DAY="6")
    store = app_module.store_for("default")
    wid = store.create_watchlist("AFWERX", {"scope": scopes.PRESETS["afwerx"].id})
    store.set_watchlist_backlog(wid, True)
    monkeypatch.setattr(app_module, "CATCH_UP_MINUTES", -1)
    statuses = [c.post("/v1/schedule/watchlists").json()["status"] for _ in range(4)]
    assert statuses == ["queued", "queued", "nothing due", "nothing due"], "2 of 6 by 9 am"


def test_set_to_any_it_runs_whenever_asked(site):
    c, app_module, started = site("2026-07-01T03:00:00", FOCI_SCREEN_TIMES="any")
    app_module.store_for("default").create_watchlist("Navy", {"agency": "Navy"})
    assert c.post("/v1/schedule/watchlists").json()["status"] == "queued"


def test_watching_an_office_out_of_hours_starts_at_the_next_time(site):
    c, app_module, started = site("2026-07-01T19:00:00")
    r = c.post("/v1/scopes/watch", headers={"X-Foci-Client": "web"},
               json={"scope": "AFWERX"})
    assert r.status_code == 201
    assert r.json()["run"] == {"status": "waiting", "next": "9:00 am tomorrow"}
    assert started == []
    store = app_module.store_for("default")
    [watch] = store.list_watchlists()
    assert watch["backlog"] == 1, "the next screening time picks it up"
