"""Getting an alert to the addresses typed into a portfolio.

No test here talks to a mail service. `requests.post` and `smtplib.SMTP` are
replaced with recorders, so what is checked is what would be sent and what is
done with each kind of reply — including the replies that matter in practice:
an address the service refuses, a key it does not accept, and a host that
blocks the port.
"""
from __future__ import annotations

import importlib
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from foci_screen import alerts
from foci_screen import portfolio as pf
from foci_screen.notify.mailer import BREVO_URL, RESEND_URL, Mailer
from foci_screen.store import Store

AUTH = {"X-API-Key": "secret-key"}
KEY = "xkeysib-not-a-real-key"


class Cfg:
    """The settings as they are when nothing has been set."""
    alerts_send = None
    brevo_api_key = ""
    resend_api_key = ""
    mail_provider = ""
    smtp_host = ""
    smtp_port = 587
    smtp_user = ""
    smtp_password = ""
    smtp_security = "starttls"
    alerts_from = ""
    email_redirect_to = ""
    managed_host = ""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def brevo(**kw):
    return Cfg(brevo_api_key=KEY, alerts_from="alerts@concord.example", **kw)


class Reply:
    def __init__(self, status=201, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class Post:
    """Stands in for requests.post; answers per recipient."""

    def __init__(self, monkeypatch, replies=None):
        self.calls, self.replies = [], replies or {}
        monkeypatch.setattr("requests.post", self)

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "body": json})
        to = json["to"][0]
        address = to["email"] if isinstance(to, dict) else to
        return self.replies.get(address, Reply(201 if url == BREVO_URL else 200, {"id": "1"}))


# ------------------------------------------------------- what turns sending on

def test_nothing_is_sent_until_a_mail_service_is_connected():
    mailer = Mailer(Cfg())
    assert mailer.status()["mode"] == "draft"
    assert "BREVO_API_KEY" in mailer.status()["explanation"]
    d = mailer.send(["ko@agency.gov"], "s", "t")
    assert d.status == "drafted" and "No mail service" in d.detail


def test_connecting_a_service_is_what_turns_sending_on(monkeypatch):
    post = Post(monkeypatch)
    mailer = Mailer(brevo())
    status = mailer.status()
    assert status["mode"] == "send" and status["provider"] == "Brevo"
    assert "alerts@concord.example through Brevo" in status["explanation"]
    assert mailer.send(["ko@agency.gov"], "Subject", "Body").status == "sent"
    assert len(post.calls) == 1


def test_alerts_send_false_holds_everything_as_a_draft(monkeypatch):
    post = Post(monkeypatch)
    mailer = Mailer(brevo(alerts_send=False))
    assert mailer.status()["mode"] == "draft"
    assert mailer.send(["ko@agency.gov"], "s", "t").status == "drafted"
    assert post.calls == []


def test_a_half_connected_service_says_what_is_missing(monkeypatch):
    post = Post(monkeypatch)
    mailer = Mailer(Cfg(brevo_api_key=KEY))                 # no sender address
    status = mailer.status()
    assert status["mode"] == "misconfigured" and status["missing"] == ["ALERTS_FROM"]
    d = mailer.send(["ko@agency.gov"], "s", "t")
    assert d.status == "failed" and "ALERTS_FROM" in d.detail, "failed, so it is retried"
    assert post.calls == []

    insisted = Mailer(Cfg(alerts_send=True))                # told to send, nothing to send with
    assert insisted.status()["mode"] == "misconfigured"
    assert insisted.send(["ko@agency.gov"], "s", "t").status == "failed"


# ------------------------------------------------------------------ over HTTPS

def test_brevo_gets_one_message_per_recipient(monkeypatch):
    """People on a list are at different organisations; none of them is shown
    the others' addresses."""
    post = Post(monkeypatch)
    d = Mailer(brevo()).send(["ko@agency.gov", "KO2@navy.mil ", "ko@agency.gov"],
                             "Subject", "Plain body", "<p>HTML body</p>")
    assert d.status == "sent" and d.recipients == ["ko@agency.gov", "KO2@navy.mil"]

    assert [c["url"] for c in post.calls] == [BREVO_URL, BREVO_URL]
    first = post.calls[0]
    assert first["headers"]["api-key"] == KEY
    assert first["body"] == {
        "sender": {"name": "FOCI-Screener", "email": "alerts@concord.example"},
        "to": [{"email": "ko@agency.gov"}], "subject": "Subject",
        "textContent": "Plain body", "htmlContent": "<p>HTML body</p>"}
    assert post.calls[1]["body"]["to"] == [{"email": "KO2@navy.mil"}]


