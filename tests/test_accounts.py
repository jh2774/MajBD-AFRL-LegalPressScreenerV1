"""Accounts: portfolios kept by the address you sign in with, and emailed.

FOCI_SIGN_IN lets people other than the admins sign in. Signing in that way
gives no admin powers: it keeps your portfolios on the server, so they open on
any browser and survive the end of a session; ties an alert list to the
portfolio it is for, so the list follows edits made anywhere; trusts your own
address on an alert list; and lets you email a portfolio.

No test talks to a mail service: `requests.post` is replaced with a recorder.
"""
from __future__ import annotations

import importlib
import re
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from test_mailer import Post

from foci_screen import portfolio as pf

ADMIN = "julian@concord.example"
MEMBER = "analyst@concord.example"
SITE_HEADER = {"X-FOCI-Session": "1"}
ENV = ("DATABASE_URL", "REDIS_URL", "GMAIL_ENABLED", "FOCI_EMAIL_REDIRECT_TO", "BREVO_API_KEY",
       "RESEND_API_KEY", "MAIL_PROVIDER", "SMTP_HOST", "ALERTS_FROM", "ALERTS_SEND",
       "ALERTS_REPLY_TO", "NOTICES_SEND", "FOCI_PUBLIC", "FOCI_ADMIN_EMAILS", "FOCI_SIGN_IN",
       "FOCI_PUBLIC_URL", "VERCEL_PROJECT_PRODUCTION_URL")

ONE = pf.encode(pf.from_entity_keys("Watch", ["UEI1"]))
TWO = pf.encode(pf.from_entity_keys("Watch", ["UEI1", "UEI2"]))


@pytest.fixture()
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "a.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FOCI_API_KEYS", "")

    def make(**env):
        env = {"FOCI_PUBLIC": "true", "FOCI_ADMIN_EMAILS": ADMIN, "FOCI_SIGN_IN": "anyone",
               "RESEND_API_KEY": "re_not_real", "ALERTS_FROM": "alerts@contractscreener.com",
               **env}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from foci_screen.api import app as app_module
        importlib.reload(app_module)
        return TestClient(app_module.app), app_module

    return make


def sign_in(c, post, email=MEMBER):
    c.post("/v1/auth/sign-in", json={"email": email})
    path = re.search(r"http://testserver(/auth/sign-in/[\w-]+)",
                     post.calls[-1]["body"]["text"]).group(1)
    assert c.post(path, follow_redirects=False).status_code == 303


def keep(c, key=ONE, **body):
    return c.post("/v1/me/portfolios", headers=SITE_HEADER, json={"key": key, **body})


# ------------------------------------------------------------ who may sign in

