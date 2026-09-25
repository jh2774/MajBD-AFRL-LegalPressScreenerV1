"""Phase 1 service: browser fallback, tenancy, notice gate, API.

Offline. The browser is a stub — these assert the *escalation logic*, that a
page unreadable over plain HTTP gets retried through a renderer and that a
missing renderer is reported rather than silently treated as an empty page.
Whether Chromium itself works is not something a unit test can tell you.
"""
from __future__ import annotations

import json

import pytest

from foci_screen.api import auth
from foci_screen.connectors.webwatch import WebWatchConnector
from foci_screen.models import Contract, Entity, Finding, Signal
from foci_screen.store import Store

# A real page: enough prose to clear the shell threshold.
REAL_PAGE = ("<html><body><main>" + ("Lockheed Martin announced today. " * 60)
             + "</main></body></html>")
# What an IR platform serves a non-browser client.
SHELL = '<html><body><div id="root"></div><noscript>Enable JavaScript</noscript></body></html>'


class StubHttp:
    """Returns a canned response per URL; records what was asked for."""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.stats = {"hits": 0, "misses": 0, "errors": 0}

    def get(self, url, **kw):
        self.calls.append(url)
        return self.responses.get(url, {"status": 404, "text": "", "json": None})


class StubBrowser:
    """Stands in for Playwright."""

    def __init__(self, pages: dict, available: bool = True) -> None:
        self.pages = pages
        self.available = available
        self.rendered: list[str] = []

    def render(self, url: str) -> str:
        self.rendered.append(url)
        return self.pages.get(url, "")

    def close(self) -> None:
        pass


# ------------------------------------------------------------ browser fallback

def test_plain_fetch_is_not_escalated_when_it_works():
    http = StubHttp({"https://x.com/news": {"status": 200, "text": REAL_PAGE}})
    browser = StubBrowser({})
    web = WebWatchConnector(http, browser=browser)

    doc = web.fetch("https://x.com/news", kind="press", company="X")

    assert doc is not None
    assert doc.meta["rendered_with"] == "http"
    assert browser.rendered == [], "browser must not be used when HTTP suffices"


def test_javascript_shell_escalates_to_browser():
    """A 200 carrying an empty app shell is the IR-platform failure mode."""
    url = "https://investors.x.com/news"
    http = StubHttp({url: {"status": 200, "text": SHELL}})
    browser = StubBrowser({url: REAL_PAGE})
    web = WebWatchConnector(http, browser=browser)

    doc = web.fetch(url, kind="investor_relations", company="X")

    assert doc is not None
    assert doc.meta["rendered_with"] == "browser"
    assert "Lockheed Martin announced" in doc.text
    assert browser.rendered == [url]


def test_timeout_escalates_to_browser():
    url = "https://investors.x.com/news"
    http = StubHttp({url: {"status": 0, "text": "", "error": "ReadTimeout"}})
    browser = StubBrowser({url: REAL_PAGE})
    web = WebWatchConnector(http, browser=browser)

    doc = web.fetch(url, kind="investor_relations")

    assert doc is not None and doc.meta["rendered_with"] == "browser"


def test_known_browser_host_skips_the_doomed_plain_fetch():
    """Second page on a host already proven to need a browser."""
    a, b = "https://investors.x.com/one", "https://investors.x.com/two"
    http = StubHttp({a: {"status": 200, "text": SHELL}})
    browser = StubBrowser({a: REAL_PAGE, b: REAL_PAGE})
    web = WebWatchConnector(http, browser=browser)

    web.fetch(a)
    http.calls.clear()
    web.fetch(b)

    assert http.calls == [], "host is known bad; plain HTTP should be skipped"
    assert b in browser.rendered


def test_unreadable_host_is_reported_when_no_browser():
    """Silence here would read as 'nothing found', which is a different claim."""
    url = "https://investors.x.com/news"
    http = StubHttp({url: {"status": 403, "text": ""}})
    web = WebWatchConnector(http, browser=None)

    assert web.fetch(url) is None
    assert "investors.x.com" in web.skipped_js_hosts


