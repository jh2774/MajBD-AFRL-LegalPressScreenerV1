"""Scheduled screens only at a few set times a day: midnight and noon Eastern
unless the site says otherwise. Most of these tests set three times, 9 am,
1 pm and 5 pm, to see the times shared out. Nothing here waits for a clock:
the time is given."""
from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from foci_screen import scopes, screen_times
from foci_screen.screen_times import ScreenTimesError

THREE = "09:00,13:00,17:00"
TIMES = screen_times.parse_times(THREE)


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("raw,expected", [
    (None, [(0, 0), (12, 0)]),
    ("12am, 12pm", [(0, 0), (12, 0)]),
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
                "FOCI_PUBLIC": "true", "FOCI_SCREEN_TIMES": THREE}
        for name in ("DATABASE_URL", "REDIS_URL", "FOCI_SCREEN_TIMES", "FOCI_TIMEZONE",
                     "FOCI_SCOPE_RUNS_PER_DAY"):
            monkeypatch.delenv(name, raising=False)
        for k, v in {**base, **env}.items():
            if v is not None:               # None: leave it unset, to the default
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


def test_by_default_routine_screens_run_at_midnight_and_noon(site):
    c, app_module, started = site("2026-07-01T10:30:00", FOCI_SCREEN_TIMES=None)
    app_module.store_for("default").create_watchlist("Navy", {"agency": "Navy"})
    r = c.post("/v1/schedule/watchlists").json()
    assert (r["status"], r["next"]) == ("not a screening time", "12:00 pm")
    assert r["times"] == "12:00 am and 12:00 pm Eastern time"
    c, app_module, started = site("2026-07-01T12:05:00", FOCI_SCREEN_TIMES=None)
    app_module.store_for("default").create_watchlist("Navy", {"agency": "Navy"})
    assert c.post("/v1/schedule/watchlists").json()["status"] == "queued"
    c, app_module, started = site("2026-07-01T23:00:00", FOCI_SCREEN_TIMES=None)
    assert c.post("/v1/schedule/watchlists").json()["next"] == "12:00 am tomorrow"


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


# --------------------------- first screens now; screening again: the routine

WEB = {"X-Foci-Client": "web"}
ADMIN = {"Authorization": "Bearer admin-key"}
SCREEN = {"recipient": "ACME DYNAMICS LLC", "max_entities": 1}
EVENING = "2026-07-01T19:00:00"


def screened_before(app_module, key="ACME DYNAMICS LLC"):
    app_module.store_for("default").mark_entity_screened(key)


def test_a_firms_first_screen_runs_at_once_whoever_asks(site):
    c, app_module, started = site(EVENING)
    r = c.post("/v1/screens", json=SCREEN).json()
    assert r["status"] == "queued" and len(started) == 1


def test_screening_a_firm_again_waits_for_the_routine(site):
    c, app_module, started = site(EVENING)
    screened_before(app_module)
    assert c.get("/v1/auth/status").json()["screening"]["instant"] is False
    r = c.post("/v1/screens", json=SCREEN).json()
    assert (r["status"], r["next"]) == ("scheduled", "9:00 am tomorrow")
    assert started == [] and "execute" not in r
    page = c.get(f"/v1/screens/{r['run_id']}").json()
    assert page["status"] == "scheduled" and page["screening"]["next"] == "9:00 am tomorrow"
    # Its page cannot start it early either.
    assert c.post(f"/v1/screens/{r['run_id']}/execute").json()["started_here"] is False


def test_an_admin_screens_again_whenever_they_like(site):
    c, app_module, started = site(EVENING)
    screened_before(app_module)
    assert c.get("/v1/auth/status", headers=ADMIN).json()["screening"]["instant"] is True
    r = c.post("/v1/screens", json=SCREEN, headers=ADMIN).json()
    assert r["status"] == "queued" and len(started) == 1


def test_the_page_hears_whether_its_key_is_the_admins(site):
    c, app_module, started = site(EVENING)
    nobody = c.get("/v1/auth/status").json()
    assert nobody["key_accepted"] is None and nobody["screening"]["admin"] is False
    admin = c.get("/v1/auth/status", headers=ADMIN).json()
    assert admin["key_accepted"] is True and admin["screening"]["admin"] is True
    wrong = c.get("/v1/auth/status", headers={"Authorization": "Bearer not-it"}).json()
    assert wrong["key_accepted"] is False and wrong["screening"]["admin"] is False


def test_the_page_keeps_the_key_it_is_given_and_asks_with_it():
    """The key dialog's Save once did nothing (lost in a merge), so the site
    never saw the key and nobody could act as an admin from the page."""
    from pathlib import Path

    import foci_screen
    js = (Path(foci_screen.__file__).parent / "web" / "app.js").read_text(encoding="utf-8")
    assert 'keyDialog.addEventListener("close"' in js
    assert "localStorage.setItem(KEY_STORAGE, apiKey)" in js
    status = js[js.index('fetch("/v1/auth/status"'):][:200]
    assert "Authorization" in status


def test_the_sites_own_accounts_are_admins_and_strangers_are_not(site):
    c, app_module, started = site(EVENING)
    screened_before(app_module)
    owner = TestClient(app_module.app)
    owner.post("/v1/auth/signup", headers={**WEB, **ADMIN},
               json={"email": "owner@concord.example", "password": "correct horse battery"})
    assert owner.post("/v1/screens", headers=WEB, json=SCREEN).json()["status"] == "queued"
    stranger = TestClient(app_module.app)
    stranger.post("/v1/auth/signup", headers=WEB,
                  json={"email": "someone@firm.example", "password": "correct horse battery"})
    r = stranger.post("/v1/screens", headers=WEB, json=SCREEN).json()
    assert r["status"] == "queued", \
        "a stranger's own workspace has never screened it: a first screen"
    store = app_module.store_for(stranger.get("/v1/me").json()["account"]["workspace"])
    store.mark_entity_screened("ACME DYNAMICS LLC")
    r = stranger.post("/v1/screens", headers=WEB, json=SCREEN).json()
    assert r["status"] == "scheduled"


def test_a_watchlists_first_run_is_now_and_its_reruns_are_the_routines(site):
    c, app_module, started = site(EVENING)
    wid = app_module.store_for("default").create_watchlist("Navy", {"agency": "Navy"})
    assert c.post(f"/v1/watchlists/{wid}/run").json()["status"] == "queued"
    assert c.post(f"/v1/watchlists/{wid}/run").json()["status"] == "scheduled"
    assert c.post(f"/v1/watchlists/{wid}/run", headers=ADMIN).json()["status"] == "queued"


def test_an_agency_screened_before_waits_too(site):
    from test_search import make_contract
    c, app_module, started = site(EVENING)
    agency = {"agency": "Department of Defense", "max_entities": 1}
    assert c.post("/v1/screens", json=agency).json()["status"] == "queued"
    store = app_module.store_for("default")
    store.start_run("DoD", {}, run_id="earlier")
    store.save_contract(make_contract(), "earlier", "UEI123")
    assert c.post("/v1/screens", json=agency).json()["status"] == "scheduled"


def test_watching_an_office_starts_at_once_and_asking_again_waits(site):
    c, app_module, started = site(EVENING)
    first = c.post("/v1/scopes/watch", headers=WEB, json={"scope": "AFWERX"}).json()
    assert first["run"]["status"] == "queued" and started[-1]["scope"]
    again = c.post("/v1/scopes/watch", headers=WEB, json={"scope": "AFWERX"}).json()
    assert again["run"] == {"status": "waiting", "next": "9:00 am tomorrow"}
    admin = c.post("/v1/scopes/watch", headers={**WEB, **ADMIN},
                   json={"scope": "AFWERX"}).json()
    assert admin["run"]["status"] == "queued"
    [watch] = app_module.store_for("default").list_watchlists()
    assert watch["backlog"] == 1, "the routine carries on from there"


def test_waiting_screens_run_first_at_the_next_screening_time(site, monkeypatch):
    c, app_module, started = site(EVENING)
    screened_before(app_module)
    waiting = c.post("/v1/screens", json=SCREEN).json()["run_id"]
    store = app_module.store_for("default")
    asked_at = store.get_run(waiting)["started_at"]
    store.create_watchlist("Navy", {"agency": "Navy"})
    morning = datetime.fromisoformat("2026-07-02T09:05:00")
    monkeypatch.setattr(app_module.screen_times, "local_now", lambda zone: morning)
    first = c.post("/v1/schedule/watchlists").json()
    assert first == {"status": "queued", "left": 1}
    assert started[0]["recipient"] == "ACME DYNAMICS LLC", "the waiting screen first"
    run = store.get_run(waiting)
    assert run["status"] == "queued" and run["started_at"] >= asked_at
    assert c.post("/v1/schedule/watchlists").json()["status"] == "queued"   # the watchlist
    assert c.post("/v1/schedule/watchlists").json()["status"] == "nothing due"
