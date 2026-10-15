"""Running on Vercel.

Vercel runs the site as a function: only /tmp is writable, nothing keeps
running once a response has been sent, a request is cut off at 300 seconds on
the free plan, and several copies may be answering at once. Each test here is
one of those facts, and what the application does about it.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from foci_screen import jobs, syscheck
from foci_screen.config import Config
from foci_screen.store import Store

ROOT = Path(__file__).resolve().parent.parent
AUTH = {"X-API-Key": "secret-key"}
ENV = ("VERCEL", "VERCEL_GIT_COMMIT_SHA", "RENDER", "RENDER_GIT_COMMIT", "DYNO",
       "FLY_APP_NAME", "K_SERVICE", "K_REVISION", "WEBSITE_INSTANCE_ID",
       "DATABASE_URL", "DATABASE_URL_UNPOOLED", "POSTGRES_URL_NON_POOLING",
       "INSTANCE_CONNECTION_NAME", "DB_USER", "DB_NAME", "DB_PASS", "REDIS_URL",
       "FOCI_DB", "FOCI_CACHE", "FOCI_OUT", "FOCI_CACHE_MAX_MB", "FOCI_KEEP_AWAKE",
       "FOCI_SCREENS_IN_REQUEST", "FOCI_SCREEN_SECONDS", "FOCI_INPROCESS_SCREENS",
       "ALERTS_PAUSED", "FOCI_PUBLIC_URL")


@pytest.fixture()
def env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ------------------------------------------------------------- configuration

def test_on_vercel_everything_written_goes_to_tmp(env):
    env.setenv("VERCEL", "1")
    cfg = Config()
    assert cfg.managed_host == "Vercel"
    assert (cfg.cache_dir, cfg.out_dir, cfg.db_path) == (
        "/tmp/foci-cache", "/tmp/foci-out", "/tmp/foci_screen.db")
    assert cfg.cache_max_mb == 64
    env.setenv("FOCI_CACHE", "/tmp/elsewhere")
    assert Config().cache_dir == "/tmp/elsewhere", "an explicit setting still wins"


def test_off_vercel_nothing_changes(env):
    cfg = Config()
    assert (cfg.cache_dir, cfg.out_dir, cfg.db_path) == (".cache", "out", "foci_screen.db")
    assert (cfg.screens_in_request, cfg.screen_seconds) == (False, 0)


def test_screens_run_in_a_request_with_a_time_limit_and_nothing_calls_itself(env):
    env.setenv("VERCEL", "1")
    cfg = Config()
    assert (cfg.screens_in_request, cfg.screen_seconds) == (True, 240)
    assert cfg.keep_awake_wanted is False, \
        "on Vercel a call to its own address starts another function; it keeps nothing awake"
    env.setenv("FOCI_SCREEN_SECONDS", "770")         # a Pro plan, maxDuration 800
    assert Config().screen_seconds == 770

    env.delenv("VERCEL")
    env.setenv("K_SERVICE", "foci")
    assert Config().keep_awake_wanted is True, "Cloud Run still holds itself awake"
    env.setenv("FOCI_KEEP_AWAKE", "false")
    assert Config().keep_awake_wanted is False


def test_neons_direct_address_is_used_over_its_pooled_one(env):
    """A pooler in transaction mode would break the alert lock, the copy's
    streaming cursor and prepared statements."""
    env.setenv("DATABASE_URL", "postgresql://u:p@ep-x-pooler.neon.tech/neondb")
    assert Config().database_url.endswith("ep-x-pooler.neon.tech/neondb")
    env.setenv("DATABASE_URL_UNPOOLED", "postgresql://u:p@ep-x.neon.tech/neondb")
    assert Config().database_url == "postgresql://u:p@ep-x.neon.tech/neondb"
    env.delenv("DATABASE_URL_UNPOOLED")
    env.setenv("POSTGRES_URL_NON_POOLING", "postgresql://u:p@ep-y.neon.tech/neondb")
    assert Config().database_url == "postgresql://u:p@ep-y.neon.tech/neondb"


def test_vercel_is_pointed_at_the_application_and_can_install_what_it_needs():
    tomllib = pytest.importorskip("tomllib")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    entrypoint = project["tool"]["vercel"]["entrypoint"]
    module, _, name = entrypoint.partition(":")
    assert (module, name) == ("foci_screen.api.app", "app")

    deps = " ".join(project["project"]["dependencies"])
    for needed in ("fastapi", "pydantic", "psycopg[binary]"):
        assert needed in deps, f"Vercel installs no extras; {needed} must be a dependency"

    functions = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["functions"]
    assert list(functions) == [module.replace(".", "/") + ".py"]
    assert functions["foci_screen/api/app.py"]["maxDuration"] == 300, "the free plan's limit"
    assert "foci_screen" not in functions["foci_screen/api/app.py"]["excludeFiles"]


# ------------------------------------------------------- the screen itself

class FakeResult:
    findings: list = []
    stats: dict = {}
    notes: list = []


def a_screen_that_takes(env, tmp_path, steps: int, seconds_each: float):
    """Stand in for the screener; a clock that moves on with each step."""
    env.setenv("FOCI_DB", str(tmp_path / "screen.db"))
    env.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    env.setenv("FOCI_OUT", str(tmp_path / "out"))
    clock = {"now": 1000.0}
    env.setattr("time.monotonic", lambda: clock["now"])
    done: list[int] = []

    class Screener:
        def run(self, opts, progress, run_id):
            for step in range(steps):
                progress(f"[contractor {step}]")
                done.append(step)
                clock["now"] += seconds_each
            return FakeResult()

    class Browser:
        def close(self):
            pass

    env.setattr(jobs, "build_screener", lambda cfg, store: (Screener(), Browser()))
    store = Store(str(tmp_path / "screen.db"), tenant_id="acme")
    run_id = store.start_run("ACME", {}, status="queued")
    return store, run_id, done


def test_a_screen_stops_at_its_limit_between_steps_and_says_why(env, tmp_path):
    store, run_id, done = a_screen_that_takes(env, tmp_path, steps=10, seconds_each=60)
    result = jobs.run_screen_job("acme", run_id, {}, limit_seconds=240)
    run = store.get_run(run_id)
    assert result["status"] == "interrupted" and run["status"] == "interrupted"
    assert done == [0, 1, 2, 3], "four steps fit in four minutes; the fifth never started"
    assert "time limit" in run["error"] and "4 min 00 s" in run["error"]
    assert "screened before then are saved" in run["error"]
    assert run["finished_at"]


def test_a_screen_inside_its_limit_finishes_normally(env, tmp_path):
    store, run_id, done = a_screen_that_takes(env, tmp_path, steps=3, seconds_each=30)
    jobs.run_screen_job("acme", run_id, {}, limit_seconds=240)
    assert store.get_run(run_id)["status"] == "complete" and done == [0, 1, 2]

    store, run_id, done = a_screen_that_takes(env, tmp_path, steps=10, seconds_each=60)
    jobs.run_screen_job("acme", run_id, {})
    assert store.get_run(run_id)["status"] == "complete", "no limit: as before"


def test_the_queue_starts_nothing_when_screens_run_in_requests(env):
    env.setenv("VERCEL", "1")
    queue = jobs.JobQueue(Config())
    assert queue.backend == "request"
    queue.enqueue("acme", "r1", {}, hold_url="x", hold_token="t")
    assert not queue.is_running("r1") and queue._threads == {}


# ------------------------------------------------------------ the store

def backdate(store, run_id, minutes):
    when = (datetime.now(timezone.utc) - timedelta(minutes=minutes))
    with store._tx() as c:
        c.execute("UPDATE runs SET started_at=? WHERE run_id=?",
                  (when.replace(microsecond=0, tzinfo=None).isoformat() + "Z", run_id))


def test_a_queued_run_is_claimed_once(tmp_path):
    store = Store(str(tmp_path / "claim.db"), tenant_id="acme")
    run_id = store.start_run("ACME", {}, status="queued")
    assert store.claim_run(run_id) is True and store.get_run(run_id)["status"] == "running"
    assert store.claim_run(run_id) is False, "a second tab does not start it again"
    other = Store(str(tmp_path / "claim.db"), tenant_id="globex")
    assert other.claim_run(other.start_run("X", {}, status="queued")) is True
    assert other.claim_run(run_id) is False, "nor another tenant's"


def test_only_runs_too_old_to_be_running_are_closed(tmp_path):
    store = Store(str(tmp_path / "stale.db"), tenant_id="acme")
    old, young, waiting, done = (store.start_run(n, {}, status="queued")
                                 for n in ("old", "young", "waiting", "done"))
    store.claim_run(old)
    store.claim_run(young)
    store.finish_run(done)
    for run_id in (old, waiting, done):
        backdate(store, run_id, 30)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(
        microsecond=0, tzinfo=None).isoformat() + "Z"
    assert store.close_stale_runs("gone", statuses=("queued", "running"),
                                  started_before=cutoff) == 2
    status = {r: store.get_run(r)["status"] for r in (old, young, waiting, done)}
    assert status == {old: "interrupted", young: "running", waiting: "interrupted",
                      done: "complete"}
    assert store.get_run(old)["error"] == "gone"


# ----------------------------------------------------------------------- API

def boot(tmp_path, env, **extra):
    env.setenv("FOCI_API_KEYS", "acme:secret-key")
    env.setenv("FOCI_DB", str(tmp_path / "api.db"))
    env.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    env.setenv("FOCI_OUT", str(tmp_path / "out"))
    for name, value in extra.items():
        env.setenv(name, value)
    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


def test_starting_a_screen_says_how_to_run_it(tmp_path, env):
    c, app_module = boot(tmp_path, env, VERCEL="1")
    started = c.post("/v1/screens", headers=AUTH, json={"recipient": "ACME DYNAMICS LLC"})
    body = started.json()
    assert started.status_code == 202 and body["backend"] == "request"
    assert body["execute"] == f"/v1/screens/{body['run_id']}/execute"
    assert body["time_limit_seconds"] == 240
    assert c.get(f"/v1/screens/{body['run_id']}", headers=AUTH).json()["status"] == "queued"
    assert not app_module.queue.is_running(body["run_id"]), "nothing started in the background"


def test_the_request_runs_the_screen_once(tmp_path, env):
    c, app_module = boot(tmp_path, env, VERCEL="1")
    ran: list = []

    def fake_job(tenant, run_id, options, limit_seconds=0):
        ran.append((tenant, options["recipient"], limit_seconds))
        app_module.store_for(tenant).finish_run(run_id)
        return {"run_id": run_id}

    env.setattr(jobs, "run_screen_job", fake_job)
    run_id = c.post("/v1/screens", headers=AUTH,
                    json={"recipient": "ACME DYNAMICS LLC"}).json()["run_id"]
    url = f"/v1/screens/{run_id}/execute"
    assert c.post(url).status_code == 401

    first = c.post(url, headers=AUTH).json()
    assert first == {"run_id": run_id, "status": "complete", "started_here": True}
    assert ran == [("acme", "ACME DYNAMICS LLC", 240)]
    again = c.post(url, headers=AUTH).json()
    assert again["started_here"] is False and len(ran) == 1
    assert c.post("/v1/screens/nope/execute", headers=AUTH).status_code == 404


def test_a_screen_whose_request_ended_is_closed_once_it_is_too_old(tmp_path, env):
    c, app_module = boot(tmp_path, env, VERCEL="1")
    store = app_module.store_for("acme")
    gone = store.start_run("gone", {}, status="queued")
    store.claim_run(gone)
    backdate(store, gone, 30)
    young = store.start_run("young", {}, status="queued")
    store.claim_run(young)

    run = c.get(f"/v1/screens/{gone}", headers=AUTH).json()
    assert run["status"] == "interrupted" and "keep the page open" in run["error"]
    assert c.get(f"/v1/screens/{young}", headers=AUTH).json()["status"] == "running", \
        "another copy of the site may still be running it"


def test_where_screens_run_in_threads_nothing_is_closed_by_age(tmp_path, env):
    c, app_module = boot(tmp_path, env)
    store = app_module.store_for("acme")
    run_id = store.start_run("old", {}, status="queued")
    store.claim_run(run_id)
    backdate(store, run_id, 30)
    assert c.get(f"/v1/screens/{run_id}", headers=AUTH).json()["status"] == "running"


def test_health_and_the_system_check_on_vercel(tmp_path, env):
    c, app_module = boot(tmp_path, env, VERCEL="1",
                         VERCEL_GIT_COMMIT_SHA="417b249aa0c0ffee")
    health = c.get("/health").json()
    assert (health["host"], health["revision"], health["queue"]) == ("Vercel", "417b249",
                                                                      "request")
    assert any("will not survive a restart" in w for w in health["warnings"]), \
        "no database configured: said, not left to be found"
    assert not any("Screens run inside the web process" in w for w in health["warnings"])

    held: list = []
    env.setattr(app_module, "_hold_open", lambda *a: held.append(a))
    env.setattr(app_module.alerts_engine, "run_alerts",
                lambda *a, **kw: {"subscriptions": 0, "emails": []})
    assert c.post("/v1/alerts/run", headers=AUTH).json()["status"] == "checked"
    assert held == [], "nothing calls its own address on Vercel"


def test_the_system_check_explains_the_limits():
    note = syscheck.screens_check("request", 240)
    assert note["ok"] is None and "4 min 00 s" in note["detail"]
    assert "page has to stay open" in note["detail"]
    assert syscheck.self_check(object(), "https://x.example", needed=False)["ok"] is None
    assert syscheck.revision({"VERCEL_GIT_COMMIT_SHA": "417b249aa0c0ffee"}) == "417b249"