def test_unavailable_browser_is_treated_as_no_browser():
    url = "https://investors.x.com/news"
    http = StubHttp({url: {"status": 403, "text": ""}})
    web = WebWatchConnector(http, browser=StubBrowser({}, available=False))

    assert web.fetch(url) is None
    assert "investors.x.com" in web.skipped_js_hosts


def test_browser_failure_does_not_crash_the_screen():
    url = "https://investors.x.com/news"
    http = StubHttp({url: {"status": 200, "text": SHELL}})
    web = WebWatchConnector(http, browser=StubBrowser({}))   # renders ""

    assert web.fetch(url) is None


# ------------------------------------------------------------- normalisation

def test_nav_rail_in_main_does_not_hide_the_page():
    """Regression: Lockheed's newsroom puts a nav rail in <main>.

    Trusting the tag discarded 98% of that page — 150KB of HTML normalised to
    97 characters of menu labels, and every rule downstream saw a nav menu
    instead of press releases.
    """
    from foci_screen.connectors.webwatch import normalise_page

    body_prose = "Acme entered into a definitive agreement with an investor. " * 40
    html = (f"<html><body><main><nav>Media Contacts Press Archive</nav></main>"
            f"<div class='content'><p>{body_prose}</p></div></body></html>")

    text = normalise_page(html)

    assert "definitive agreement" in text
    assert len(text) > 1000


def test_substantial_main_is_still_preferred():
    """The <main> preference is right when <main> actually holds the content."""
    from foci_screen.connectors.webwatch import normalise_page

    content = "Acme announced a strategic investment today. " * 40
    html = (f"<html><body><main><p>{content}</p></main>"
            f"<footer>Careers Privacy Investors Sitemap</footer></body></html>")

    text = normalise_page(html)

    assert "strategic investment" in text
    assert "Sitemap" not in text, "footer should stay out when main is real"


# -------------------------------------------------------------------- storage

@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"), tenant_id="acme")
    yield s
    s.close()


def _finding(name="ACME", severity="high", score=20.0, run_id="r1") -> Finding:
    return Finding(
        entity=Entity(name=name, uei="UEI1"),
        signals=[Signal(rule_id="R-1", category="FOCI", severity=severity, score=score,
                        title="t", rationale="why", evidence="e", source="sec_edgar")],
        contracts=[Contract(award_id="A1", piid="P1", recipient_name=name)],
        total_score=score, severity=severity, run_id=run_id)


def test_findings_are_scoped_to_their_tenant(tmp_path):
    path = str(tmp_path / "shared.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        acme.start_run("DoD", {}, run_id="r1")
        acme.save_finding(_finding())

        assert len(acme.search_findings()) == 1
        assert other.search_findings() == [], "tenants must not see each other"
    finally:
        acme.close()
        other.close()


def test_snapshots_are_shared_across_tenants(tmp_path):
    """The cost saving: ten tenants watching one prime fetch its 10-K once."""
    from foci_screen.models import Document

    path = str(tmp_path / "shared.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        doc = Document(source="sec_edgar", key="k1", text="body")
        assert acme.observe(doc).kind == "new"
        assert other.observe(doc).kind == "unchanged"
    finally:
        acme.close()
        other.close()


def test_run_lifecycle(store):
    run_id = store.start_run("DoD", {"months_back": 9}, status="queued")
    assert store.get_run(run_id)["status"] == "queued"

    store.mark_running(run_id)
    assert store.get_run(run_id)["status"] == "running"
    assert store.get_run(run_id)["finished_at"] is None, "running runs are not finished"

    store.set_run_progress(run_id, "screening LOCKHEED")
    store.finish_run(run_id, stats={"awards_examined": 3})

    run = store.get_run(run_id)
    assert run["status"] == "complete"
    assert run["progress"] == "screening LOCKHEED"
    assert json.loads(run["stats"])["awards_examined"] == 3


def test_failed_run_records_its_error(store):
    run_id = store.start_run("DoD", {}, status="queued")
    store.finish_run(run_id, status="failed", error="ConnectionError: nope")

    run = store.get_run(run_id)
    assert run["status"] == "failed"
    assert "ConnectionError" in run["error"]


def test_notice_starts_pending_and_records_its_decision(store):
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="[HIGH] flag", body_text="original body")

    assert store.get_notice(notice_id)["status"] == "pending"

    store.decide_notice(notice_id, "approved", decided_by="analyst@org",
                        note="verified", body_text="edited body")
    notice = store.get_notice(notice_id)

    assert notice["status"] == "approved"
    assert notice["decided_by"] == "analyst@org"
    # What was approved is what goes out — not a re-render.
    assert notice["body_text"] == "edited body"


def test_rejecting_keeps_the_original_body(store):
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="s", body_text="original")
    store.decide_notice(notice_id, "rejected", decided_by="a", note="false positive")

    notice = store.get_notice(notice_id)
    assert notice["status"] == "rejected"
    assert notice["body_text"] == "original"
    assert notice["decision_note"] == "false positive"


