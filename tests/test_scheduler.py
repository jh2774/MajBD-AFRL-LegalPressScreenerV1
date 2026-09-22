"""The nightly cron entry point.

A cron job's exit code is the only thing most people ever look at, so it has
to distinguish "nothing to do" from "unable to do anything".
"""
from __future__ import annotations

import pytest

from foci_screen import scheduler
from foci_screen.store import Store


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "sched.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    return tmp_path


class FakeQueue:
    backend = "rq"
    enqueued: list = []

    def __init__(self, cfg):
        pass

    def enqueue(self, tenant_id, run_id, options):
        FakeQueue.enqueued.append((tenant_id, run_id, options))


def test_missing_queue_fails_the_cron_run(env):
    """Regression: this used to log an error and exit 0, so a deploy without
    Redis showed a green scheduler every night while screening nothing."""
    assert scheduler.main() == scheduler.EXIT_QUEUE_UNAVAILABLE


def test_missing_queue_still_prunes(env):
    """Pruning needs no queue and should not be held hostage to one."""
    store = Store(str(env / "sched.db"))
    try:
        from foci_screen.models import Document
        for i in range(25):
            store.observe(Document(source="web", key="web:x", text=f"revision {i}"))
    finally:
        store.close()

    scheduler.main()

    store = Store(str(env / "sched.db"))
    try:
        assert len(store.document_timeline("web", "web:x", limit=100)) == \
            scheduler.RETAIN_REVISIONS
    finally:
        store.close()


def test_sweep_raises_rather_than_returning_zero(env):
    with pytest.raises(scheduler.QueueUnavailable):
        scheduler.sweep()


def test_no_watchlists_is_a_success(env, monkeypatch):
    monkeypatch.setattr(scheduler, "JobQueue", FakeQueue)
    assert scheduler.main() == 0


def test_sweep_enqueues_every_tenants_active_watchlists(env, monkeypatch):
    FakeQueue.enqueued = []
    monkeypatch.setattr(scheduler, "JobQueue", FakeQueue)
    path = str(env / "sched.db")

    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        acme.create_watchlist("navy", {"agency": "Department of Defense"})
        paused = acme.create_watchlist("paused", {"agency": "Department of Energy"})
        acme.set_watchlist_active(paused, False)
        other.create_watchlist("doe", {"agency": "Department of Energy"})
    finally:
        acme.close()
        other.close()

    assert scheduler.sweep() == 2
    assert sorted(t for t, _, _ in FakeQueue.enqueued) == ["acme", "other"]

    # Each enqueued run exists as a queued row, owned by the right tenant.
    acme = Store(path, tenant_id="acme")
    try:
        run_id = next(r for t, r, _ in FakeQueue.enqueued if t == "acme")
        assert acme.get_run(run_id)["status"] == "queued"
    finally:
        acme.close()


def test_unreadable_watchlist_is_skipped_not_fatal(env, monkeypatch):
    FakeQueue.enqueued = []
    monkeypatch.setattr(scheduler, "JobQueue", FakeQueue)
    path = str(env / "sched.db")

    store = Store(path, tenant_id="acme")
    try:
        good = store.create_watchlist("good", {"agency": "DoD"})
        broken = store.create_watchlist("broken", {"agency": "DoE"})
        with store._tx() as c:
            c.execute("UPDATE watchlists SET params=? WHERE watchlist_id=?",
                      ("{not json", broken))
    finally:
        store.close()

    assert scheduler.sweep() == 1
    assert FakeQueue.enqueued[0][2] == {"agency": "DoD"}
    assert good