def test_resend_is_spoken_to_in_its_own_shape(monkeypatch):
    post = Post(monkeypatch)
    cfg = Cfg(resend_api_key="re_not_real", alerts_from="alerts@concord.example")
    assert Mailer(cfg).send(["ko@agency.gov"], "Subject", "Body").status == "sent"
    [call] = post.calls
    assert call["url"] == RESEND_URL
    assert call["headers"]["Authorization"] == "Bearer re_not_real"
    assert call["body"] == {"from": "FOCI-Screener <alerts@concord.example>",
                            "to": ["ko@agency.gov"], "subject": "Subject", "text": "Body"}


def test_one_refused_address_does_not_stop_the_rest(monkeypatch):
    Post(monkeypatch, {"typo@agency.gvo": Reply(400, {"code": "invalid_parameter",
                                                      "message": "email is not valid"})})
    d = Mailer(brevo()).send(["ko@agency.gov", "typo@agency.gvo"], "s", "t")
    assert d.status == "partial"
    assert "Delivered to 1 of 2" in d.detail
    assert "typo@agency.gvo" in d.detail and "email is not valid" in d.detail


def test_a_key_that_is_not_accepted_fails_with_the_services_own_words(monkeypatch, caplog):
    """Brevo's reply to a request from an address it has not seen says exactly
    what to do about it; that reply is what the page shows."""
    reason = ("We have detected you are using an unrecognised IP address 203.0.113.9. "
              "If you performed this action make sure to add the new IP address in "
              "this link: https://app.brevo.com/security/authorised_ips")
    refused = Reply(401, {"code": "unauthorized", "message": reason})
    Post(monkeypatch, {"ko@agency.gov": refused, "ko2@navy.mil": refused})
    with caplog.at_level(logging.WARNING):
        d = Mailer(brevo()).send(["ko@agency.gov", "ko2@navy.mil"], "s", "t")
    assert d.status == "failed" and "unrecognised IP address" in d.detail
    assert KEY not in d.detail and KEY not in caplog.text, "the key is never repeated"


def test_a_service_that_cannot_be_reached_is_a_failure_not_a_crash(monkeypatch):
    import requests

    def down(*a, **kw):
        raise requests.ConnectionError(f"could not connect with key {KEY}")

    monkeypatch.setattr("requests.post", down)
    d = Mailer(brevo()).send(["ko@agency.gov"], "s", "t")
    assert d.status == "failed" and "could not reach the mail service" in d.detail
    assert KEY not in d.detail


def test_the_pilot_redirect_applies_to_every_route(monkeypatch):
    post = Post(monkeypatch)
    mailer = Mailer(brevo(email_redirect_to="me@concord.example"))
    assert mailer.status()["mode"] == "redirect"
    d = mailer.send(["ko@agency.gov", "ko2@navy.mil"], "Alert", "Body")
    assert d.recipients == ["me@concord.example"] and len(post.calls) == 1
    assert post.calls[0]["body"]["subject"] == "[for ko@agency.gov, ko2@navy.mil] Alert"


# ------------------------------------------------------------------------ SMTP

class FakeSMTP:
    sent: list = []
    refuse: set = set()

    def __init__(self, host, port, timeout=30):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        import smtplib
        if message["To"] in self.refuse:
            raise smtplib.SMTPRecipientsRefused({message["To"]: (550, b"no such user")})
        FakeSMTP.sent.append(message)


def smtp(**kw):
    return Cfg(smtp_host="smtp.example", smtp_user="me@example.com", smtp_password="pw", **kw)


def test_smtp_also_sends_one_message_each(monkeypatch):
    FakeSMTP.sent, FakeSMTP.refuse = [], {"gone@agency.gov"}
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    d = Mailer(smtp()).send(["ko@agency.gov", "gone@agency.gov", "ko2@navy.mil"], "S", "B")
    assert d.status == "partial" and "gone@agency.gov" in d.detail
    assert [m["To"] for m in FakeSMTP.sent] == ["ko@agency.gov", "ko2@navy.mil"]


def test_a_blocked_port_is_named_for_what_it_is(monkeypatch):
    """Render's free plan cannot reach ports 25, 465 or 587. The connection
    simply times out, which says nothing unless the tool does."""
    def blocked(host, port, timeout=30):
        raise TimeoutError("timed out")

    monkeypatch.setattr("smtplib.SMTP", blocked)
    cfg = smtp(managed_host="Render")
    assert "Render's free plan blocks port 587" in Mailer(cfg).status()["explanation"]
    d = Mailer(cfg).send(["ko@agency.gov"], "s", "t")
    assert d.status == "failed" and "port 587 is blocked on Render's free plan" in d.detail

    assert "blocks port" not in Mailer(smtp(managed_host="Render", smtp_port=2525)
                                       ).status()["explanation"]


# ------------------------------------------------- what a run does with each

@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "mail.db"), tenant_id="acme")
    yield s
    s.close()


class NoFirms:
    def refresh(self, crd, previous=None):
        raise AssertionError("no investment firms in these portfolios")