def test_a_member_is_signed_in_but_not_an_admin(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    sign_in(c, post)
    assert c.get("/v1/auth").json() == {"sign_in": True, "members": "anyone",
                                        "email": MEMBER, "admin": False}
    r = c.put("/v1/policy", json={}, headers=SITE_HEADER)
    assert r.status_code == 403 and "admins" in r.json()["detail"]
    assert c.get("/v1/alerts").json()["can_edit_addresses"] is False


def test_sign_in_can_be_limited_to_domains(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site(FOCI_SIGN_IN="concord.example, @navy.mil")
    for address in ("a@concord.example", "b@navy.mil", "c@elsewhere.com"):
        c.post("/v1/auth/sign-in", json={"email": address})
    assert [x["body"]["to"][0] for x in post.calls] == ["a@concord.example", "b@navy.mil"]
    assert c.get("/v1/auth").json()["members"] == "@concord.example, @navy.mil"


def test_by_default_only_admins_sign_in(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site(FOCI_SIGN_IN="")
    c.post("/v1/auth/sign-in", json={"email": MEMBER})
    c.post("/v1/auth/sign-in", json={"email": ADMIN})
    assert [x["body"]["to"][0] for x in post.calls] == [ADMIN]


def test_links_to_members_are_rationed_across_the_site(site, monkeypatch):
    from foci_screen.api import signin
    post = Post(monkeypatch)
    monkeypatch.setattr(signin, "MEMBER_LINKS_PER_DAY", 2)
    c, _ = site()
    for n in range(4):
        c.post("/v1/auth/sign-in", json={"email": f"person{n}@example.com"})
    c.post("/v1/auth/sign-in", json={"email": ADMIN})
    assert [x["body"]["to"][0] for x in post.calls] == \
        ["person0@example.com", "person1@example.com", ADMIN], "admins are never held back"


def test_on_a_closed_site_a_member_may_use_it_but_not_run_it(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site(FOCI_PUBLIC="")
    sign_in(c, post)
    assert c.get("/v1/overview").status_code == 200
    assert c.delete("/v1/screens/x?confirm=true", headers=SITE_HEADER).status_code == 403


# ------------------------------------------------------------ kept portfolios

def test_portfolios_are_kept_by_address_and_survive_the_session(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    assert keep(c).status_code == 401, "not signed in"
    sign_in(c, post)
    pid = keep(c).json()["portfolio"]["portfolio_id"]
    assert keep(c, TWO, portfolio_id=pid).json()["portfolio"]["companies"] == 2

    # A new instance of the site — as on Vercel, and as on another day.
    cookies = dict(c.cookies)
    later, _ = site()
    later.cookies.update(cookies)
    [p] = later.get("/v1/me/portfolios").json()["portfolios"]
    assert (p["portfolio_id"], p["portfolio_key"], p["companies"]) == (pid, TWO, 2)


def test_one_persons_portfolios_are_not_anothers(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = site()
    sign_in(c, post)
    pid = keep(c).json()["portfolio"]["portfolio_id"]

    other = TestClient(app_module.app)
    sign_in(other, post, "someone@example.com")
    assert other.get("/v1/me/portfolios").json()["portfolios"] == []
    assert keep(other, TWO, portfolio_id=pid).status_code == 404
    assert other.delete(f"/v1/me/portfolios/{pid}", headers=SITE_HEADER).status_code == 404
    assert c.delete(f"/v1/me/portfolios/{pid}", headers=SITE_HEADER).status_code == 204


def test_an_alert_list_follows_its_portfolio(site, monkeypatch):
    """Edited on any browser, the list watches the companies the portfolio
    now holds — and the added company's history is not sent as news."""
    post = Post(monkeypatch)
    c, app_module = site()
    sign_in(c, post)
    store = app_module.store_for("default")
    pid = keep(c).json()["portfolio"]["portfolio_id"]
    sub = c.post("/v1/alerts/subscriptions", headers=SITE_HEADER, json={
        "name": "Watch", "emails": [MEMBER], "portfolio_key": ONE,
        "portfolio_id": pid}).json()["subscription"]
    sid = sub["subscription_id"]
    assert store.saved_portfolio(pid, MEMBER)["subscription_id"] == sid

    store.start_run("DoD", {}, run_id="r1")
    old = store.create_notice(run_id="r1", entity_key="UEI2", entity_name="B", severity="high",
                              recipient="", officer_confidence="", subject="s", body_text="b")
    keep(c, TWO, portfolio_id=pid)
    assert store.alert_subscription(sid)["companies"] == ["UEI1", "UEI2"]
    assert f"notice:{old}" in store.alert_already_sent(sid)
    assert [p["subscription_id"] for p in c.get("/v1/me/portfolios").json()["portfolios"]] \
        == [sid]


def test_your_own_address_is_not_asked_to_confirm(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = site()
    sign_in(c, post)
    r = c.post("/v1/alerts/subscriptions", headers=SITE_HEADER, json={
        "name": "Watch", "emails": [f"{MEMBER}, colleague@example.com"],
        "portfolio_key": ONE}).json()
    sid = r["subscription"]["subscription_id"]
    statuses = {k: v["status"] for k, v in
                app_module.store_for("default").alert_recipients(sid).items()}
    assert statuses == {MEMBER: "confirmed", "colleague@example.com": "pending"}
    assert post.calls[-1]["body"]["to"] == ["colleague@example.com"]
    [listed] = c.get("/v1/alerts").json()["subscriptions"]
    assert listed["emails"] == [MEMBER, "c•••@example.com"], "your own, in full"


def test_adding_a_company_keeps_the_portfolios_name(site):
    """An edit that does not name the portfolio used to rename it "Portfolio"."""
    c, _ = site()
    added = c.post("/v1/portfolio/edit", json={"key": ONE, "add": ["UEI9"]}).json()
    assert added["name"] == "Watch"
    assert pf.decode(added["key"]).name == "Watch"
    fresh = c.post("/v1/portfolio/edit", json={"add": ["UEI9"]}).json()
    assert fresh["name"] == "Portfolio"


# ------------------------------------------------------------ emailing one

def test_emailing_a_portfolio_to_yourself(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site(FOCI_EMAIL_REDIRECT_TO="pilot@concord.example")
    sign_in(c, post)
    r = c.post("/v1/portfolio/email", headers=SITE_HEADER,
               json={"key": ONE, "to": [MEMBER], "note": "for Monday"}).json()
    assert r["status"] == "sent" and r["recipients"] == [MEMBER]
    body = post.calls[-1]["body"]
    assert body["to"] == [MEMBER], "your own copy is not redirected"
    assert body["subject"] == "Your portfolio: Watch"
    assert f"http://testserver/#/portfolio/open/{quote(ONE, safe='')}" in body["text"]
    assert ONE in body["text"] and "for Monday" in body["text"]
    assert "UEI1 — not screened on this site yet" in body["text"]


def test_emailing_a_portfolio_to_colleagues(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    sign_in(c, post)
    r = c.post("/v1/portfolio/email", headers=SITE_HEADER,
               json={"key": ONE, "to": ["a@example.com, b@example.com"]}).json()
    assert r["status"] == "sent"
    sent = post.calls[-2:]
    assert [x["body"]["to"] for x in sent] == [["a@example.com"], ["b@example.com"]]
    assert sent[0]["body"]["subject"] == f"{MEMBER} shared a portfolio: Watch"
    assert sent[0]["body"]["reply_to"] == MEMBER, "replies reach the person who sent it"


def test_emailing_needs_a_sign_in_and_is_rationed(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = site()
    assert c.post("/v1/portfolio/email", headers=SITE_HEADER,
                  json={"key": ONE, "to": [MEMBER]}).status_code == 401
    sign_in(c, post)
    monkeypatch.setattr(app_module, "PORTFOLIO_EMAILS_PER_DAY", 3)
    ok = c.post("/v1/portfolio/email", headers=SITE_HEADER,
                json={"key": ONE, "to": ["a@example.com, b@example.com"]})
    assert ok.status_code == 200
    over = c.post("/v1/portfolio/email", headers=SITE_HEADER,
                  json={"key": ONE, "to": ["c@example.com, d@example.com"]})
    assert over.status_code == 429
    assert c.post("/v1/portfolio/email", headers=SITE_HEADER,
                  json={"key": ONE, "to": [f"x{n}@example.com" for n in range(11)]}
                  ).status_code == 422


def test_without_sign_in_there_are_no_accounts(site):
    c, _ = site(FOCI_ADMIN_EMAILS="", FOCI_SIGN_IN="")
    assert c.get("/v1/me/portfolios").status_code == 404
