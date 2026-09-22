"""Revision retention and the document diff view.

The change-detection design only pays off if you can see *what* changed, not
just that something did. These cover the retention that makes that possible and
the honesty of the result when it is not.
"""
from __future__ import annotations

import pytest

from foci_screen.models import Document
from foci_screen.store import Store

PAGE_V1 = "Acme Corp\nFounded 1957\nHeadquarters in Ohio\nContact us"
PAGE_V2 = ("Acme Corp\nFounded 1957\nHeadquarters in Ohio\n"
           "Acme announced a strategic investment from a Cayman Islands fund\nContact us")


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "docs.db"), tenant_id="acme")
    yield s
    s.close()


def doc(text: str, key: str = "web:acme.com/news") -> Document:
    return Document(source="web", key=key, title="Acme — press",
                    url="https://acme.com/news", text=text, doc_type="press")


# ----------------------------------------------------------------- retention

def test_body_is_retained_on_first_observation(store):
    d = doc(PAGE_V1)
    store.observe(d)
    assert store.document_body(d.sha256()) == PAGE_V1


def test_both_sides_are_retained_when_a_document_changes(store):
    """The replaced version matters as much as the new one — without it the
    first diff after an upgrade has nothing to compare against."""
    old, new = doc(PAGE_V1), doc(PAGE_V2)
    store.observe(old)
    store.observe(new)

    assert store.document_body(old.sha256()) == PAGE_V1
    assert store.document_body(new.sha256()) == PAGE_V2


def test_bodies_are_content_addressed(store):
    """Two documents with identical text cost one row."""
    store.observe(doc(PAGE_V1, key="web:a.com/x"))
    store.observe(doc(PAGE_V1, key="web:b.com/y"))

    rows = store._query("SELECT COUNT(*) AS n FROM snapshot_bodies")
    assert rows[0]["n"] == 1


def test_unchanged_observation_adds_no_revision(store):
    store.observe(doc(PAGE_V1))
    store.observe(doc(PAGE_V1))
    assert len(store.document_timeline("web", "web:acme.com/news")) == 1


# ---------------------------------------------------------------------- diff

def test_diff_reports_only_what_was_added(store):
    store.observe(doc(PAGE_V1))
    store.observe(doc(PAGE_V2))

    result = store.document_diff("web", "web:acme.com/news")

    assert result["baseline"] is False
    assert result["stats"]["added"] == 1
    assert result["stats"]["removed"] == 0
    added = [ln["text"] for ln in result["lines"] if ln["kind"] == "add"]
    assert added == ["Acme announced a strategic investment from a Cayman Islands fund"]


def test_diff_marks_a_first_observation_as_a_baseline(store):
    """Everything reads as new on a first sight; that is not a change the
    contractor made, and the flag exists so the UI cannot imply it was."""
    store.observe(doc(PAGE_V1))

    result = store.document_diff("web", "web:acme.com/news")

    assert result["baseline"] is True
    assert result["from"] is None
    assert all(ln["kind"] == "add" for ln in result["lines"])


def test_diff_between_named_revisions(store):
    a, b, c = doc(PAGE_V1), doc(PAGE_V2), doc(PAGE_V2 + "\nAnd another line")
    for d in (a, b, c):
        store.observe(d)

    result = store.document_diff("web", "web:acme.com/news",
                                 from_sha=a.sha256(), to_sha=c.sha256())

    assert result["stats"]["added"] == 2


def test_diff_lines_are_structured_not_prefixed(store):
    """A line whose own text starts with '-' must not read as a deletion."""
    store.observe(doc("alpha\nbeta"))
    store.observe(doc("alpha\n-1 adjustment\nbeta"))

    result = store.document_diff("web", "web:acme.com/news")
    added = [ln for ln in result["lines"] if ln["kind"] == "add"]

    assert len(added) == 1
    assert added[0]["text"] == "-1 adjustment"


def test_diff_on_unknown_document_reports_an_error(store):
    assert "error" in store.document_diff("web", "web:nothing.com")


def test_timeline_flags_revisions_whose_text_is_gone(store):
    store.observe(doc(PAGE_V1))
    store.observe(doc(PAGE_V2))
    store._query("SELECT 1")
    with store._tx() as c:
        c.execute("DELETE FROM snapshot_bodies")

    timeline = store.document_timeline("web", "web:acme.com/news")
    assert all(r["has_body"] is False for r in timeline)

    # And the diff says so rather than showing an empty result.
    assert "error" in store.document_diff("web", "web:acme.com/news")


# ----------------------------------------------------------------- pruning

def test_pruning_keeps_recent_revisions_and_drops_old_text(store):
    for i in range(8):
        store.observe(doc(f"{PAGE_V1}\nrevision {i}"))

    stats = store.prune_snapshot_bodies(keep_per_document=3)

    assert stats["history_rows_removed"] == 5
    assert len(store.document_timeline("web", "web:acme.com/news")) == 3
    assert stats["bodies_remaining"] == 3