def _pending(store, body="original body"):
    return store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="[HIGH] flag", body_text=body)


def test_editing_keeps_the_generated_text(store):
    """What the tool wrote and what a person sent are different claims."""
    notice_id = _pending(store)
    assert store.decide_notice(notice_id, "approved", decided_by="a",
                               body_text="reworded body") == "ok"

    notice = store.get_notice(notice_id)
    assert notice["body_text"] == "reworded body"
    assert notice["original_body_text"] == "original body"

    edits = store.notice_edits(notice)
    assert edits["stats"] == {"added": 1, "removed": 1}


def test_unchanged_body_is_not_recorded_as_an_edit(store):
    notice_id = _pending(store)
    store.decide_notice(notice_id, "approved", decided_by="a",
                        body_text="  original body \n")

    notice = store.get_notice(notice_id)
    assert notice["original_body_text"] is None
    assert store.notice_edits(notice) is None


def test_a_decision_is_final(store):
    """A rejection must not be quietly overturned — it is the only labelled
    false-positive data the tool gets."""
    notice_id = _pending(store)
    assert store.decide_notice(notice_id, "rejected", decided_by="a") == "ok"
    assert store.decide_notice(notice_id, "approved", decided_by="b") == "already_decided"
    assert store.get_notice(notice_id)["status"] == "rejected"
    assert store.get_notice(notice_id)["decided_by"] == "a"


def test_deciding_an_unknown_notice(store):
    assert store.decide_notice("nope", "approved", decided_by="a") == "not_found"


