"""Running screens without a queue, and what that does and does not cost.

Where no worker service is available — a single free-tier web service, which
is how this is actually deployed — screens run in the web process. That is a
trade-off with a real cost, and the job here is to make the cost as small as
it can be and describe it accurately, rather than to warn about infrastructure
nobody can add.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

AUTH = {"X-API-Key": "secret-key"}


def _boot(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "q.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("FOCI_INPROCESS_SCREENS", raising=False)
    for var in ("RENDER", "DYNO", "FLY_APP_NAME", "K_SERVICE", "WEBSITE_INSTANCE_ID"):
        monkeypatch.delenv(var, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


def _warnings(client):
    return client.get("/health").json()["warnings"]


def _about_queues(client):
    """Only the queue-related lines: these tests run on SQLite under RENDER,
    which legitimately warns about storage as well."""
    return [w for w in _warnings(client)
            if "worker" in w or "REDIS_URL" in w or "web process" in w]


def _as_queue_backed(app_module, monkeypatch, workers):
    """Make the module's queue look like RQ. `backend` is a read-only property
    derived from whether a queue object exists, so the queue object is what
    has to change."""
    monkeypatch.setattr(app_module.queue, "_queue", object())
    monkeypatch.setattr(app_module.queue, "worker_count", lambda: workers)
    assert app_module.queue.backend == "rq"


# --------------------------------------------------- what the banner says

def test_in_process_screens_are_described_not_prescribed(tmp_path, monkeypatch):
    """The old text told an operator to add a Redis instance and a worker.

    On a free plan there is no worker to add, so the advice could not be
    taken, and a banner carrying a permanent instruction nobody can follow is
    one that gets read as noise — including the parts that matter.
    """
    c, _ = _boot(tmp_path, monkeypatch, RENDER="true")
    text = " ".join(_warnings(c))

    assert "interrupted" in text, "say what actually happens to a dying screen"
    assert "keeps whatever it has finished" in text
    assert "command line" in text, "offer a route that works without a worker"


def test_the_trade_off_can_be_acknowledged(tmp_path, monkeypatch):
    """An operator who has read it and decided should not keep being told."""
    c, _ = _boot(tmp_path, monkeypatch, RENDER="true",
                 FOCI_INPROCESS_SCREENS="true")
    assert _about_queues(c) == []


def test_acknowledging_it_does_not_silence_anything_else(tmp_path, monkeypatch):
    """The flag covers one known trade-off, not the whole banner."""
    c, _ = _boot(tmp_path, monkeypatch, RENDER="true",
                 FOCI_INPROCESS_SCREENS="true", FOCI_API_KEYS="")
    assert any("FOCI_API_KEYS" in w for w in _warnings(c))


def test_a_queue_with_no_worker_is_the_loud_one(tmp_path, monkeypatch):
    """Strictly worse than no queue: accepted, queued, never run — while
    /health reports a real queue and looks healthier than before."""
    c, app_module = _boot(tmp_path, monkeypatch, RENDER="true")
    _as_queue_backed(app_module, monkeypatch, workers=0)

    text = " ".join(_about_queues(c))
    assert "never run" in text
    assert "no worker is listening" in text


def test_a_queue_with_workers_says_nothing(tmp_path, monkeypatch):
    c, app_module = _boot(tmp_path, monkeypatch, RENDER="true")
    _as_queue_backed(app_module, monkeypatch, workers=2)
    assert _about_queues(c) == []


def test_an_unanswerable_worker_count_is_not_treated_as_zero(tmp_path, monkeypatch):
    """Redis not answering a diagnostic is not evidence that nothing is
    listening, and crying wolf on a healthy deployment is how a banner stops
    being read."""
    c, app_module = _boot(tmp_path, monkeypatch, RENDER="true")
    _as_queue_backed(app_module, monkeypatch, workers=None)
    assert _about_queues(c) == []


def test_nothing_is_said_about_queues_on_a_developer_machine(tmp_path, monkeypatch):
    """Threads are the right answer for the CLI and for local work."""
    c, _ = _boot(tmp_path, monkeypatch)
    assert _warnings(c) == []


# ------------------------------------------- what an interrupted screen keeps

def test_a_finding_is_saved_as_soon_as_its_contractor_is_screened(tmp_path):
    """The cost of an interruption, reduced to what was actually unfinished.

    Findings used to be written after every contractor had been screened, so a
    screen that died at contractor three lost one and two as well — the awards
    were already indexed, so what the run threw away was precisely the part
    that had taken the work.
    """
    from foci_screen.models import Entity, Finding, Signal
    from foci_screen.pipeline import Screener
    from foci_screen.store import Store

    store = Store(str(tmp_path / "partial.db"), tenant_id="acme")
    store.start_run("DoD", {}, run_id="r1")

    saved: list[str] = []
    real_save = store.save_finding

    def record(finding):
        saved.append(finding.entity.name)
        return real_save(finding)

    store.save_finding = record

    def finding_for(name):
        return Finding(
            run_id="r1", entity=Entity(name=name, uei=name.replace(" ", "")),
            signals=[Signal(rule_id="X-01", category="STRUCTURE", severity="low",
                            score=4.0, title="t", rationale="r",
                            evidence="e" * 10, source="test")],
            severity="low", total_score=4.0)

    # Stand in for the loop body: each contractor screened, then the third
    # one's process dies before the run can finish.
    for name in ("FIRST CORP", "SECOND CORP"):
        store.save_finding(finding_for(name))

    assert saved == ["FIRST CORP", "SECOND CORP"]
    kept = store.search_findings(limit=10)
    assert {f["entity"]["name"] for f in kept} == {"FIRST CORP", "SECOND CORP"}
    store.close()

    # And the pipeline no longer writes them a second time at the end.
    import inspect
    body = inspect.getsource(Screener.run)
    assert body.count("save_finding") == 1


def test_an_interrupted_run_keeps_its_findings_queryable(tmp_path, monkeypatch):
    """The run reads interrupted; the work it completed is still there."""
    from foci_screen.models import Entity, Finding, Signal
    from foci_screen.store import Store

    store = Store(str(tmp_path / "q.db"), tenant_id="acme")
    store.start_run("DoD", {}, run_id="dead", status="running")
    store.save_finding(Finding(
        run_id="dead", entity=Entity(name="DONE CORP", uei="UEIDONE"),
        signals=[Signal(rule_id="X-01", category="STRUCTURE", severity="low",
                        score=4.0, title="t", rationale="r", evidence="e" * 10,
                        source="test")],
        severity="low", total_score=4.0))
    store.close()

    c, _ = _boot(tmp_path, monkeypatch)          # restart closes the run out
    assert c.get("/v1/screens/dead", headers=AUTH).json()["status"] == "interrupted"

    findings = c.get("/v1/findings", headers=AUTH).json()["findings"]
    assert [f["entity"]["name"] for f in findings] == ["DONE CORP"]


if __name__ == "__main__":   # pragma: no cover
    pytest.main([__file__])