def test_pruning_keeps_the_body_of_the_current_snapshot(store):
    """Even at keep=1 the live snapshot's text must survive — the next diff
    compares against it."""
    latest = doc(PAGE_V2)
    store.observe(doc(PAGE_V1))
    store.observe(latest)

    store.prune_snapshot_bodies(keep_per_document=1)

    assert store.document_body(latest.sha256()) == PAGE_V2


def test_pruning_is_safe_on_an_empty_store(store):
    assert store.prune_snapshot_bodies()["history_rows_removed"] == 0


def test_upgrading_backfills_bodies_from_existing_snapshots(tmp_path):
    """A database written before revision storage existed still has the current
    text on the snapshot row. Copying it across on upgrade means the next change
    is diffable, instead of every document needing to change twice first."""
    import sqlite3

    path = str(tmp_path / "legacy.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE snapshots (
            source TEXT NOT NULL, document_key TEXT NOT NULL, sha256 TEXT NOT NULL,
            url TEXT, title TEXT, text TEXT, first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL, PRIMARY KEY (source, document_key));
        INSERT INTO snapshots VALUES ('web', 'web:acme.com/news', 'abc123',
            'https://acme.com/news', 'Acme', 'original body',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
    """)
    old.commit()
    old.close()

    s = Store(path, tenant_id="acme")
    try:
        assert s.document_body("abc123") == "original body"
        # And a subsequent change diffs against it straight away.
        s.observe(doc("original body\nnew disclosure line"))
    finally:
        s.close()


# ------------------------------------------------------- joining diff to rule

def test_annotate_marks_the_line_the_evidence_came_from():
    """The diff shows what changed, the signal shows what fired; without this
    a reviewer pairs them up by eye."""
    from foci_screen.store import annotate_diff

    lines = [
        {"kind": "ctx", "text": "Board of directors declares a dividend"},
        {"kind": "add", "text": "Acme entered a definitive agreement with a Cayman fund"},
    ]
    signals = [{"rule_id": "FOCI-JURIS-01",
                "evidence": "…quarter. Acme entered a definitive agreement with a "
                            "Cayman fund which will acquire a minority stake…"}]

    out = annotate_diff(lines, signals)

    assert "rules" not in out[0]
    assert out[1]["rules"] == ["FOCI-JURIS-01"]


def test_annotate_tolerates_reflowed_whitespace():
    """Evidence snippets have their newlines flattened; the line will not
    match character-for-character."""
    from foci_screen.store import annotate_diff

    lines = [{"kind": "add", "text": "Acme   entered  a definitive\tagreement today"}]
    signals = [{"rule_id": "R1", "evidence": "Acme entered a definitive agreement today"}]

    assert annotate_diff(lines, signals)[0]["rules"] == ["R1"]


def test_annotate_ignores_short_lines():
    """A heading matches half the snippets on a site. Pointing a reviewer at the
    wrong sentence is worse than not pointing at all."""
    from foci_screen.store import annotate_diff

    lines = [{"kind": "add", "text": "Contact us"}]
    signals = [{"rule_id": "R1", "evidence": "Please Contact us about the agreement"}]

    assert "rules" not in annotate_diff(lines, signals)[0]


def test_annotate_records_every_matching_rule():
    from foci_screen.store import annotate_diff

    text = "Acme pledged its patents to a Cayman Islands lender as collateral"
    lines = [{"kind": "add", "text": text}]
    signals = [{"rule_id": "IPCOL-01", "evidence": f"...{text}..."},
               {"rule_id": "FOCI-JURIS-01", "evidence": f"xx {text} yy"}]

    assert annotate_diff(lines, signals)[0]["rules"] == ["FOCI-JURIS-01", "IPCOL-01"]


def test_annotate_matches_when_the_line_is_longer_than_the_evidence():
    """Regression: evidence is a fixed-width window around the matched term, so
    on a long paragraph the *evidence* is the shorter of the two. Testing
    containment in one direction only marked nothing at all."""
    from foci_screen.store import annotate_diff

    line = ("Lockheed Martin has entered into a definitive agreement with an "
            "investor group organised in the Cayman Islands, under which the "
            "group will acquire a minority stake and appoint one board observer.")
    # What _snippet() produces: a window, clipped mid-sentence at both ends.
    evidence = ("agreement with an investor group organised in the Cayman "
                "Islands, under which the group will acquire a min")
    lines = [{"kind": "add", "text": line}]

    out = annotate_diff(lines, [{"rule_id": "FOCI-JURIS-01", "evidence": evidence}])
    assert out[0]["rules"] == ["FOCI-JURIS-01"]


def test_annotate_does_not_match_unrelated_text():
    from foci_screen.store import annotate_diff

    lines = [{"kind": "add", "text": "The board declared a regular quarterly dividend"}]
    signals = [{"rule_id": "R1",
                "evidence": "acquired a minority stake through a Cayman Islands vehicle "
                            "and appointed a board observer to the company"}]

    assert "rules" not in annotate_diff(lines, signals)[0]


def test_annotate_with_no_signals_changes_nothing():
    from foci_screen.store import annotate_diff

    lines = [{"kind": "add", "text": "A line long enough to be matchable here"}]
    assert annotate_diff(lines, []) == lines


def test_signals_are_stamped_with_their_document(store):
    """Set centrally in evaluate_documents, not by each rule."""
    from foci_screen.models import Contract, Entity
    from foci_screen.risk import engine

    text = ("Acme entered into a definitive agreement with an investor organised "
            "in the Cayman Islands to acquire a minority stake.")
    d = doc(text, key="web:acme.com/pr")
    signals = engine.evaluate_documents(
        [(d, None)], Entity(name="ACME", uei="UEI1"),
        [Contract(award_id="A1", piid="P1")])

    assert signals, "expected the jurisdiction rule to fire"
    assert all(s.document_key == "web:acme.com/pr" for s in signals)


def test_signals_for_document_filters_by_key(store):
    from foci_screen.models import Entity, Finding, Signal

    def sig(rule_id, key):
        return Signal(rule_id=rule_id, category="FOCI", severity="low", score=1.0,
                      title="t", rationale="r", evidence="e", source="web",
                      document_key=key)

    store.save_finding(Finding(
        entity=Entity(name="ACME", uei="UEI123"),
        signals=[sig("R1", "web:a"), sig("R2", "web:b")],
        total_score=5.0, severity="low", run_id="r1"))

    found = store.signals_for_document("UEI123", "web:a")
    assert [s["rule_id"] for s in found] == ["R1"]


# ------------------------------------------------------------ entity linking

def test_documents_are_linked_to_the_entity_that_gathered_them(store):
    d = doc(PAGE_V1)
    store.observe(d)
    store.link_document("UEI123", d)

    docs = store.documents_for_entity("UEI123")
    assert len(docs) == 1
    assert docs[0]["title"] == "Acme — press"
    assert docs[0]["revisions"] == 1


def test_relinking_updates_rather_than_duplicates(store):
    d = doc(PAGE_V1)
    store.observe(d)
    store.link_document("UEI123", d)
    store.link_document("UEI123", d)

    assert len(store.documents_for_entity("UEI123")) == 1


def test_document_links_are_tenant_scoped_though_snapshots_are_shared(tmp_path):
    """The corpus is global; who was watching is not."""
    path = str(tmp_path / "shared.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        d = doc(PAGE_V1)
        acme.observe(d)
        acme.link_document("UEI123", d)

        assert len(acme.documents_for_entity("UEI123")) == 1
        assert other.documents_for_entity("UEI123") == []
        # But the shared snapshot means the second tenant does not refetch.
        assert other.observe(d).kind == "unchanged"
    finally:
        acme.close()
        other.close()


# ---------------------------------------------------------------------- API

AUTH = {"X-API-Key": "secret-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)

    store = app_module.store_for("acme")
    a, b = doc(PAGE_V1), doc(PAGE_V2)
    store.observe(a)
    store.link_document("UEI123", a)
    store.observe(b)
    store.link_document("UEI123", b)
    return TestClient(app_module.app), app_module


def test_documents_endpoint_lists_by_entity(client):
    c, _ = client
    body = c.get("/v1/documents?entity_key=UEI123", headers=AUTH).json()
    assert len(body["documents"]) == 1
    assert body["documents"][0]["revisions"] == 2


def test_history_endpoint(client):
    c, _ = client
    body = c.get("/v1/documents/history?source=web&key=web:acme.com/news",
                 headers=AUTH).json()
    assert len(body["revisions"]) == 2
    assert all(r["has_body"] for r in body["revisions"])


def test_diff_endpoint_defaults_to_the_two_most_recent(client):
    c, _ = client
    body = c.get("/v1/documents/diff?source=web&key=web:acme.com/news",
                 headers=AUTH).json()
    assert body["stats"]["added"] == 1
    assert body["baseline"] is False


def test_diff_endpoint_handles_keys_containing_colons_and_slashes(client):
    """`web:acme.com/news` is why these are query parameters, not path segments."""
    c, _ = client
    r = c.get("/v1/documents/diff", params={"source": "web",
                                           "key": "web:acme.com/news"},
              headers=AUTH)
    assert r.status_code == 200


def test_unknown_document_is_404(client):
    c, _ = client
    assert c.get("/v1/documents/history?source=web&key=nope",
                 headers=AUTH).status_code == 404
    assert c.get("/v1/documents/diff?source=web&key=nope",
                 headers=AUTH).status_code == 404


def test_document_endpoints_need_a_key(client):
    c, _ = client
    assert c.get("/v1/documents?entity_key=UEI123").status_code == 401
    assert c.get("/v1/documents/diff?source=web&key=x").status_code == 401