def test_opening_a_pre_edit_database_adds_the_original_body_column(tmp_path):
    import sqlite3

    path = str(tmp_path / "v04.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE notices (
            notice_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'default',
            run_id TEXT NOT NULL, finding_id TEXT, entity_key TEXT NOT NULL,
            entity_name TEXT NOT NULL, severity TEXT NOT NULL, recipient TEXT,
            officer_confidence TEXT, subject TEXT NOT NULL, body_text TEXT NOT NULL,
            status TEXT NOT NULL, decided_by TEXT, decided_at TEXT,
            decision_note TEXT, created_at TEXT NOT NULL);
    """)
    old.commit()
    old.close()

    s = Store(path)
    try:
        notice_id = _pending(s)
        assert s.decide_notice(notice_id, "approved", decided_by="a",
                               body_text="changed") == "ok"
        assert s.get_notice(notice_id)["original_body_text"] == "original body"
    finally:
        s.close()


def test_disclaimer_check_survives_word_wrapping():
    """The body wraps and indents the disclaimer; an untouched notice must pass."""
    from foci_screen.notify.render import DISCLAIMER, _wrap, has_disclaimer

    wrapped = "Dear KO,\n\n  " + "\n  ".join(_wrap(DISCLAIMER, 92)) + "\n"
    assert has_disclaimer(wrapped)
    assert not has_disclaimer("Dear KO,\n\nAcme is controlled by a foreign power.")


def test_ddl_splitter_ignores_semicolons_in_comments():
    """Regression: a semicolon inside a schema comment cut the next CREATE
    TABLE in half, and the failure only ever showed on a fresh database."""
    from foci_screen.store import _statements

    ddl = """
    -- Snapshots are global; which documents were gathered is not.
    CREATE TABLE a (x TEXT);
    CREATE TABLE b (y TEXT);
    """
    statements = _statements(ddl)

    assert len(statements) == 2
    assert statements[0].startswith("CREATE TABLE a")
    assert statements[1].startswith("CREATE TABLE b")


def test_every_schema_statement_is_valid(tmp_path):
    """Builds the real schema from scratch, which is what a first deploy does."""
    s = Store(str(tmp_path / "fresh.db"))
    try:
        tables = {r["name"] for r in s._query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        s.close()

    assert {"runs", "snapshots", "snapshot_history", "snapshot_bodies",
            "entity_documents", "findings", "notices", "watchlists",
            "contracts", "notifications"} <= tables


def test_opening_a_v1_database_migrates_it(tmp_path):
    """An existing single-user database must survive the upgrade.

    `CREATE TABLE IF NOT EXISTS` is a no-op on a table that already exists, so
    without explicit column migration a v0.1 database keeps its old shape and
    then fails on the first index over `tenant_id`.
    """
    import sqlite3

    path = str(tmp_path / "legacy.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                           finished_at TEXT, agency TEXT, params TEXT);
        CREATE TABLE findings (id INTEGER PRIMARY KEY AUTOINCREMENT,
                               run_id TEXT NOT NULL, entity_key TEXT NOT NULL,
                               entity_name TEXT NOT NULL, severity TEXT NOT NULL,
                               score REAL NOT NULL, payload TEXT NOT NULL,
                               created_at TEXT NOT NULL);
        INSERT INTO runs VALUES ('old1', '2026-01-01T00:00:00Z', NULL, 'DoD', '{}');
        INSERT INTO findings (run_id, entity_key, entity_name, severity, score,
                              payload, created_at)
        VALUES ('old1', 'UEI1', 'ACME', 'high', 20.0, '{"signals": []}',
                '2026-01-01T00:00:00Z');
    """)
    old.commit()
    old.close()

    s = Store(path, tenant_id="default")
    try:
        assert len(s.recent_findings()) == 1, "pre-existing findings must survive"
        assert s.get_run("old1")["status"] == "complete"
        # And the new columns are usable.
        s.set_run_progress("old1", "resumed")
        assert s.get_run("old1")["progress"] == "resumed"
    finally:
        s.close()


def test_watchlist_round_trip(store):
    wl = store.create_watchlist("Navy quarterly", {"agency": "DoD", "months_back": 3})

    listed = store.list_watchlists()
    assert len(listed) == 1 and listed[0]["name"] == "Navy quarterly"

    store.mark_watchlist_run(wl, "run-9")
    assert store.list_watchlists()[0]["last_run_id"] == "run-9"

    assert store.set_watchlist_active(wl, False) is True
    assert store.list_watchlists(active_only=True) == []
    assert store.set_watchlist_active("nonexistent", False) is False


# ----------------------------------------------------------------------- auth

def test_parse_keys():
    assert auth.parse_keys("acme:k1,navy:k2") == {"k1": "acme", "k2": "navy"}
    assert auth.parse_keys("") == {}
    # A bare entry used to be discarded, which produced a server that started
    # cleanly and then refused everything. It is now a key for the default
    # tenant — see test_a_key_without_a_tenant_prefix_still_works.
    assert auth.parse_keys("malformed") == {"malformed": "default"}


