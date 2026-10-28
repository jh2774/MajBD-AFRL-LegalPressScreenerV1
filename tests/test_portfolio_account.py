"""What an account does for a portfolio and its email, on top of signing in
(test_accounts.py): an account keeps several portfolios and switches between
them, an alert list follows the account's portfolio wherever it is edited, the
portfolio can be emailed, and the person holding the admin key can make the
account that owns the site's own workspace.

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
CLIENT = pf.encode(pf.from_entity_keys("Client B", ["UEI3"]))
CLIENT2 = pf.encode(pf.from_entity_keys("Client B", ["UEI3", "UEI4"]))
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


def mine(c):
    return c.get("/v1/me/portfolios").json()


def add(c, key):
    return c.post("/v1/me/portfolios", headers=WEB, json={"portfolio_key": key})


# ------------------------------------------------------- creating an account

def test_the_page_is_told_whether_anyone_has_an_account(site, monkeypatch):
    """So the form can say the first account is made with the admin key."""
    Post(monkeypatch)
    c, _ = site(FOCI_SIGNUP="")
    assert c.get("/v1/auth/status").json()["accounts"] is False
    signup(c, Authorization="Bearer admin-key")
    assert c.get("/v1/auth/status").json()["accounts"] is True


def test_closed_sign_ups_say_how_to_get_in(site, monkeypatch):
    Post(monkeypatch)
    c, _ = site(FOCI_SIGNUP="")
    detail = signup(c).json()["detail"]
    assert "invite" in detail and "admin key" in detail


# ------------------------------------------------- several portfolios

def test_an_account_keeps_several_portfolios(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    signup(c)
    keep(c, ONE)
    first = mine(c)
    assert [p["name"] for p in first["portfolios"]] == ["Watch"]
    assert first["open"] == first["portfolios"][0]["portfolio_id"]
    watch = first["open"]

    account = add(c, CLIENT).json()["account"]
    assert account["portfolio_key"] == CLIENT
    listed = mine(c)
    assert len(listed["portfolios"]) == 2 and listed["open"] != watch
    sizes = {p["name"]: p["companies"] for p in listed["portfolios"]}
    assert sizes == {"Watch": 1, "Client B": 1}

    # Another computer finds both, and switches back to the first.
    laptop = TestClient(app_module.app)
    laptop.cookies.update(dict(c.cookies))
    r = laptop.post(f"/v1/me/portfolios/{watch}/open", headers=WEB)
    assert r.json()["account"]["portfolio_key"] == ONE
    assert r.json()["account"]["portfolio_id"] == watch
    # An edit changes the open one, and only it.
    keep(laptop, TWO)
    keys = {p["name"]: p["portfolio_key"] for p in mine(c)["portfolios"]}
    assert keys == {"Watch": TWO, "Client B": CLIENT}


def test_closing_a_portfolio_keeps_it_and_the_next_is_another(site, monkeypatch):
    Post(monkeypatch)
    c, _ = site()
    signup(c)
    keep(c, ONE)
    account = keep(c, "").json()["account"]
    assert (account["portfolio_key"], account["portfolio_id"]) == ("", "")
    assert len(mine(c)["portfolios"]) == 1, "closed, not deleted"
    keep(c, CLIENT)
    assert sorted(p["name"] for p in mine(c)["portfolios"]) == ["Client B", "Watch"]


def test_adding_one_already_there_opens_it(site, monkeypatch):
    """The same emailed link opened twice is one portfolio, not two."""
    Post(monkeypatch)
    c, _ = site()
    signup(c)
    add(c, ONE)
    add(c, CLIENT)
    assert add(c, ONE).json()["account"]["portfolio_key"] == ONE
    assert len(mine(c)["portfolios"]) == 2
    assert add(c, "not a key").status_code == 400


def test_removing_a_portfolio(site, monkeypatch):
    Post(monkeypatch)
    c, _ = site()
    signup(c)
    add(c, ONE)
    watch = mine(c)["open"]
    add(c, CLIENT)
    client = mine(c)["open"]
    assert c.delete(f"/v1/me/portfolios/{watch}", headers=WEB).status_code == 204
    assert [p["name"] for p in mine(c)["portfolios"]] == ["Client B"]
    assert c.get("/v1/me").json()["account"]["portfolio_key"] == CLIENT, \
        "the open one stays open"
    assert c.delete(f"/v1/me/portfolios/{client}", headers=WEB).status_code == 204
    account = c.get("/v1/me").json()["account"]
    assert (account["portfolio_key"], account["portfolio_id"]) == ("", "")
    assert c.delete(f"/v1/me/portfolios/{client}", headers=WEB).status_code == 404


def test_portfolios_are_their_accounts_own(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    signup(c)
    add(c, ONE)
    watch = mine(c)["open"]
    other = TestClient(app_module.app)
    signup(other, "other@concord.example")
    assert mine(other)["portfolios"] == []
    assert other.post(f"/v1/me/portfolios/{watch}/open", headers=WEB).status_code == 404
    assert other.delete(f"/v1/me/portfolios/{watch}", headers=WEB).status_code == 404
    assert len(mine(c)["portfolios"]) == 1
    assert c.delete(f"/v1/me/portfolios/{watch}").status_code == 403, \
        "not from the site's own pages"
    assert TestClient(app_module.app).get("/v1/me/portfolios").status_code == 401


def test_a_portfolio_saved_before_the_list_is_in_it(site, monkeypatch):
    """Accounts made before several portfolios existed had one, on the account."""
    Post(monkeypatch)
    c, app_module = site()
    signup(c)
    store = app_module.store_for("default")
    user = store.user_by_email(ME)
    store.set_user_portfolio(user["user_id"], ONE)
    listed = mine(c)
    assert [p["portfolio_key"] for p in listed["portfolios"]] == [ONE]
    assert listed["open"] == listed["portfolios"][0]["portfolio_id"]
    assert len(mine(c)["portfolios"]) == 1, "adopted once"


def test_each_portfolios_alert_list_stays_with_it(site, monkeypatch):
    """Editing one portfolio moves its own list, never another's."""
    Post(monkeypatch)
    c, app_module = site()
    signup(c)
    store = app_module.store_for(c.get("/v1/me").json()["account"]["workspace"])
    add(c, ONE)
    sid = c.post("/v1/alerts/subscriptions", headers=WEB, json={
        "name": "Watch", "emails": [ME], "portfolio_key": ONE}).json()["subscription"][
        "subscription_id"]
    add(c, CLIENT)
    keep(c, CLIENT2)
    assert store.alert_subscription(sid)["portfolio_key"] == ONE


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