def flagged_list(store, emails):
    key = pf.encode(pf.from_entity_keys("Watch", ["UEI123"]))
    sid = store.save_alert_subscription(name="Watch", emails=emails, companies=["UEI123"],
                                        portfolio_key=key)
    store.start_run("DoD", {}, run_id="r1")
    store.create_notice(run_id="r1", entity_key="UEI123", entity_name="ACME DYNAMICS LLC",
                        severity="high", recipient="x@mail.mil", officer_confidence="high",
                        subject="s", body_text="b", trigger_reason="Always notify.")
    return sid


def test_a_partly_delivered_alert_is_not_sent_again(monkeypatch, store):
    post = Post(monkeypatch, {"typo@agency.gvo": Reply(400, {"message": "email is not valid"})})
    sid = flagged_list(store, ["ko@agency.gov", "typo@agency.gvo"])

    first = alerts.run_alerts(store, NoFirms(), Mailer(brevo()))
    assert first["emails"][0]["status"] == "partial"
    [logged] = store.alert_deliveries(sid)
    assert "typo@agency.gvo" in logged["detail"], "the bad address is on record to be fixed"

    second = alerts.run_alerts(store, NoFirms(), Mailer(brevo()))
    assert second["emails"][0]["status"] == "nothing new"
    assert len(post.calls) == 2, "the person it reached is not emailed twice"


def test_an_alert_nobody_received_is_tried_again(monkeypatch, store):
    refused = Reply(401, {"message": "Key not found"})
    Post(monkeypatch, {"ko@agency.gov": refused})
    sid = flagged_list(store, ["ko@agency.gov"])

    def run():
        return alerts.run_alerts(store, NoFirms(), Mailer(brevo()))["emails"][0]["status"]

    assert run() == "failed"
    Post(monkeypatch)                                       # the key is corrected
    assert run() == "sent"
    assert sorted(d["status"] for d in store.alert_deliveries(sid)) == ["failed", "sent"]


def test_a_list_is_due_when_it_has_gone_most_of_a_day_unchecked(store):
    assert alerts.is_due(store) is False, "no lists, nothing due"
    sid = flagged_list(store, ["ko@agency.gov"])
    assert alerts.is_due(store) is True, "never checked"

    store.touch_alert_subscription(sid)
    assert alerts.is_due(store) is False

    stale = (datetime.now(timezone.utc) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with store._tx() as c:
        c.execute("UPDATE alert_subscriptions SET last_run_at=? WHERE subscription_id=?",
                  (stale, sid))
    assert alerts.is_due(store) is True


# ----------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    for name in ("DATABASE_URL", "REDIS_URL", "ALERTS_SEND", "BREVO_API_KEY", "RESEND_API_KEY",
                 "MAIL_PROVIDER", "SMTP_HOST", "SMTP_USER", "ALERTS_FROM",
                 "FOCI_EMAIL_REDIRECT_TO"):
        monkeypatch.delenv(name, raising=False)

    def make(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from foci_screen.api import app as app_module
        importlib.reload(app_module)
        return TestClient(app_module.app), app_module

    return make


def test_health_says_whether_alerts_leave_without_naming_anyone(client):
    c, _ = client()
    assert c.get("/health").json()["alerts"] == {"mode": "draft", "provider": ""}

    c, _ = client(BREVO_API_KEY=KEY, ALERTS_FROM="alerts@concord.example")
    body = c.get("/health")
    assert body.json()["alerts"] == {"mode": "send", "provider": "Brevo"}
    assert KEY not in body.text and "concord.example" not in body.text


def test_a_half_connected_mail_service_raises_a_banner(client):
    c, _ = client(BREVO_API_KEY=KEY)
    warnings = c.get("/health").json()["warnings"]
    assert any("ALERTS_FROM" in w for w in warnings)


def test_opening_the_portfolio_checks_only_when_a_check_is_due(client):
    c, app_module = client()
    store = app_module.store_for("acme")
    assert c.post("/v1/alerts/run?only_if_due=true", headers=AUTH).json()["status"] == "not due"

    sid = flagged_list(store, ["ko@agency.gov"])
    ran = c.post("/v1/alerts/run?only_if_due=true", headers=AUTH).json()
    assert ran["status"] == "checked" and ran["emails"][0]["status"] == "drafted"
    assert store.alert_subscription(sid)["last_run_at"]

    again = c.post("/v1/alerts/run?only_if_due=true", headers=AUTH).json()
    assert again["status"] == "not due"


def test_two_checks_cannot_run_at_once(client):
    c, app_module = client()
    assert app_module._alerts_running.acquire(blocking=False)
    try:
        busy = c.post("/v1/alerts/run", headers=AUTH).json()
        assert busy["status"] == "already running" and busy["emails"] == []
    finally:
        app_module._alerts_running.release()
    assert c.post("/v1/alerts/run", headers=AUTH).json()["status"] == "checked"