def test_resolve_tenant():
    keymap = {"k1": "acme"}
    assert auth.resolve_tenant(keymap, "k1") == "acme"
    assert auth.resolve_tenant(keymap, "k2") == ""
    assert auth.resolve_tenant({}, "k1") == ""


# ------------------------------------------------------------------------ API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A fresh app bound to a temp database and one known key."""
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    # Otherwise a run *on* one of these platforms fails the "sound deployment"
    # assertions for reasons that have nothing to do with the code under test.
    for var in ("RENDER", "DYNO", "FLY_APP_NAME", "K_SERVICE", "WEBSITE_INSTANCE_ID"):
        monkeypatch.delenv(var, raising=False)

    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


AUTH = {"X-API-Key": "secret-key"}


def test_a_key_without_a_tenant_prefix_still_works():
    """The likeliest misconfiguration: pasting the key without "default:".
    Discarding it silently produced a server that started and refused
    everything, which is how a Render deployment came up closed."""
    keymap = auth.parse_keys("jh6dagger6small3unsnap0blast5john6apikey4242kopi")
    assert keymap == {"jh6dagger6small3unsnap0blast5john6apikey4242kopi": "default"}


def test_prefixed_and_bare_keys_can_be_mixed():
    keymap = auth.parse_keys("acme:sk_one, sk_two ,navy-pmo:sk_three")
    assert keymap == {"sk_one": "acme", "sk_two": "default", "sk_three": "navy-pmo"}


def test_empty_configuration_is_still_closed():
    """Leniency about the prefix must not become leniency about having a key."""
    for raw in ("", "   ", ",,", ":", "acme:"):
        assert auth.parse_keys(raw) == {}, raw


def test_a_closed_api_says_how_to_open_it(tmp_path, monkeypatch):
    """With no keys the symptom is a web page showing nothing, which reads as
    missing data rather than missing configuration. The 503 has to carry the fix."""
    monkeypatch.setenv("FOCI_API_KEYS", "")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "closed.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    c = TestClient(app_module.app)

    r = c.get("/v1/overview")
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "FOCI_API_KEYS" in detail
    assert "tenant:key" in detail
    assert "default" in detail, "the CLI writes as tenant 'default'; say so"

    # And the unauthenticated probe reports it too, so it is visible without a key.
    assert c.get("/health").json()["authenticated"] is False


def test_health_needs_no_key(client):
    c, _ = client
    body = c.get("/health").json()
    assert body["status"] == "ok"
    # Present so a deploy can be confirmed as the intended build without a key.
    assert "version" in body
    assert body["authenticated"] is True


def test_health_is_quiet_when_nothing_is_misconfigured(client):
    """A warning that fires on a correct local install is a warning nobody
    reads by the third time they see it."""
    c, _ = client
    assert c.get("/health").json()["warnings"] == []


