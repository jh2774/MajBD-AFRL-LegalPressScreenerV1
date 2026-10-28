"""What an account does for a portfolio and its email, on top of signing in
(test_accounts.py): an alert list follows the account's portfolio wherever it
is edited, the portfolio can be emailed, and the person holding the admin key
can make the account that owns the site's own workspace.

No test talks to a mail service: `requests.post` is replaced with a recorder.
"""
from __future__ import annotations

import importlib
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from test_mailer import Post
from test_notice_delivery import _notice

from foci_screen import portfolio as pf

WEB = {"X-Foci-Client": "web"}
PASSWORD = "correct horse battery"
ME = "analyst@concord.example"
ONE = pf.encode(pf.from_entity_keys("Watch", ["UEI1"]))
TWO = pf.encode(pf.from_entity_keys("Watch", ["UEI1", "UEI2"]))
ENV = ("DATABASE_URL", "REDIS_URL", "GMAIL_ENABLED", "FOCI_EMAIL_REDIRECT_TO", "BREVO_API_KEY",
       "MAIL_PROVIDER", "SMTP_HOST", "ALERTS_SEND", "ALERTS_REPLY_TO", "NOTICES_SEND",
       "FOCI_PUBLIC_URL", "VERCEL_PROJECT_PRODUCTION_URL")


@pytest.fixture()
def site(tmp_path, monkeypatch):
    def make(**env):
        base = {"FOCI_API_KEYS": "default:admin-key", "FOCI_DB": str(tmp_path / "p.db"),
                "FOCI_CACHE": str(tmp_path / "cache"), "FOCI_OUT": str(tmp_path / "out"),
                "FOCI_SIGNUP": "open", "FOCI_PUBLIC": "true",
                "RESEND_API_KEY": "re_not_real",
                "ALERTS_FROM": "alerts@contractscreener.com"}
        for name in ENV:
            monkeypatch.delenv(name, raising=False)
        for k, v in {**base, **env}.items():
            monkeypatch.setenv(k, v)
        from foci_screen.api import app as app_module
        importlib.reload(app_module)
        return TestClient(app_module.app), app_module
    return make


def signup(c, email=ME, **headers):
    return c.post("/v1/auth/signup", headers={**WEB, **headers},
                  json={"email": email, "password": PASSWORD})


def keep(c, key):
    return c.put("/v1/me/portfolio", headers=WEB, json={"portfolio_key": key})


# ----------------------------------------------- the site's own workspace

def test_the_admin_key_makes_the_owner_of_the_sites_workspace(site, monkeypatch):
    """On Vercel there is no command line for `add-user --workspace default`."""
    Post(monkeypatch)
    c, app_module = site(FOCI_SIGNUP="")
    assert signup(c).status_code == 403, "sign-ups are closed"
    r = signup(c, Authorization="Bearer admin-key")
    assert r.status_code == 201
    account = r.json()["account"]
    assert (account["workspace"], account["role"]) == ("default", "owner")
    assert signup(c, "other@concord.example", Authorization="Bearer wrong").status_code == 401


