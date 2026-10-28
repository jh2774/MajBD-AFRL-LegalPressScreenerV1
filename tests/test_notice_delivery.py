"""Approved notices reach Gmail, under the same guards as everything else.

Approval used to be the end of the line: the decision was recorded and the
response said "Approved notices are sent" while nothing was. These cover the
step after approval — where the notice goes, what is recorded about it, and
that nothing can be delivered without a person having approved it first.
"""
from __future__ import annotations

import base64
import email

import pytest

from foci_screen.config import Config
from foci_screen.notify.gmail import GmailNotifier
from foci_screen.notify.render import DISCLAIMER, _wrap
from foci_screen.store import Store

AUTH = {"X-API-Key": "secret-key"}


class FakeGmail:
    """Just enough of the Gmail client: records what it was handed."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.drafts_made: list[email.message.Message] = []
        self.sent: list[email.message.Message] = []
        self.fail = fail

    def users(self):
        return self

    def _take(self, raw: str, into: list) -> None:
        if self.fail:
            raise self.fail
        into.append(email.message_from_bytes(base64.urlsafe_b64decode(raw)))

    def drafts(self):
        outer = self

        class Drafts:
            def create(self, userId, body):
                outer._take(body["message"]["raw"], outer.drafts_made)
                return _Exec({"id": "d-1"})
        return Drafts()

    def messages(self):
        outer = self

        class Messages:
            def send(self, userId, body):
                outer._take(body["raw"], outer.sent)
                return _Exec({"id": "m-1"})
        return Messages()


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


def _body(prose="Dear KO,\n\nACME flagged."):
    return prose + "\n\n  " + "\n  ".join(_wrap(DISCLAIMER, 92)) + "\n"


def _notice(store, recipient="ko@mail.mil"):
    return store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient=recipient, officer_confidence="high",
        subject="[HIGH] FOCI screening flag: ACME", body_text=_body())


def _text_of(msg) -> str:
    return msg.get_body(preferencelist=("plain",)).get_content() \
        if hasattr(msg, "get_body") else msg.get_payload(0).get_payload(decode=True).decode()


# ------------------------------------------------------------------- store

@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    yield s
    s.close()


def test_a_pending_notice_cannot_be_delivered(store):
    notice_id = _notice(store)
    assert store.record_notice_delivery(notice_id, "drafted",
                                        delivered_to="ko@mail.mil") == "not_approved"
    assert store.get_notice(notice_id)["status"] == "pending"


def test_a_draft_moves_the_notice_on_and_records_where_it_went(store):
    notice_id = _notice(store)
    store.decide_notice(notice_id, "approved", decided_by="a")

    assert store.record_notice_delivery(notice_id, "drafted", delivered_to="ko@mail.mil",
                                        detail="gmail draft id d-1") == "ok"
    notice = store.get_notice(notice_id)
    assert notice["status"] == "drafted"
    assert notice["delivered_to"] == "ko@mail.mil"
    assert notice["delivered_at"]
    # The decision is untouched by delivery.
    assert notice["decided_by"] == "a"
    assert store.undelivered_notices() == []


def test_a_failed_delivery_stays_approved_with_its_reason(store):
    notice_id = _notice(store)
    store.decide_notice(notice_id, "approved", decided_by="a")

    store.record_notice_delivery(notice_id, "error", delivered_to="ko@mail.mil",
                                 detail="not signed in")
    notice = store.get_notice(notice_id)
    assert notice["status"] == "approved"
    assert notice["delivery_detail"] == "not signed in"
    assert notice["delivered_at"] is None
    assert [n["notice_id"] for n in store.undelivered_notices()] == [notice_id]


def test_a_delivered_notice_is_not_delivered_twice(store):
    notice_id = _notice(store)
    store.decide_notice(notice_id, "approved", decided_by="a")
    store.record_notice_delivery(notice_id, "sent", delivered_to="ko@mail.mil")
    assert store.record_notice_delivery(notice_id, "sent",
                                        delivered_to="ko@mail.mil") == "not_approved"


def test_a_delivered_notice_still_counts_as_a_flag_for_portfolio_alerts(store):
    notice_id = _notice(store)
    store.decide_notice(notice_id, "approved", decided_by="a")
    store.record_notice_delivery(notice_id, "drafted", delivered_to="ko@mail.mil")
    assert [n["notice_id"] for n in store.notices_for_entities(["UEI1"])] == [notice_id]


# ------------------------------------------------------------------- gmail

def _cfg(tmp_path, **kw) -> Config:
    base = dict(out_dir=str(tmp_path), gmail_enabled=True, gmail_send=False,
                gmail_sender="screen@agency.example", email_redirect_to="",
                gmail_token=str(tmp_path / "token.json"))
    return Config(**{**base, **kw})


def _approved_row(store, body_text=""):
    notice_id = _notice(store)
    store.decide_notice(notice_id, "approved", decided_by="a", body_text=body_text)
    return store.get_notice(notice_id)


def test_gmail_off_renders_only(tmp_path, store):
    result = GmailNotifier(_cfg(tmp_path, gmail_enabled=False)).deliver_notice(
        _approved_row(store))
    assert result.status == "rendered"
    assert "GMAIL_ENABLED" in result.detail


def test_the_approved_text_is_what_reaches_gmail(tmp_path, store):
    edited = _body("Dear KO,\n\nACME flagged; SAM ownership unconfirmed.")
    notifier = GmailNotifier(_cfg(tmp_path))
    notifier._service = gmail = FakeGmail()

    result = notifier.deliver_notice(_approved_row(store, body_text=edited))

    assert result.status == "drafted"
    assert gmail.sent == []                      # a draft, not a send
    msg = gmail.drafts_made[0]
    assert msg["To"] == "ko@mail.mil"
    assert "SAM ownership unconfirmed" in _text_of(msg)


def test_send_mode_sends(tmp_path, store):
    notifier = GmailNotifier(_cfg(tmp_path, gmail_send=True))
    notifier._service = gmail = FakeGmail()
    assert notifier.deliver_notice(_approved_row(store)).status == "sent"
    assert len(gmail.sent) == 1 and gmail.drafts_made == []


def test_redirect_names_the_intended_officer(tmp_path, store):
    notifier = GmailNotifier(_cfg(tmp_path, email_redirect_to="pilot@agency.example"))
    notifier._service = gmail = FakeGmail()

    result = notifier.deliver_notice(_approved_row(store))

    msg = gmail.drafts_made[0]
    assert result.recipient == msg["To"] == "pilot@agency.example"
    assert msg["Subject"].startswith("[for ko@mail.mil] ")


def test_without_a_sign_in_the_service_does_not_open_a_browser(tmp_path, store):
    result = GmailNotifier(_cfg(tmp_path)).deliver_notice(_approved_row(store))
    assert result.status == "error"
    try:
        import google_auth_oauthlib  # noqa: F401
    except ImportError:
        assert "not installed" in result.detail
    else:
        assert "gmail-auth" in result.detail


def test_no_token_with_the_libraries_present_never_starts_a_sign_in(
        tmp_path, store, monkeypatch):
    """The web service has nobody to complete a browser sign-in; starting one
    would hang the request forever. Stubbed, so it runs without the extra."""
    import sys
    import types

    started = []

    class Flow:
        @classmethod
        def from_client_secrets_file(cls, *a, **k):
            started.append(a)
            return cls()

    stubs = {
        "google": types.ModuleType("google"),
        "google.auth": types.ModuleType("google.auth"),
        "google.auth.transport": types.ModuleType("google.auth.transport"),
        "google.auth.transport.requests": types.SimpleNamespace(Request=object),
        "google.oauth2": types.ModuleType("google.oauth2"),
        "google.oauth2.credentials": types.SimpleNamespace(Credentials=object),
        "google_auth_oauthlib": types.ModuleType("google_auth_oauthlib"),
        "google_auth_oauthlib.flow": types.SimpleNamespace(InstalledAppFlow=Flow),
        "googleapiclient": types.ModuleType("googleapiclient"),
        "googleapiclient.discovery": types.SimpleNamespace(build=None),
    }
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)

    result = GmailNotifier(_cfg(tmp_path)).deliver_notice(_approved_row(store))

    assert result.status == "error"
    assert "gmail-auth" in result.detail
    assert started == []


def test_a_gmail_failure_is_an_error_not_a_crash(tmp_path, store):
    notifier = GmailNotifier(_cfg(tmp_path))
    notifier._service = FakeGmail(fail=RuntimeError("quota exceeded\nsecret detail"))
    result = notifier.deliver_notice(_approved_row(store))
    assert result.status == "error"
    assert "quota exceeded" in result.detail
    assert "secret detail" not in result.detail


# --------------------------------------------------------------------- api

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    for var in ("DATABASE_URL", "REDIS_URL", "GMAIL_ENABLED", "GMAIL_SEND",
                "FOCI_EMAIL_REDIRECT_TO", "RENDER", "DYNO", "FLY_APP_NAME",
                "K_SERVICE", "WEBSITE_INSTANCE_ID"):
        monkeypatch.delenv(var, raising=False)

    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


def _arm(app_module, monkeypatch, **flags) -> FakeGmail:
    """Switch Gmail on for the running app, with a fake client behind it."""
    monkeypatch.setattr(app_module.cfg, "gmail_enabled", True)
    for name, value in flags.items():
        monkeypatch.setattr(app_module.cfg, name, value)
    gmail = FakeGmail()
    monkeypatch.setattr(app_module._gmail, "_service", gmail)
    return gmail


def test_approval_with_gmail_off_says_nothing_was_sent(client):
    c, app_module = client
    notice_id = _notice(app_module.store_for("acme"))

    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["delivery"]["mode"] == "render_only"
    assert body["delivery"]["outcome"] == "rendered"

    notice = c.get(f"/v1/notices/{notice_id}", headers=AUTH).json()
    assert "GMAIL_ENABLED" in notice["delivery_detail"]


def test_approval_with_gmail_on_creates_a_draft(client, monkeypatch):
    c, app_module = client
    gmail = _arm(app_module, monkeypatch)
    notice_id = _notice(app_module.store_for("acme"))

    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH,
               json={"decided_by": "analyst@org"})
    assert r.json()["status"] == "drafted"
    assert r.json()["delivery"]["to"] == "ko@mail.mil"
    assert len(gmail.drafts_made) == 1

    notice = c.get(f"/v1/notices/{notice_id}", headers=AUTH).json()
    assert notice["status"] == "drafted"
    assert notice["decided_by"] == "analyst@org"


def test_a_notice_approved_while_off_can_be_delivered_later(client, monkeypatch):
    c, app_module = client
    notice_id = _notice(app_module.store_for("acme"))
    c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH, json={})

    gmail = _arm(app_module, monkeypatch)
    r = c.post(f"/v1/notices/{notice_id}/deliver", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "drafted"
    assert len(gmail.drafts_made) == 1

    # And not twice.
    assert c.post(f"/v1/notices/{notice_id}/deliver", headers=AUTH).status_code == 409
    assert len(gmail.drafts_made) == 1


def test_an_unapproved_notice_cannot_be_delivered(client, monkeypatch):
    c, app_module = client
    gmail = _arm(app_module, monkeypatch)
    store = app_module.store_for("acme")
    pending = _notice(store)
    rejected = _notice(store)
    c.post(f"/v1/notices/{rejected}/reject", headers=AUTH, json={"note": "fp"})

    assert c.post(f"/v1/notices/{pending}/deliver", headers=AUTH).status_code == 409
    assert c.post(f"/v1/notices/{rejected}/deliver", headers=AUTH).status_code == 409
    assert c.post("/v1/notices/nope/deliver", headers=AUTH).status_code == 404
    assert gmail.drafts_made == []


def test_delivery_is_scoped_to_the_tenant(client, monkeypatch):
    c, app_module = client
    gmail = _arm(app_module, monkeypatch)
    other = app_module.store_for("someone-else")
    notice_id = _notice(other)
    other.decide_notice(notice_id, "approved", decided_by="x")

    assert c.post(f"/v1/notices/{notice_id}/deliver", headers=AUTH).status_code == 404
    assert gmail.drafts_made == []


def test_health_reports_notice_delivery_and_a_missing_sign_in(tmp_path, monkeypatch):
    monkeypatch.setenv("GMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_TOKEN", str(tmp_path / "absent-token.json"))
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "h.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    health = TestClient(app_module.app).get("/health").json()

    assert health["notices"]["mode"] == "draft"
    assert any("gmail-auth" in w for w in health["warnings"])


# --------------------------------------------------------------------- cli

def test_cli_delivers_the_approved_backlog(tmp_path, monkeypatch, capsys):
    from foci_screen import cli

    db = str(tmp_path / "cli.db")
    store = Store(db)
    waiting = _notice(store)
    store.decide_notice(waiting, "approved", decided_by="a")
    untouched = _notice(store)
    store.close()

    gmail = FakeGmail()
    monkeypatch.setattr(cli, "get_config", lambda: _cfg(tmp_path, db_path=db, database_url=""))
    monkeypatch.setattr(GmailNotifier, "_connect", lambda self, interactive=True: gmail)

    assert cli.main(["deliver-notices"]) == 0
    assert "[drafted] ACME -> ko@mail.mil" in capsys.readouterr().out

    store = Store(db)
    assert store.get_notice(waiting)["status"] == "drafted"
    assert store.get_notice(untouched)["status"] == "pending"
    store.close()
    assert len(gmail.drafts_made) == 1
