"""Undoing a screen.

One run against the wrong agency puts contractors on the dashboard that nobody
chose to watch, and until now there was no way to take them off.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from test_search import make_contract

from foci_screen.models import Entity, Finding, Signal

AUTH = {"X-API-Key": "secret-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "d.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    store = app_module.store_for("acme")

    for run_id, uei, name in (("keep", "UEIKEEP", "WANTED CORP"),
                              ("stray", "UEISTRAY", "UNWANTED CORP")):
        store.start_run(f"agency for {run_id}", {}, run_id=run_id)
        store.save_contract(
            make_contract(piid=f"P-{run_id}", award_id=f"A-{run_id}",
                          recipient_name=name, recipient_uei=uei), run_id, uei)
        store.save_finding(Finding(
            run_id=run_id, entity=Entity(name=name, uei=uei),
            signals=[Signal(rule_id="X-01", category="STRUCTURE", severity="low",
                            score=4.0, title="t", rationale="r",
                            evidence="e" * 10, source="test")],
            severity="low", total_score=4.0))
    return TestClient(app_module.app), app_module


def test_a_dry_run_reports_without_deleting(client):
    c, _ = client
    body = c.delete("/v1/screens/stray", headers=AUTH).json()

    assert body["deleted"] is False
    assert body["would_remove"]["contracts"] == 1
    assert body["would_remove"]["findings"] == 1
    assert "cannot be undone" in body["detail"]
    # Nothing actually went.
    assert c.get("/v1/screens/stray", headers=AUTH).status_code == 200


def test_confirming_removes_the_run_and_its_rows(client):
    c, _ = client
    body = c.delete("/v1/screens/stray?confirm=true", headers=AUTH).json()

    assert body["deleted"] is True
    assert body["removed"]["contracts"] == 1
    assert c.get("/v1/screens/stray", headers=AUTH).status_code == 404

    overview = c.get("/v1/overview", headers=AUTH).json()
    assert overview["totals"]["entities"] == 1, "only the wanted run should remain"


def test_the_other_run_is_untouched(client):
    c, _ = client
    c.delete("/v1/screens/stray?confirm=true", headers=AUTH)

    entities = c.get("/v1/search?q=&kind=entity", headers=AUTH).json()["entities"]
    assert [e["entity_name"] for e in entities] == ["WANTED CORP"]
    assert c.get("/v1/entities/UEIKEEP", headers=AUTH).status_code == 200


def test_shared_document_revisions_are_left_alone(client, tmp_path):
    """Snapshots are global — a hash is a fact about the world, not about who
    was watching. Dropping them would take the change baseline away from every
    other tenant screening the same company."""
    c, app_module = client
    store = app_module.store_for("acme")

    from foci_screen.models import Document
    store.observe(Document(source="web", key="example.com/news",
                           url="https://example.com/news", title="News",
                           text="Something the company published. " * 20))
    before = store._one("SELECT COUNT(*) AS n FROM snapshots")["n"]

    c.delete("/v1/screens/stray?confirm=true", headers=AUTH)

    after = store._one("SELECT COUNT(*) AS n FROM snapshots")["n"]
    assert after == before


def test_deleting_something_that_is_not_there(client):
    c, _ = client
    assert c.delete("/v1/screens/nosuchrun", headers=AUTH).status_code == 404


def test_removal_needs_a_key(client):
    c, _ = client
    assert c.delete("/v1/screens/stray?confirm=true").status_code == 401
    assert c.get("/v1/screens/stray", headers=AUTH).status_code == 200