def test_a_signed_in_owner_of_the_sites_workspace_sees_its_data(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site(FOCI_SIGNUP="")
    _notice(app_module.store_for("default"))
    signup(c, Authorization="Bearer admin-key")
    assert len(c.get("/v1/notices").json()["notices"]) == 1
    assert c.get("/v1/notices").json()["can_decide"] is True


def test_approvals_carry_the_persons_address(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site(FOCI_SIGNUP="", NOTICES_SEND="true")
    store = app_module.store_for("default")
    notice_id = _notice(store)
    signup(c, Authorization="Bearer admin-key")
    r = c.post(f"/v1/notices/{notice_id}/approve", headers=WEB,
               json={"decided_by": "someone else"})
    assert r.json()["status"] == "sent"
    assert store.get_notice(notice_id)["decided_by"] == ME
    assert store.notice_sends([notice_id])[notice_id][0]["sent_by"] == ME


# -------------------------------------------- the alert list follows the portfolio

def test_an_alert_list_follows_the_accounts_portfolio(site, monkeypatch):
    """Edited on another computer, the browser that started the list never
    hears of it; the server moves the list along with the portfolio."""
    Post(monkeypatch)
    c, app_module = site()
    signup(c)
    tenant = c.get("/v1/me").json()["account"]["workspace"]
    store = app_module.store_for(tenant)
    keep(c, ONE)
    sid = c.post("/v1/alerts/subscriptions", headers=WEB, json={
        "name": "Watch", "emails": [ME], "portfolio_key": ONE}).json()["subscription"][
        "subscription_id"]

    store.start_run("DoD", {}, run_id="r1")
    old = store.create_notice(run_id="r1", entity_key="UEI2", entity_name="B", severity="high",
                              recipient="", officer_confidence="", subject="s", body_text="b")
    laptop = TestClient(app_module.app)
    laptop.cookies.update(dict(c.cookies))
    keep(laptop, TWO)
    sub = store.alert_subscription(sid)
    assert sub["companies"] == ["UEI1", "UEI2"] and sub["portfolio_key"] == TWO
    assert f"notice:{old}" in store.alert_already_sent(sid), "history is not news"


# ------------------------------------------------------------ emailing one

def test_emailing_your_portfolio_to_yourself(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site(FOCI_EMAIL_REDIRECT_TO="pilot@concord.example")
    signup(c)
    r = c.post("/v1/portfolio/email", headers=WEB,
               json={"key": ONE, "to": [ME], "note": "for Monday"}).json()
    assert r["status"] == "sent" and r["recipients"] == [ME]
    body = post.calls[-1]["body"]
    assert body["to"] == [ME], "your own copy is not redirected"
    assert body["subject"] == "Your portfolio: Watch"
    assert f"http://testserver/#/portfolio/open/{quote(ONE, safe='')}" in body["text"]
    assert ONE in body["text"] and "for Monday" in body["text"]
    assert "UEI1 — not screened on this site yet" in body["text"]


def test_emailing_a_portfolio_to_colleagues(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    signup(c)
    r = c.post("/v1/portfolio/email", headers=WEB,
               json={"key": ONE, "to": ["a@example.com, b@example.com"]}).json()
    assert r["status"] == "sent"
    sent = post.calls[-2:]
    assert [x["body"]["to"] for x in sent] == [["a@example.com"], ["b@example.com"]]
    assert sent[0]["body"]["subject"] == f"{ME} shared a portfolio: Watch"
    assert sent[0]["body"]["reply_to"] == ME, "replies reach the person who sent it"


def test_emailing_needs_a_sign_in_and_is_rationed(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    assert c.post("/v1/portfolio/email", headers=WEB,
                  json={"key": ONE, "to": [ME]}).status_code == 401
    signup(c)
    monkeypatch.setattr(app_module, "PORTFOLIO_EMAILS_PER_DAY", 3)
    assert c.post("/v1/portfolio/email", headers=WEB,
                  json={"key": ONE, "to": ["a@example.com, b@example.com"]}).status_code == 200
    assert c.post("/v1/portfolio/email", headers=WEB,
                  json={"key": ONE, "to": ["c@example.com, d@example.com"]}).status_code == 429
    assert c.post("/v1/portfolio/email", headers=WEB,
                  json={"key": ONE, "to": [f"x{n}@example.com" for n in range(11)]}
                  ).status_code == 422
    assert c.post("/v1/portfolio/email", json={"key": ONE, "to": [ME]}).status_code == 403, \
        "not from the site's own pages"


# ------------------------------------------------------------ editing one

def test_adding_a_company_keeps_the_portfolios_name(site):
    """An edit that does not name the portfolio used to rename it "Portfolio"."""
    c, _ = site()
    added = c.post("/v1/portfolio/edit", json={"key": ONE, "add": ["UEI9"]}).json()
    assert added["name"] == "Watch" and pf.decode(added["key"]).name == "Watch"
    assert c.post("/v1/portfolio/edit", json={"add": ["UEI9"]}).json()["name"] == "Portfolio"
