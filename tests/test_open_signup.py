"""Anyone may make an account on a public site, and use everything the site
does in a workspace of their own. What such an account has the site email is
held to people who asked for it: the site's name is on every email, and it
does not know them.

No test talks to a mail service: `requests.post` is replaced with a recorder.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_mailer import Post, flagged_list
from test_notice_delivery import _notice

from foci_screen import accounts
from foci_screen import portfolio as pf

WEB = {"X-Foci-Client": "web"}
PASSWORD = "correct horse battery"
ME = "someone@firm.example"
STRANGER = "stranger@elsewhere.example"
ONE = pf.encode(pf.from_entity_keys("Watch", ["UEI1"]))
ENV = ("DATABASE_URL", "REDIS_URL", "GMAIL_ENABLED", "FOCI_EMAIL_REDIRECT_TO", "BREVO_API_KEY",
       "MAIL_PROVIDER", "SMTP_HOST", "ALERTS_SEND", "ALERTS_REPLY_TO", "NOTICES_SEND",
       "FOCI_PUBLIC_URL", "VERCEL_PROJECT_PRODUCTION_URL", "FOCI_SIGNUP", "CRON_SECRET")


@pytest.fixture()
def site(tmp_path, monkeypatch):
    def make(**env):
        base = {"FOCI_API_KEYS": "default:admin-key", "FOCI_DB": str(tmp_path / "o.db"),
                "FOCI_CACHE": str(tmp_path / "cache"), "FOCI_OUT": str(tmp_path / "out"),
                "FOCI_PUBLIC": "true", "RESEND_API_KEY": "re_not_real",
                "ALERTS_FROM": "alerts@contractscreener.com", "NOTICES_SEND": "true",
                "FOCI_SCREEN_TIMES": "any"}
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


def confirm(app_module, email=ME):
    store = app_module.store_for("default")
    store.mark_email_verified(store.user_by_email(email)["user_id"])


def sent_to(post, address):
    return [call["body"] for call in post.calls if address in call["body"]["to"]]


# ------------------------------------------------------------ making one

def test_anyone_may_make_an_account_on_a_public_site(site, monkeypatch):
    Post(monkeypatch)
    c, _ = site()
    assert c.get("/v1/auth/status").json()["signup"] == "open"
    r = signup(c)
    assert r.status_code == 201
    account = r.json()["account"]
    assert account["workspace"] != "default", "a workspace of their own"
    assert account["role"] == "owner" and account["vouched"] is False
    # Everything the site does, in that workspace — deciding notices included.
    assert c.get("/v1/notices").json()["can_decide"] is True


def test_a_site_can_still_close_sign_ups(site, monkeypatch):
    Post(monkeypatch)
    c, _ = site(FOCI_SIGNUP="closed")
    assert c.get("/v1/auth/status").json()["signup"] == "closed"
    assert signup(c).status_code == 403
    c, _ = site(FOCI_PUBLIC="")
    assert signup(c).status_code == 403, "a site that needs a key stays closed by default"


def test_the_site_takes_so_many_new_accounts_a_day(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    monkeypatch.setattr(accounts, "SIGNUPS_PER_DAY", 1)
    assert signup(c).status_code == 201
    assert signup(TestClient(app_module.app), "second@firm.example").status_code == 429
    owner = signup(TestClient(app_module.app), "owner@concord.example",
                   Authorization="Bearer admin-key")
    assert owner.status_code == 201, "not the site's own people"
    assert owner.json()["account"]["vouched"] is True


def test_confirmation_emails_are_rationed(site, monkeypatch):
    """Otherwise anyone could sign up with someone else's address and have the
    link sent to it over and over."""
    Post(monkeypatch)
    c, _ = site()
    monkeypatch.setattr(accounts, "RESEND_SECONDS", 0)
    signup(c)
    assert c.post("/v1/me/verify", headers=WEB).status_code == 200
    assert c.post("/v1/me/verify", headers=WEB).status_code == 200
    assert c.post("/v1/me/verify", headers=WEB).status_code == 429


# ------------------------------------------------------- what it may send

def test_addresses_a_self_made_account_adds_are_asked_first(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = site()
    workspace = signup(c).json()["account"]["workspace"]
    confirm(app_module)
    r = c.post("/v1/alerts/subscriptions", headers=WEB, json={
        "name": "Watch", "emails": [ME, STRANGER], "portfolio_key": ONE}).json()
    sid = r["subscription"]["subscription_id"]
    rows = app_module.store_for(workspace).alert_recipients(sid)
    assert rows[ME]["status"] == "confirmed", "their own, confirmed address"
    assert rows[STRANGER]["status"] == "pending"
    asked = [b["subject"] for b in sent_to(post, STRANGER)]
    assert asked == ['Confirm: email alerts for "Watch"'], "asked, and nothing else"

    test = c.post(f"/v1/alerts/subscriptions/{sid}/test", headers=WEB).json()
    assert test["recipients"] == [ME]
    assert c.post(f"/v1/alerts/subscriptions/{sid}/test", headers=WEB).status_code == 429


def test_the_sites_own_people_are_trusted_as_before(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    signup(c, "owner@concord.example", Authorization="Bearer admin-key")
    r = c.post("/v1/alerts/subscriptions", headers=WEB, json={
        "name": "Watch", "emails": ["colleague@concord.example"], "portfolio_key": ONE}).json()
    rows = app_module.store_for("default").alert_recipients(
        r["subscription"]["subscription_id"])
    assert rows["colleague@concord.example"]["status"] == "confirmed"


def test_a_notice_from_a_self_made_workspace_reaches_no_officer(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = site()
    workspace = signup(c).json()["account"]["workspace"]
    confirm(app_module)
    store = app_module.store_for(workspace)
    nid = _notice(store)
    assert c.get("/v1/notices").json()["sending"]["mode"] == "own_mailbox"

    r = c.post(f"/v1/notices/{nid}/approve", headers=WEB,
               json={"send_to": "both", "copy_to": STRANGER})
    assert r.status_code == 403
    assert store.get_notice(nid)["status"] == "pending", "a refusal decides nothing"

    r = c.post(f"/v1/notices/{nid}/approve", headers=WEB,
               json={"send_to": "both", "copy_to": ME}).json()
    assert r["delivery"]["outcome"] == "rendered"
    assert "own mailbox" in r["delivery"]["detail"]
    assert r["copy"]["outcome"] == "sent"
    assert not sent_to(post, "ko@mail.mil")
    assert len(sent_to(post, ME)) == 2, "the confirmation link, and the copy"


def test_a_self_made_account_emails_portfolios_within_its_organisation(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    workspace = signup(c).json()["account"]["workspace"]

    def send(to):
        return c.post("/v1/portfolio/email", headers=WEB, json={"key": ONE, "to": to})

    assert send([ME]).status_code == 403, "not before the address is confirmed"
    confirm(app_module)
    assert send([STRANGER]).status_code == 403
    app_module.store_for("default").create_user(
        email="colleague@firm.example", password_hash="x", tenant_id=workspace,
        role="member", verified=True)
    r = send([ME, "colleague@firm.example"])
    assert r.status_code == 200 and r.json()["status"] == "sent"


def test_a_self_made_organisation_invites_once_confirmed_and_not_endlessly(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    signup(c)

    def invite(email):
        return c.post("/v1/org/invites", headers=WEB, json={"email": email, "role": "member"})

    assert invite("a@firm.example").status_code == 403
    confirm(app_module)
    monkeypatch.setattr(accounts, "INVITES_PER_DAY", 1)
    assert invite("a@firm.example").status_code == 201
    assert invite("b@firm.example").status_code == 429


# --------------------------------------------------------- the daily runs

def test_the_daily_alert_check_covers_every_workspace(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    workspace = signup(c).json()["account"]["workspace"]
    stores = [app_module.store_for(t) for t in ("default", workspace)]
    for store in stores:
        store.save_alert_subscription(subscription_id="", name="Watch",
                                      emails=["x@firm.example"], companies=["UEI1"],
                                      portfolio_key=ONE, active=True)
    anyone = TestClient(app_module.app)
    assert anyone.post("/v1/schedule/alerts").json()["left"] == 1
    last = anyone.post("/v1/schedule/alerts").json()
    assert (last["status"], last["left"]) == ("checked", 0)
    assert anyone.post("/v1/schedule/alerts").json()["status"] == "nothing due"
    assert all(s["last_run_at"] for store in stores for s in store.alert_subscriptions())


def test_the_daily_watchlist_sweep_covers_every_workspace(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    workspace = signup(c).json()["account"]["workspace"]
    started = []
    monkeypatch.setattr(app_module.queue, "enqueue",
                        lambda tenant, run_id, options, **kw:
                        started.append((tenant, options["watchlist_id"])))
    made = {(t, app_module.store_for(t).create_watchlist("W", {"agency": "Department of X"}))
            for t in ("default", workspace)}
    anyone = TestClient(app_module.app)
    assert anyone.post("/v1/schedule/watchlists").json() == {"status": "queued", "left": 1}
    assert anyone.post("/v1/schedule/watchlists").json() == {"status": "queued", "left": 0}
    assert anyone.post("/v1/schedule/watchlists").json()["status"] == "nothing due"
    assert set(started) == made


# ------------------------------------------------- Vercel's daily job, too

def test_vercel_asks_for_the_daily_check_once_a_day(site):
    _, app_module = site()
    crons = json.loads((Path(__file__).resolve().parent.parent / "vercel.json")
                       .read_text(encoding="utf-8"))["crons"]
    assert [c["schedule"].split()[2:] for c in crons] == [["*", "*", "*"]], "daily"
    gets = {r.path for r in app_module.app.routes if "GET" in getattr(r, "methods", ())}
    assert [c["path"] for c in crons] == ["/v1/schedule/daily"]
    assert "/v1/schedule/daily" in gets, "Vercel's scheduler sends a GET"


def test_the_daily_check_emails_what_is_new_and_nothing_twice(site, monkeypatch):
    """GitHub's workflow, Vercel's job, and someone opening the Portfolio page
    may all ask on the same day. Only the first finds anything to send."""
    post = Post(monkeypatch)
    c, app_module = site()
    flagged_list(app_module.store_for("default"), ["reader@firm.example"])
    anyone = TestClient(app_module.app)

    first = anyone.get("/v1/schedule/daily").json()
    assert first == {"status": "checked", "workspaces": 1, "emails": 1, "left": 0}
    assert len(sent_to(post, "reader@firm.example")) == 1

    assert anyone.post("/v1/schedule/alerts").json()["status"] == "nothing due"
    assert anyone.get("/v1/schedule/daily").json()["emails"] == 0

    # Due again — as if a day had passed — the check runs, and finds nothing new.
    monkeypatch.setattr(app_module, "SWEEP_ALERTS_HOURS", -1)
    again = anyone.get("/v1/schedule/daily").json()
    assert (again["status"], again["emails"]) == ("checked", 0)
    assert len(sent_to(post, "reader@firm.example")) == 1, "never the same item twice"


def test_with_cron_secret_set_only_vercel_gets_in(site, monkeypatch):
    Post(monkeypatch)
    c, _ = site(CRON_SECRET="test-cron-secret")
    assert c.get("/v1/schedule/daily").status_code == 401
    assert c.get("/v1/schedule/daily",
                 headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/v1/schedule/daily",
                 headers={"Authorization": "Bearer test-cron-secret"}).status_code == 200


def test_a_site_that_needs_a_key_needs_one_for_the_daily_check(site, monkeypatch):
    Post(monkeypatch)
    c, _ = site(FOCI_PUBLIC="")
    assert c.get("/v1/schedule/daily").status_code == 401
    assert c.get("/v1/schedule/daily",
                 headers={"Authorization": "Bearer admin-key"}).status_code == 200
