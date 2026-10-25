"""Anyone on an alert list can take themselves off it.

The people on a list are at other organisations, added by someone else. Until
this, the only way off was to ask whoever added them. Each alert now carries a
link of its own and the headers mail clients turn into an Unsubscribe button.
"""
from __future__ import annotations

import importlib
import re

import pytest
from fastapi.testclient import TestClient
from test_mailer import AUTH, KEY, FakeSMTP, NoFirms, Post, brevo, flagged_list, smtp

from foci_screen import alerts
from foci_screen import portfolio as pf
from foci_screen.notify.mailer import Mailer
from foci_screen.store import Store

SITE = "https://screen.example"


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "u.db"), tenant_id="acme")
    yield s
    s.close()


def _link(text: str) -> str:
    return re.search(r"https://screen\.example/u/[\w-]+", text).group(0)


# ------------------------------------------------------------------- mailer

def test_each_recipient_gets_their_own_body_and_headers(monkeypatch):
    post = Post(monkeypatch)
    d = Mailer(brevo()).send(
        ["a@agency.gov", "b@navy.mil"], "S", "shared", None,
        personalize=lambda addr: (f"for {addr}", None, {"X-For": addr}))
    assert d.status == "sent"
    assert [c["body"]["textContent"] for c in post.calls] == ["for a@agency.gov",
                                                              "for b@navy.mil"]
    assert post.calls[1]["body"]["headers"] == {"X-For": "b@navy.mil"}


def test_smtp_carries_the_headers_too(monkeypatch):
    FakeSMTP.sent, FakeSMTP.refuse = [], set()
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    Mailer(smtp()).send(["a@agency.gov"], "S", "B",
                        personalize=lambda addr: ("B2", None, {"List-Unsubscribe": "<u>"}))
    [msg] = FakeSMTP.sent
    assert msg["List-Unsubscribe"] == "<u>"
    assert msg.get_content().strip() == "B2"


# ------------------------------------------------------------------- alerts