def _reloaded_app(tmp_path, monkeypatch, **env):
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("FOCI_DB", str(tmp_path / "health.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    for var in ("RENDER", "DYNO", "FLY_APP_NAME", "K_SERVICE", "WEBSITE_INSTANCE_ID"):
        monkeypatch.delenv(var, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_health_warns_that_a_managed_host_discards_a_sqlite_database(tmp_path, monkeypatch):
    """The failure this tool cannot survive, and the one nothing reveals.

    Snapshots *are* the change-detection baseline. On an ephemeral filesystem
    every restart throws them away, so the next run reads every document as
    new — the alert storm the whole design exists to prevent. From outside,
    that deployment is indistinguishable from a healthy one: it answers, it
    renders, and the data quietly goes.
    """
    c = _reloaded_app(tmp_path, monkeypatch, RENDER="true",
                      FOCI_API_KEYS="acme:secret-key")
    warnings = c.get("/health").json()["warnings"]

    assert any("DATABASE_URL" in w and "Render" in w for w in warnings), warnings
    # No Redis either: a screen would run in the web process and die with it.
    assert any("REDIS_URL" in w for w in warnings), warnings


def test_health_states_plainly_whether_storage_survives_a_restart(tmp_path, monkeypatch):
    """A flag, not prose to match on.

    The web interface changes what it says on an empty dashboard depending on
    this: "nothing has been screened" and "what was screened has been thrown
    away" look identical from the browser, and only one of them means the
    reader should go and fix something. Grepping the warning text for a phrase
    would break the page the next time the wording improved.
    """
    ephemeral = _reloaded_app(tmp_path, monkeypatch, RENDER="true",
                              FOCI_API_KEYS="acme:secret-key")
    assert ephemeral.get("/health").json()["ephemeral_storage"] is True

    # A disk mounted at FOCI_DB is still SQLite; what changes is the platform.
    local = _reloaded_app(tmp_path, monkeypatch, FOCI_API_KEYS="acme:secret-key")
    assert local.get("/health").json()["ephemeral_storage"] is False

    on_postgres = _reloaded_app(tmp_path, monkeypatch, RENDER="true",
                                FOCI_API_KEYS="acme:secret-key",
                                DATABASE_URL="postgres://u:p@example.invalid:5432/f")
    assert on_postgres.get("/health").json()["ephemeral_storage"] is False


def test_health_reports_a_closed_api_as_a_warning(tmp_path, monkeypatch):
    c = _reloaded_app(tmp_path, monkeypatch, FOCI_API_KEYS="")
    warnings = c.get("/health").json()["warnings"]
    assert any("FOCI_API_KEYS" in w for w in warnings), warnings


def test_sqlite_alone_is_not_a_warning(tmp_path, monkeypatch):
    """SQLite is the right answer for the CLI and for local development. It is
    only wrong where the disk does not survive a restart."""
    c = _reloaded_app(tmp_path, monkeypatch, FOCI_API_KEYS="acme:secret-key")
    assert c.get("/health").json()["warnings"] == []


def test_health_warns_when_the_postgres_driver_is_missing(tmp_path, monkeypatch):
    """A build that installed the package without the `postgres` extra boots,
    serves the web UI, and 500s on the first query. `/health` is the only place
    that can say so before a request goes wrong."""
    c = _reloaded_app(tmp_path, monkeypatch, FOCI_API_KEYS="acme:secret-key",
                      DATABASE_URL="postgres://u:p@example.invalid:5432/foci")
    from foci_screen.api import app as app_module
    monkeypatch.setattr(app_module, "postgres_driver_available", lambda: False)

    warnings = c.get("/health").json()["warnings"]
    assert any("psycopg" in w and "postgres" in w for w in warnings), warnings
    # And the warning is about the driver, not about the database being wrong:
    # Postgres is the configuration we have been asking for.
    assert c.get("/health").json()["database"] == "postgres"


def test_no_driver_warning_when_psycopg_is_present(tmp_path, monkeypatch):
    c = _reloaded_app(tmp_path, monkeypatch, FOCI_API_KEYS="acme:secret-key",
                      DATABASE_URL="postgres://u:p@example.invalid:5432/foci")
    from foci_screen.api import app as app_module
    monkeypatch.setattr(app_module, "postgres_driver_available", lambda: True)

    assert c.get("/health").json()["warnings"] == []


def test_a_missing_driver_names_the_extra_rather_than_the_module(monkeypatch):
    """"No module named 'psycopg'" in a 500 tells you nothing about the fix."""
    import sys

    from foci_screen.store import Store

    # None in sys.modules makes `import psycopg` raise ImportError, whether or
    # not the real driver is installed on the machine running the tests.
    monkeypatch.setitem(sys.modules, "psycopg", None)

    with pytest.raises(RuntimeError) as err:
        Store("postgres://u:p@example.invalid:5432/foci")
    message = str(err.value)
    assert "psycopg" in message
    assert "postgres" in message, "name the extra to install"
    assert "DATABASE_URL" in message, "name the variable that put it in this state"


def test_managed_host_is_named_so_a_warning_can_say_where(monkeypatch):
    from foci_screen.config import Config

    for var in ("RENDER", "DYNO", "FLY_APP_NAME", "K_SERVICE", "WEBSITE_INSTANCE_ID"):
        monkeypatch.delenv(var, raising=False)
    assert Config().managed_host == ""

    monkeypatch.setenv("FLY_APP_NAME", "foci")
    assert Config().managed_host == "Fly.io"


def test_authenticated_routes_reject_missing_and_wrong_keys(client):
    c, _ = client
    assert c.get("/v1/findings").status_code == 401
    assert c.get("/v1/findings", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/v1/findings", headers=AUTH).status_code == 200


def test_bearer_token_is_accepted(client):
    c, _ = client
    r = c.get("/v1/findings", headers={"Authorization": "Bearer secret-key"})
    assert r.status_code == 200


def test_api_fails_closed_with_no_keys_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "closed.db"))
    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)

    c = TestClient(app_module.app)
    assert c.get("/health").status_code == 200          # probe still works
    assert c.get("/v1/findings", headers=AUTH).status_code == 503


def test_creating_a_screen_returns_immediately(client, monkeypatch):
    c, app_module = client
    enqueued = []
    monkeypatch.setattr(app_module.queue, "enqueue",
                        lambda t, r, o: enqueued.append((t, r, o)))

    r = c.post("/v1/screens", headers=AUTH,
               json={"agency": "Department of Defense", "months_back": 6})

    assert r.status_code == 202
    run_id = r.json()["run_id"]
    assert r.json()["status"] == "queued"
    assert enqueued[0][0] == "acme" and enqueued[0][1] == run_id

    status = c.get(f"/v1/screens/{run_id}", headers=AUTH).json()
    assert status["status"] == "queued"


def test_screen_request_is_validated(client):
    c, _ = client
    assert c.post("/v1/screens", headers=AUTH, json={"agency": "X"}).status_code == 422
    assert c.post("/v1/screens", headers=AUTH,
                  json={"agency": "Department of Defense",
                        "min_severity": "catastrophic"}).status_code == 422


def test_unknown_run_is_404(client):
    c, _ = client
    assert c.get("/v1/screens/nope", headers=AUTH).status_code == 404


def test_notice_approval_flow(client):
    c, app_module = client
    store = app_module.store_for("acme")
    store.start_run("DoD", {}, run_id="r1")
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="[HIGH] flag", body_text="body")

    listed = c.get("/v1/notices?status=pending", headers=AUTH).json()["notices"]
    assert len(listed) == 1

    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH,
               json={"decided_by": "analyst@org", "note": "confirmed"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    # Approval records a decision; it must not imply transmission.
    assert r.json()["delivery"]["mode"] == "render_only"


def test_unaddressable_notice_cannot_be_approved(client):
    """No resolved KO means no recipient — refuse rather than guess."""
    c, app_module = client
    store = app_module.store_for("acme")
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="", officer_confidence="unresolved",
        subject="s", body_text="body")

    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH, json={})
    assert r.status_code == 409


