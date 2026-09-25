"""Screening a named contractor, rather than a department and whoever turns up.

Search reads the screened population, so a firm nobody has screened matches
nothing — correctly. What was missing was any way to act on that: `agency` was
required, so the only route into the database was to screen a whole department.
A contractor somebody already had in mind was unreachable.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from foci_screen.connectors.usaspending import USASpendingConnector
from foci_screen.pipeline import ScreenOptions

AUTH = {"X-API-Key": "secret-key"}


class RecordingHttp:
    """Captures the request body so the filters can be asserted."""

    def __init__(self, results=None):
        self.posts: list[dict] = []
        self.results = results or []
        self.stats = {"hits": 0, "misses": 0, "errors": 0}

    def post(self, url, json_body=None, **kw):
        self.posts.append({"url": url, "body": json_body})
        return {"status": 200, "json": {"results": self.results}, "text": ""}

    def get(self, url, **kw):
        return {"status": 404, "json": None, "text": ""}


def _filters(http):
    return http.posts[0]["body"]["filters"]


# ------------------------------------------------------------- the connector

def test_a_recipient_becomes_a_recipient_filter():
    http = RecordingHttp()
    USASpendingConnector(http).search_awards(recipient="MERIDIAN PHOTONICS INC")

    f = _filters(http)
    assert f["recipient_search_text"] == ["MERIDIAN PHOTONICS INC"]
    # No agency filter at all, rather than an empty one that matches nothing.
    assert "agencies" not in f


def test_an_agency_still_works_on_its_own():
    http = RecordingHttp()
    USASpendingConnector(http).search_awards("Department of Defense")

    f = _filters(http)
    assert f["agencies"] == [
        {"type": "awarding", "tier": "toptier", "name": "Department of Defense"}]
    assert "recipient_search_text" not in f


def test_both_together_narrow_rather_than_conflict():
    """One contractor's work for one department."""
    http = RecordingHttp()
    USASpendingConnector(http).search_awards(
        "Department of Defense", recipient="ACME DYNAMICS LLC",
        sub_agency="Department of the Navy")

    f = _filters(http)
    assert f["recipient_search_text"] == ["ACME DYNAMICS LLC"]
    assert len(f["agencies"]) == 2


def test_naming_neither_is_refused_rather_than_searching_everything():
    """An unfiltered search would pull the whole of federal contracting."""
    with pytest.raises(ValueError, match="agency, a recipient, or both"):
        USASpendingConnector(RecordingHttp()).search_awards()


# ------------------------------------------------------------------- options

def test_screen_options_accept_a_recipient_alone():
    opts = ScreenOptions(recipient="ACME DYNAMICS LLC")
    assert opts.agency == ""
    assert opts.recipient == "ACME DYNAMICS LLC"


# ----------------------------------------------------------------- the API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "r.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


def test_a_screen_can_name_only_a_recipient(client, monkeypatch):
    c, app_module = client
    enqueued = []
    monkeypatch.setattr(app_module.queue, "enqueue",
                        lambda t, r, o: enqueued.append(o))

    r = c.post("/v1/screens", headers=AUTH,
               json={"recipient": "LOCKHEED MARTIN CORPORATION", "months_back": 6})
    assert r.status_code == 202
    assert enqueued[0]["recipient"] == "LOCKHEED MARTIN CORPORATION"
    assert enqueued[0]["agency"] == ""


def test_a_screen_with_neither_is_refused_with_a_reason(client):
    c, _ = client
    r = c.post("/v1/screens", headers=AUTH, json={"months_back": 6})
    assert r.status_code == 422
    assert "recipient" in r.text


def test_the_agency_route_is_unchanged(client, monkeypatch):
    c, app_module = client
    enqueued = []
    monkeypatch.setattr(app_module.queue, "enqueue",
                        lambda t, r, o: enqueued.append(o))

    r = c.post("/v1/screens", headers=AUTH,
               json={"agency": "Department of Defense", "months_back": 6})
    assert r.status_code == 202
    assert enqueued[0]["agency"] == "Department of Defense"


def test_surrounding_whitespace_does_not_defeat_the_check(client, monkeypatch):
    """A name pasted out of a spreadsheet arrives padded."""
    c, app_module = client
    monkeypatch.setattr(app_module.queue, "enqueue", lambda t, r, o: None)

    assert c.post("/v1/screens", headers=AUTH,
                  json={"recipient": "   "}).status_code == 422
    assert c.post("/v1/screens", headers=AUTH,
                  json={"recipient": "  ACME DYNAMICS LLC  "}).status_code == 202


def test_the_run_is_labelled_with_the_contractor(client, monkeypatch):
    """`agency` on the run row is the subject of the screen; for a recipient
    screen, showing a blank there would make the run unidentifiable."""
    c, app_module = client
    monkeypatch.setattr(app_module.queue, "enqueue", lambda t, r, o: None)

    run_id = c.post("/v1/screens", headers=AUTH,
                    json={"recipient": "ACME DYNAMICS LLC"}).json()["run_id"]
    body = c.get(f"/v1/screens/{run_id}", headers=AUTH).json()
    assert "ACME" in (body["agency"] or "")
