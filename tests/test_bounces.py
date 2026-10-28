"""Hearing back from the mail service about addresses that bounce or complain.

The mail service accepts an alert and only later learns the address is dead,
so `Mailer.send` reports it sent and the next alert goes to the same dead
address. Brevo and Resend report these to a webhook; these cover believing
only real reports, reading both shapes, and what an alert run then does.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import time

import pytest
from fastapi.testclient import TestClient
from test_mailer import AUTH, NoFirms, Post, brevo, flagged_list

from foci_screen import alerts, cli
from foci_screen.notify import bounces
from foci_screen.notify.mailer import Mailer
from foci_screen.store import Store

SECRET_KEY = b"resend-signing-key-for-tests-only"
RESEND_SECRET = "whsec_" + base64.b64encode(SECRET_KEY).decode()
BREVO_SECRET = "brevo-hook-secret"


def svix_headers(body: bytes, at: int | None = None, msg_id="msg_1") -> dict:
    """Signed as Svix documents it, independently of the code under test."""
    stamp = str(at if at is not None else int(time.time()))
    sig = base64.b64encode(hmac.new(SECRET_KEY, f"{msg_id}.{stamp}.".encode() + body,
                                    hashlib.sha256).digest()).decode()
    return {"svix-id": msg_id, "svix-timestamp": stamp,
            "svix-signature": f"v1,{sig}", "content-type": "application/json"}


def resend_bounce(to="dead@agency.gov", kind="Permanent") -> bytes:
    return json.dumps({"type": "email.bounced", "created_at": "2026-10-25T10:00:00Z",
                       "data": {"email_id": "e1", "to": [to], "subject": "s",
                                "bounce": {"type": kind, "subType": "General",
                                           "message": "mailbox does not exist"}}}).encode()


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "b.db"), tenant_id="acme")
    yield s
    s.close()


# ------------------------------------------------------------- authenticity

def test_a_correctly_signed_resend_report_is_believed():
    body = resend_bounce()
    bounces.check_svix_signature(RESEND_SECRET, svix_headers(body), body)


@pytest.mark.parametrize("tamper", ["body", "old", "unsigned", "no-secret"])
def test_anything_else_is_not(tamper):
    body = resend_bounce()
    headers = svix_headers(body)
    secret = RESEND_SECRET
    if tamper == "body":
        body = resend_bounce(to="someone-else@agency.gov")
    elif tamper == "old":
        headers = svix_headers(body, at=int(time.time()) - 3600)
    elif tamper == "unsigned":
        headers = {"content-type": "application/json"}
    else:
        secret = ""
    with pytest.raises(bounces.NotAuthentic):
        bounces.check_svix_signature(secret, headers, body)


def test_one_good_signature_among_several_is_enough():
    """During a key rotation Svix sends one signature per key."""
    body = resend_bounce()
    headers = svix_headers(body)
    headers["svix-signature"] = "v1,bm90LWl0 " + headers["svix-signature"]
    bounces.check_svix_signature(RESEND_SECRET, headers, body)


def test_brevo_needs_the_shared_secret():
    bounces.check_shared_secret(BREVO_SECRET, BREVO_SECRET)
    for presented in ("", "wrong"):
        with pytest.raises(bounces.NotAuthentic):
            bounces.check_shared_secret(BREVO_SECRET, presented)
    with pytest.raises(bounces.NotAuthentic):
        bounces.check_shared_secret("", "")


# ------------------------------------------------------------------ parsing

def test_brevo_events_that_matter_and_those_that_do_not():
    events = bounces.parse_brevo([
        {"event": "hard_bounce", "email": "Dead@Agency.gov", "reason": "550 no such user"},
        {"event": "soft_bounce", "email": "full@agency.gov"},
        {"event": "spam", "email": "angry@agency.gov"},
        {"event": "delivered", "email": "fine@agency.gov"},
        {"event": "opened", "email": "fine@agency.gov"},
    ])
    assert [(e.email, e.kind) for e in events] == [("dead@agency.gov", "bounce"),
                                                    ("angry@agency.gov", "complaint")]
    assert events[0].reason == "550 no such user"


def test_a_temporary_resend_bounce_is_not_a_dead_address():
    assert bounces.parse_resend(json.loads(resend_bounce(kind="Transient"))) == []
    [event] = bounces.parse_resend(json.loads(resend_bounce()))
    assert (event.email, event.kind, event.reason) == \
        ("dead@agency.gov", "bounce", "mailbox does not exist")


def test_a_resend_complaint():
    [event] = bounces.parse_resend({"type": "email.complained",
                                    "data": {"to": ["angry@agency.gov"]}})
    assert event.kind == "complaint"


# -------------------------------------------------------------------- store

def test_a_complaint_is_not_downgraded_by_a_later_bounce(store):
    store.suppress_address("a@agency.gov", "complaint", "", "Brevo")
    store.suppress_address("a@agency.gov", "bounce", "550", "Brevo")
    row = store.suppressed(["a@agency.gov"])["a@agency.gov"]
    assert row["kind"] == "complaint" and row["events"] == 2


def test_a_complaint_needs_explicit_permission_to_clear(store):
    store.suppress_address("a@agency.gov", "complaint")
    assert store.clear_suppression("a@agency.gov") == "complaint"
    assert store.clear_suppression("a@agency.gov", allow_complaint=True) == "ok"
    assert store.suppressed() == {}


def test_suppression_is_shared_across_tenants(tmp_path):
    db = str(tmp_path / "t.db")
    a, b = Store(db, tenant_id="a"), Store(db, tenant_id="b")
    a.suppress_address("dead@agency.gov", "bounce")
    assert "dead@agency.gov" in b.suppressed(["DEAD@agency.gov"])
    a.close()
    b.close()


# ------------------------------------------------------------------- alerts

def test_a_bounced_address_is_skipped_and_the_log_says_why(monkeypatch, store):
    post = Post(monkeypatch)
    sid = flagged_list(store, ["dead@agency.gov", "fine@navy.mil"])
    store.suppress_address("dead@agency.gov", "bounce", "550 no such user", "Brevo")

    result = alerts.run_alerts(store, NoFirms(), Mailer(brevo()))

    assert [c["body"]["to"] for c in post.calls] == [[{"email": "fine@navy.mil"}]]
    assert result["emails"][0]["status"] == "sent"
    [logged] = store.alert_deliveries(sid)
    assert "dead@agency.gov bounced" in logged["detail"]
    assert "550 no such user" in logged["detail"]
    # Still on the list, so its owner can see it and correct it.
    assert "dead@agency.gov" in store.alert_subscription(sid)["emails"]


def test_a_list_of_only_dead_addresses_sends_nothing(monkeypatch, store):
    post = Post(monkeypatch)
    flagged_list(store, ["dead@agency.gov"])
    store.suppress_address("dead@agency.gov", "bounce")
    result = alerts.run_alerts(store, NoFirms(), Mailer(brevo()))
    assert result["emails"][0]["status"] == "nobody to send to"
    assert post.calls == []


# ---------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("MAIL_WEBHOOK_SECRET", BREVO_SECRET)
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", RESEND_SECRET)
    for name in ("DATABASE_URL", "REDIS_URL", "BREVO_API_KEY", "RESEND_API_KEY",
                 "SMTP_HOST", "ALERTS_FROM", "FOCI_EMAIL_REDIRECT_TO"):
        monkeypatch.delenv(name, raising=False)
    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


def test_resend_reports_reach_the_suppression_list(client):
    c, app_module = client
    body = resend_bounce()
    assert c.post("/hooks/mail/resend", content=body,
                  headers=svix_headers(body)).status_code == 204
    assert "dead@agency.gov" in app_module.store_for("acme").suppressed()


def test_a_forged_report_is_refused_and_changes_nothing(client):
    c, app_module = client
    body = resend_bounce()
    forged = svix_headers(body)
    forged["svix-signature"] = "v1,bm90LWEtcmVhbC1zaWduYXR1cmU="
    assert c.post("/hooks/mail/resend", content=body, headers=forged).status_code == 401
    assert c.post("/hooks/mail/brevo", json={"event": "hard_bounce",
                                             "email": "x@agency.gov"}).status_code == 401
    assert app_module.store_for("acme").suppressed() == {}


def test_brevo_reports_with_the_secret(client):
    c, app_module = client
    event = {"event": "hard_bounce", "email": "dead@agency.gov", "reason": "550"}
    assert c.post(f"/hooks/mail/brevo?token={BREVO_SECRET}", json=event).status_code == 204
    assert c.post("/hooks/mail/brevo", json={**event, "email": "two@agency.gov"},
                  headers={"Authorization": f"Bearer {BREVO_SECRET}"}).status_code == 204
    assert set(app_module.store_for("acme").suppressed()) == {"dead@agency.gov",
                                                             "two@agency.gov"}


def test_the_owner_sees_bounces_and_can_mail_again_but_not_past_a_complaint(client):
    c, app_module = client
    store = app_module.store_for("acme")
    flagged_list(store, ["dead@agency.gov", "angry@agency.gov", "fine@navy.mil"])
    store.suppress_address("dead@agency.gov", "bounce", "550 no such user")
    store.suppress_address("angry@agency.gov", "complaint")
    store.suppress_address("stranger@elsewhere.example", "bounce")

    d = c.get("/v1/alerts", headers=AUTH).json()
    assert d["bounce_reports"] == "resend"
    bounced = d["subscriptions"][0]["bounced"]
    assert [(b["email"], b["kind"]) for b in bounced] == [("angry@agency.gov", "complaint"),
                                                          ("dead@agency.gov", "bounce")]

    assert c.delete("/v1/alerts/bounces/dead@agency.gov", headers=AUTH).status_code == 204
    assert c.delete("/v1/alerts/bounces/angry@agency.gov", headers=AUTH).status_code == 409
    # Not on any of this tenant's lists: not theirs to clear.
    assert c.delete("/v1/alerts/bounces/stranger@elsewhere.example",
                    headers=AUTH).status_code == 404
    assert set(store.suppressed()) == {"angry@agency.gov", "stranger@elsewhere.example"}


# ---------------------------------------------------------------------- CLI

def test_the_operator_can_list_and_clear(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "cli.db")
    s = Store(db)
    s.suppress_address("angry@agency.gov", "complaint")
    s.close()

    class Cfg:
        dsn = db
        user_agent = "test/1.0 (ops@agency.gov)"

    monkeypatch.setattr(cli, "get_config", lambda: Cfg())
    assert cli.main(["mail-suppressions"]) == 0
    assert "angry@agency.gov marked an alert as spam" in capsys.readouterr().out
    assert cli.main(["mail-suppressions", "--clear", "angry@agency.gov"]) == 2
    assert cli.main(["mail-suppressions", "--clear", "angry@agency.gov",
                     "--complaint"]) == 0