def _notice_with_disclaimer(app_module, prose="Dear KO,\n\nACME flagged."):
    from foci_screen.notify.render import DISCLAIMER, _wrap

    body = prose + "\n\n  " + "\n  ".join(_wrap(DISCLAIMER, 92)) + "\n"
    store = app_module.store_for("acme")
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="[HIGH] flag", body_text=body)
    return notice_id, body


def test_approving_with_edits_records_both_versions(client):
    c, app_module = client
    notice_id, body = _notice_with_disclaimer(app_module)
    edited = body.replace("ACME flagged.", "ACME flagged; SAM ownership unconfirmed.")

    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH,
               json={"note": "softened wording", "body_text": edited})
    assert r.status_code == 200
    assert r.json()["edited"] is True

    notice = c.get(f"/v1/notices/{notice_id}", headers=AUTH).json()
    assert notice["body_text"] == edited
    assert notice["original_body_text"] == body
    assert notice["edits"]["stats"] == {"added": 1, "removed": 1}

    # And what goes out is the edited text, not a re-render.
    eml = c.get(f"/v1/notices/{notice_id}.eml", headers=AUTH).content
    assert b"SAM ownership unconfirmed" in eml


def test_an_edit_cannot_remove_the_disclaimer(client):
    """That paragraph is what keeps a notice from being an assertion about a
    named company. Refused, and nothing is recorded."""
    c, app_module = client
    notice_id, _ = _notice_with_disclaimer(app_module)

    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH,
               json={"body_text": "Dear KO,\n\nACME is foreign controlled."})
    assert r.status_code == 422
    assert "limitations statement" in r.json()["detail"]

    notice = c.get(f"/v1/notices/{notice_id}", headers=AUTH).json()
    assert notice["status"] == "pending"
    assert notice["edits"] is None


