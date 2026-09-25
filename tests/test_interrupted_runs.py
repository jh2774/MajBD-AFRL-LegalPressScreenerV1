"""A screen whose process died, and how it should read afterwards.

On a free instance this is the ordinary outcome, not an edge case: the web
service spins down after its idle window, and a screen running in-process goes
with it. Nothing writes the closing row, so the run sits at `running` for good
and a client polls a screen that ended hours ago.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from foci_screen.store import Store

AUTH = {"X-API-Key": "secret-key"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "runs.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    return tmp_path, monkeypatch


def _abandoned_run(tmp_path, tenant="acme", run_id="dead1"):
    """A run recorded as started, whose process then vanished."""
    s = Store(str(tmp_path / "runs.db"), tenant_id=tenant)
    s.start_run("DoD", {}, run_id=run_id, status="running")
    assert s.get_run(run_id)["status"] == "running"
    s.close()


def _boot():
    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


def test_a_run_left_running_is_closed_on_restart(env):
    tmp_path, _ = env
    _abandoned_run(tmp_path)

    c, _ = _boot()
    body = c.get("/v1/screens/dead1", headers=AUTH).json()

    assert body["status"] == "interrupted"
    assert body["finished_at"]
    assert "spinning down" in body["error"], "say why, not just that it stopped"


def test_a_completed_run_is_left_alone(env):
    tmp_path, _ = env
    s = Store(str(tmp_path / "runs.db"), tenant_id="acme")
    s.start_run("DoD", {}, run_id="done1", status="running")
    s.finish_run("done1", stats={"awards_examined": 4})
    s.close()

    c, _ = _boot()
    body = c.get("/v1/screens/done1", headers=AUTH).json()
    assert body["status"] == "complete"
    assert not body.get("error")


def test_a_queue_means_someone_else_may_still_be_running_it(env):
    """With a worker, a run marked running may be a screen in progress.

    Closing it from the API would report a live screen as interrupted — and
    the minutes a screen spends waiting on government APIs are exactly when
    this would happen.
    """
    tmp_path, monkeypatch = env
    _abandoned_run(tmp_path, run_id="worker1")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    c, app_module = _boot()
    # The queue may or may not connect in a test environment; the guard is on
    # the backend actually being a queue.
    if app_module.queue.backend == "thread":
        pytest.skip("no Redis available, so the in-process guard is correct")
    assert c.get("/v1/screens/worker1", headers=AUTH).json()["status"] == "running"


def test_the_sweep_is_scoped_to_configured_tenants(env):
    tmp_path, _ = env
    _abandoned_run(tmp_path, tenant="acme", run_id="mine")
    _abandoned_run(tmp_path, tenant="someone-else", run_id="theirs")

    _boot()

    other = Store(str(tmp_path / "runs.db"), tenant_id="someone-else")
    assert other.get_run("theirs")["status"] == "running"
    other.close()


def test_a_database_that_will_not_open_does_not_stop_the_service(env, monkeypatch):
    """The sweep is the first thing to touch the database. If it raises, the
    health endpoint and the banner that explain the misconfiguration have to
    still come up — that is where an operator reads what went wrong."""
    monkeypatch.setattr("foci_screen.store.Store.reap_interrupted_runs",
                        lambda self, note: (_ for _ in ()).throw(
                            RuntimeError("cannot connect")))

    c, _ = _boot()
    assert c.get("/health").json()["status"] == "ok"