def test_an_alert_carries_a_one_click_unsubscribe_for_its_reader(monkeypatch, store):
    post = Post(monkeypatch)
    flagged_list(store, ["a@agency.gov", "b@navy.mil"])

    alerts.run_alerts(store, NoFirms(), Mailer(brevo()), SITE)

    first, second = (c["body"] for c in post.calls)
    url_a, url_b = _link(first["textContent"]), _link(second["textContent"])
    assert url_a != url_b, "each link only works for the person it was sent to"
    assert first["headers"] == {"List-Unsubscribe": f"<{url_a}>",
                                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    assert f'href="{url_a}"' in first["htmlContent"]


def test_without_the_site_address_the_footer_says_who_to_ask(monkeypatch, store):
    post = Post(monkeypatch)
    flagged_list(store, ["a@agency.gov"])
    alerts.run_alerts(store, NoFirms(), Mailer(brevo()), "")
    body = post.calls[0]["body"]
    assert "ask whoever added you" in body["textContent"]
    assert "headers" not in body


def test_a_redirected_pilot_copy_carries_no_link(monkeypatch, store):
    """The pilot reader is not on the list; a link minted for them would
    unsubscribe nobody, and one for the intended reader would let the pilot
    unsubscribe them."""
    post = Post(monkeypatch)
    flagged_list(store, ["a@agency.gov"])
    alerts.run_alerts(store, NoFirms(),
                      Mailer(brevo(email_redirect_to="pilot@concord.example")), SITE)
    body = post.calls[0]["body"]
    assert "/u/" not in body["textContent"] and "headers" not in body


def test_an_unsubscribed_address_is_not_emailed(monkeypatch, store):
    post = Post(monkeypatch)
    sid = flagged_list(store, ["a@agency.gov", "b@navy.mil"])
    store.set_opted_out(store.unsubscribe_token(sid, "a@agency.gov"), True)

    alerts.run_alerts(store, NoFirms(), Mailer(brevo()), SITE)
    assert [c["body"]["to"] for c in post.calls] == [[{"email": "b@navy.mil"}]]


def test_a_stale_save_cannot_email_someone_who_left(monkeypatch, store):
    """A page opened before they unsubscribed still lists them; saving it puts
    the address back on the list, but the run leaves it out."""
    post = Post(monkeypatch)
    sid = flagged_list(store, ["a@agency.gov"])
    store.set_opted_out(store.unsubscribe_token(sid, "a@agency.gov"), True)
    sub = store.alert_subscription(sid)
    store.save_alert_subscription(subscription_id=sid, name=sub["name"],
                                  emails=["a@agency.gov"], companies=sub["companies"],
                                  portfolio_key=sub["portfolio_key"])

    result = alerts.run_alerts(store, NoFirms(), Mailer(brevo()), SITE)
    assert result["emails"][0]["status"] == "nobody to send to"
    assert post.calls == []


# -------------------------------------------------------------------- store

def test_the_token_is_stable_per_address(store):
    sid = flagged_list(store, ["a@agency.gov"])
    assert store.unsubscribe_token(sid, "a@agency.gov") == \
        store.unsubscribe_token(sid, "A@Agency.gov ")


def test_unsubscribing_and_undoing(store):
    sid = flagged_list(store, ["a@agency.gov", "b@navy.mil"])
    token = store.unsubscribe_token(sid, "a@agency.gov")

    store.set_opted_out(token, True)
    assert store.alert_subscription(sid)["emails"] == ["b@navy.mil"]
    assert "a@agency.gov" in store.opted_out(sid)

    store.set_opted_out(token, False)
    assert store.alert_subscription(sid)["emails"] == ["b@navy.mil", "a@agency.gov"]
    assert store.opted_out(sid) == {}


def test_undo_cannot_re_add_someone_the_owner_removed(store):
    sid = flagged_list(store, ["a@agency.gov", "b@navy.mil"])
    token = store.unsubscribe_token(sid, "a@agency.gov")
    sub = store.alert_subscription(sid)
    store.save_alert_subscription(subscription_id=sid, name=sub["name"],
                                  emails=["b@navy.mil"], companies=sub["companies"],
                                  portfolio_key=sub["portfolio_key"])

    store.set_opted_out(token, True)          # an old email's link
    store.set_opted_out(token, False)         # ... and "undo"
    assert store.alert_subscription(sid)["emails"] == ["b@navy.mil"]


def test_a_token_belongs_to_one_tenant(tmp_path):
    db = str(tmp_path / "t.db")
    acme, other = Store(db, tenant_id="acme"), Store(db, tenant_id="other")
    sid = flagged_list(acme, ["a@agency.gov"])
    token = acme.unsubscribe_token(sid, "a@agency.gov")
    assert other.set_opted_out(token, True) is None
    assert acme.alert_subscription(sid)["emails"] == ["a@agency.gov"]
    acme.close()
    other.close()


def test_deleting_a_list_retires_its_links(store):
    sid = flagged_list(store, ["a@agency.gov"])
    token = store.unsubscribe_token(sid, "a@agency.gov")
    store.delete_alert_subscription(sid)
    assert store.optout_by_token(token) is None


# ---------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    for name in ("DATABASE_URL", "REDIS_URL", "ALERTS_SEND", "BREVO_API_KEY",
                 "RESEND_API_KEY", "MAIL_PROVIDER", "SMTP_HOST", "SMTP_USER",
                 "ALERTS_FROM", "FOCI_EMAIL_REDIRECT_TO", "FOCI_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


def _token(app_module, email="a@agency.gov"):
    store = app_module.store_for("acme")
    sid = flagged_list(store, ["a@agency.gov", "b@navy.mil"])
    return store, sid, store.unsubscribe_token(sid, email)


def test_opening_the_link_asks_but_does_not_unsubscribe(client):
    """Mail scanners open every link in a message."""
    c, app_module = client
    store, sid, token = _token(app_module)

    page = c.get(f"/u/{token}")
    assert page.status_code == 200 and "Stop these alerts?" in page.text
    assert "a@agency.gov" in page.text and 'method="post"' in page.text
    assert store.opted_out(sid) == {}
    assert "a@agency.gov" in store.alert_subscription(sid)["emails"]


def test_one_click_unsubscribe_needs_no_key(client):
    c, app_module = client
    store, sid, token = _token(app_module)

    r = c.post(f"/u/{token}", content="List-Unsubscribe=One-Click",
               headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and "You are unsubscribed" in r.text
    assert store.alert_subscription(sid)["emails"] == ["b@navy.mil"]
    assert "Subscribe again" in c.get(f"/u/{token}").text

    undo = c.post(f"/u/{token}/undo")
    assert "Subscribed again" in undo.text
    assert "a@agency.gov" in store.alert_subscription(sid)["emails"]


def test_an_unknown_link_says_so(client):
    c, _ = client
    assert c.get("/u/not-a-token").status_code == 404
    assert c.post("/u/not-a-token").status_code == 404


def test_the_owner_sees_who_left_and_cannot_re_add_them(client):
    c, app_module = client
    store, sid, token = _token(app_module)
    c.post(f"/u/{token}")

    listed = c.get("/v1/alerts", headers=AUTH).json()["subscriptions"][0]
    assert [o["email"] for o in listed["opted_out"]] == ["a@agency.gov"]

    key = pf.encode(pf.from_entity_keys("Watch", ["UEI123"]))
    r = c.post("/v1/alerts/subscriptions", headers=AUTH, json={
        "subscription_id": sid, "name": "Watch", "portfolio_key": key,
        "emails": ["a@agency.gov, b@navy.mil, c@army.mil"]})
    assert r.status_code == 200
    assert r.json()["declined"] == ["a@agency.gov"]
    assert r.json()["subscription"]["emails"] == ["b@navy.mil", "c@army.mil"]


def test_the_test_email_carries_the_link(client, monkeypatch):
    c, app_module = client
    monkeypatch.setattr(app_module.cfg, "brevo_api_key", KEY)
    monkeypatch.setattr(app_module.cfg, "alerts_from", "alerts@concord.example")
    monkeypatch.setattr(app_module.cfg, "public_url", SITE)
    post = Post(monkeypatch)
    _, sid, _ = _token(app_module)

    r = c.post(f"/v1/alerts/subscriptions/{sid}/test", headers=AUTH)
    assert r.json()["status"] == "sent"
    body = post.calls[0]["body"]
    assert _link(body["textContent"]).startswith(f"{SITE}/u/")
    assert "List-Unsubscribe" in body["headers"]