def test_rejected_notice_cannot_then_be_approved(client):
    c, app_module = client
    notice_id, _ = _notice_with_disclaimer(app_module)

    assert c.post(f"/v1/notices/{notice_id}/reject", headers=AUTH,
                  json={"note": "missile range, not an investor"}).status_code == 200
    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH, json={})
    assert r.status_code == 409

    notice = c.get(f"/v1/notices/{notice_id}", headers=AUTH).json()
    assert notice["status"] == "rejected"


def test_decision_without_a_name_is_attributed_to_the_key_not_the_tenant(client):
    """Regression: a tenant named "default" recorded "decided by default",
    which reads as an automatic approval on the record proving a person looked."""
    c, app_module = client
    notice_id, _ = _notice_with_disclaimer(app_module)

    c.post(f"/v1/notices/{notice_id}/reject", headers=AUTH, json={"note": "fp"})

    notice = c.get(f"/v1/notices/{notice_id}", headers=AUTH).json()
    assert notice["decided_by"] == "api-key:acme"


def test_notice_list_carries_edits(client):
    c, app_module = client
    notice_id, body = _notice_with_disclaimer(app_module)
    c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH,
           json={"body_text": body.replace("flagged", "flagged for review")})

    listed = c.get("/v1/notices", headers=AUTH).json()["notices"]
    assert listed[0]["edits"]["stats"]["added"] == 1


def test_notice_downloads_as_eml(client):
    c, app_module = client
    store = app_module.store_for("acme")
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="[HIGH] FOCI flag", body_text="the body")

    r = c.get(f"/v1/notices/{notice_id}.eml", headers=AUTH)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("message/rfc822")
    assert b"the body" in r.content
    assert b"ko@mail.mil" in r.content


def test_watchlist_endpoints(client):
    c, _ = client
    r = c.post("/v1/watchlists", headers=AUTH,
               json={"name": "Navy", "screen": {"agency": "Department of the Navy"}})
    assert r.status_code == 201
    wl = r.json()["watchlist_id"]

    assert len(c.get("/v1/watchlists", headers=AUTH).json()["watchlists"]) == 1
    assert c.delete(f"/v1/watchlists/{wl}", headers=AUTH).status_code == 200
    assert c.delete("/v1/watchlists/nope", headers=AUTH).status_code == 404


def test_tenants_cannot_read_each_others_notices(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:key-a,other:key-b")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "multi.db"))
    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    c = TestClient(app_module.app)

    notice_id = app_module.store_for("acme").create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="s", body_text="b")

    assert c.get(f"/v1/notices/{notice_id}",
                 headers={"X-API-Key": "key-a"}).status_code == 200
    assert c.get(f"/v1/notices/{notice_id}",
                 headers={"X-API-Key": "key-b"}).status_code == 404
