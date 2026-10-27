"""A site anyone can open (FOCI_PUBLIC), and email only to people who agreed.

Three things are checked. Using the site needs no key, while the few actions
that remove work, change screening for everyone or email an official still
need one. An address a visitor types in gets one request to confirm and
nothing else until it does; every alert after that carries that address's
own link to stop. And visitors never see other people's addresses in full.

No test talks to a mail service: `requests.post` is replaced with a recorder.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from foci_screen import alerts, consent
from foci_screen import portfolio as pf
from foci_screen.config import Config
from foci_screen.notify.mailer import BREVO_URL, RESEND_URL, Mailer

ADMIN = {"Authorization": "Bearer admin-key"}
SITE = "https://contractscreener.com"
MAIL_ENV = ("ALERTS_SEND", "BREVO_API_KEY", "RESEND_API_KEY", "MAIL_PROVIDER", "SMTP_HOST",
            "SMTP_USER", "ALERTS_FROM", "ALERTS_REPLY_TO", "FOCI_EMAIL_REDIRECT_TO",
            "FOCI_PUBLIC_URL", "VERCEL", "VERCEL_PROJECT_PRODUCTION_URL", "DATABASE_URL",
            "REDIS_URL", "FOCI_API_KEYS", "FOCI_PUBLIC", "ALERTS_PAUSED")


class Reply:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class Post:
    """Stands in for requests.post and keeps what each message said."""

    def __init__(self, monkeypatch, replies=None):
        self.calls, self.replies = [], list(replies or [])
        monkeypatch.setattr("requests.post", self)

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "body": json})
        if self.replies:
            return self.replies.pop(0)
        return Reply(201 if url == BREVO_URL else 200, {"id": "1"})

    def to(self, address):
        return [c["body"] for c in self.calls if c["body"]["to"] == [address]]


@pytest.fixture()
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "public.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    for name in MAIL_ENV:
        monkeypatch.delenv(name, raising=False)
    # Set, empty, so a developer's own .env cannot supply a key.
    monkeypatch.setenv("FOCI_API_KEYS", "")

    def make(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from foci_screen.api import app as app_module
        importlib.reload(app_module)
        return TestClient(app_module.app), app_module

    return make


def open_site(site, **env):
    """Public, with an admin key, sending through Resend, at its own domain."""
    return site(FOCI_PUBLIC="true", FOCI_API_KEYS="default:admin-key",
                RESEND_API_KEY="re_not_real", ALERTS_FROM="alerts@contractscreener.com",
                VERCEL_PROJECT_PRODUCTION_URL="contractscreener.com", **env)


PORTFOLIO = pf.encode(pf.from_entity_keys("Watch", ["UEI123"]))


def flag(store):
    """Something worth an alert about the portfolio's one contractor."""
    store.start_run("DoD", {}, run_id="r1")
    store.create_notice(run_id="r1", entity_key="UEI123", entity_name="ACME DYNAMICS LLC",
                        severity="high", recipient="x@mail.mil", officer_confidence="high",
                        subject="s", body_text="b", trigger_reason="Always notify.")


class NoFirms:
    def refresh(self, crd, previous=None):
        raise AssertionError("no investment firms in this portfolio")


def save(c, emails, headers=None, subscription_id=""):
    return c.post("/v1/alerts/subscriptions", headers=headers or {},
                  json={"subscription_id": subscription_id, "name": "Watch",
                        "emails": emails, "portfolio_key": PORTFOLIO})


def token_of(store, sid, email):
    return store.alert_recipients(sid)[email]["token"]


# ------------------------------------------------------------ no key to use

def test_a_public_site_needs_no_key(site):
    c, _ = site(FOCI_PUBLIC="true")
    for path in ("/v1/overview", "/v1/screens", "/v1/alerts", "/v1/policy"):
        assert c.get(path).status_code == 200, path
    health = c.get("/health").json()
    assert health["public"] is True
    assert not any("FOCI_API_KEYS" in w for w in health["warnings"]), \
        "no key is not a fault on a site meant to be open"


def test_a_key_left_in_a_browser_does_not_lock_anyone_out(site):
    c, _ = site(FOCI_PUBLIC="true", FOCI_API_KEYS="default:admin-key")
    assert c.get("/v1/screens", headers={"X-API-Key": "an-old-key"}).status_code == 200


def test_without_public_mode_the_site_stays_closed(site):
    c, _ = site()
    assert c.get("/v1/overview").status_code == 503
    assert c.get("/health").json()["public"] is False


# ------------------------------------------------------- the admin key's part

ADMIN_ONLY = [
    ("delete", "/v1/screens/r1?confirm=true", None),
    ("put", "/v1/policy", {}),
    ("delete", "/v1/policy", None),
    ("put", "/v1/rules/R1", {"enabled": False}),
    ("delete", "/v1/rules/R1", None),
    ("put", "/v1/watchlists/w1/policy", {}),
    ("post", "/v1/notices/n1/approve", {"reviewer": "x"}),
    ("post", "/v1/notices/n1/reject", {"reviewer": "x"}),
    ("post", "/v1/notices/n1/deliver", None),
    ("delete", "/v1/alerts/subscriptions/s1", None),
    ("delete", "/v1/alerts/bounces/ko@agency.gov", None),
    ("get", "/v1/system/check", None),
]


def call(c, method, path, body, headers=None):
    kw = {"headers": headers or {}}
    if body is not None:
        kw["json"] = body
    return getattr(c, method)(path, **kw)


def test_with_no_admin_key_configured_those_actions_are_closed(site):
    c, _ = site(FOCI_PUBLIC="true")
    for method, path, body in ADMIN_ONLY:
        r = call(c, method, path, body)
        assert r.status_code == 403, (method, path)
        assert "has none" in r.json()["detail"]


def test_those_actions_need_the_admin_key(site):
    c, _ = site(FOCI_PUBLIC="true", FOCI_API_KEYS="default:admin-key")
    for method, path, body in ADMIN_ONLY:
        refused = call(c, method, path, body)
        assert refused.status_code == 401, (method, path)
        assert "admin key" in refused.json()["detail"]
        wrong = call(c, method, path, body, {"X-API-Key": "guess"})
        assert wrong.status_code == 401, (method, path)

    assert call(c, "put", "/v1/policy", {}, ADMIN).status_code == 200
    assert call(c, "delete", "/v1/screens/nope", None, ADMIN).status_code == 404, \
        "past the key: there is simply no such screen"


# --------------------------------------------------- addresses must agree

def test_an_address_a_visitor_adds_is_asked_and_sent_nothing_else(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = open_site(site)
    store = app_module.store_for("default")

    r = save(c, ["ko@agency.gov"]).json()
    flag(store)
    assert r["consent"]["asked"] == ["k•••@agency.gov"]
    [request] = post.to("ko@agency.gov")
    assert request["subject"] == 'Confirm: email alerts for "Watch"'
    sid = r["subscription"]["subscription_id"]
    token = token_of(store, sid, "ko@agency.gov")
    assert f"{SITE}/alerts/confirm/{token}" in request["text"]
    assert "contractscreener.com" in request["text"]

    shown = c.get("/v1/alerts").json()
    [listed] = shown["subscriptions"]
    assert listed["emails"] == ["k•••@agency.gov"] and shown["can_edit_addresses"] is False
    assert listed["recipients"] == [{"email": "k•••@agency.gov", "status": "pending"}]
    assert "ko@agency.gov" not in c.get("/v1/alerts").text

    ran = alerts.run_alerts(store, NoFirms(), Mailer(app_module.cfg), SITE)
    assert ran["emails"][0]["status"] == "waiting for confirmation"
    assert len(post.calls) == 1, "nothing but the request to confirm"
    assert store.alert_deliveries(sid) == []


def test_confirming_takes_a_press_not_a_glance(site, monkeypatch):
    """Mail security scanners open every link in a message. Opening the link
    shows a button; only pressing it confirms."""
    Post(monkeypatch)
    c, app_module = open_site(site)
    store = app_module.store_for("default")
    sid = save(c, ["ko@agency.gov"]).json()["subscription"]["subscription_id"]
    token = token_of(store, sid, "ko@agency.gov")

    page = c.get(f"/alerts/confirm/{token}")
    assert page.status_code == 200 and "Confirm alerts" in page.text and "<form" in page.text
    assert store.alert_recipients(sid)["ko@agency.gov"]["status"] == "pending"

    done = c.post(f"/alerts/confirm/{token}")
    assert done.status_code == 200 and "Confirmed" in done.text
    assert store.alert_recipients(sid)["ko@agency.gov"]["status"] == "confirmed"
    assert "Already confirmed" in c.get(f"/alerts/confirm/{token}").text

    assert c.get("/alerts/confirm/not-a-token").status_code == 404


def test_every_alert_carries_its_recipients_own_way_to_stop(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = open_site(site)
    store = app_module.store_for("default")
    sid = save(c, ["ko@agency.gov"]).json()["subscription"]["subscription_id"]
    flag(store)
    token = token_of(store, sid, "ko@agency.gov")
    c.post(f"/alerts/confirm/{token}")

    ran = alerts.run_alerts(store, NoFirms(), Mailer(app_module.cfg), SITE)
    assert ran["emails"][0]["status"] == "sent"
    alert = post.to("ko@agency.gov")[-1]
    stop = f"{SITE}/u/{token}"
    assert f"unsubscribe here: {stop}" in alert["text"], "one token: confirm and stop"
    assert f'href="{stop}"' in alert["html"]
    assert alert["headers"] == {"List-Unsubscribe": f"<{stop}>",
                                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    [logged] = store.alert_deliveries(sid)
    assert token not in logged["body_text"], "the log is shown on the page; the link is not"

    # What a mail program's "Unsubscribe" button sends.
    stopped = c.post(f"/u/{token}", content="List-Unsubscribe=One-Click",
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert stopped.status_code == 200 and "You are unsubscribed" in stopped.text
    assert store.alert_subscription(sid)["emails"] == []
    assert store.alert_recipients(sid)["ko@agency.gov"]["status"] == "unsubscribed"
    assert "You are unsubscribed" in c.get(f"/alerts/confirm/{token}").text, \
        "confirming again does not undo leaving"


def test_the_first_unsubscribe_links_still_work(site, monkeypatch):
    """Alerts sent before the links moved to /u/ point at /alerts/stop/."""
    Post(monkeypatch)
    c, app_module = open_site(site)
    store = app_module.store_for("default")
    sid = save(c, ["ko@agency.gov"], ADMIN).json()["subscription"]["subscription_id"]
    token = token_of(store, sid, "ko@agency.gov")
    assert "Stop these alerts?" in c.get(f"/alerts/stop/{token}").text
    assert "You are unsubscribed" in c.post(f"/alerts/stop/{token}").text
    assert store.opted_out(sid)


def test_someone_who_left_is_not_put_back_by_anyone_else(site, monkeypatch):
    """Not by a visitor, and not by the admin: leaving was that person's
    decision. They are declined by name, and nothing is sent to them."""
    post = Post(monkeypatch)
    c, app_module = open_site(site)
    store = app_module.store_for("default")
    sid = save(c, ["ko@agency.gov", "b@navy.mil"], ADMIN).json()["subscription"][
        "subscription_id"]
    token = token_of(store, sid, "ko@agency.gov")
    c.post(f"/u/{token}")

    r = save(c, ["ko@agency.gov"], subscription_id=sid).json()
    assert r["declined"] == ["k•••@agency.gov"]
    r = save(c, ["ko@agency.gov", "b@navy.mil"], ADMIN, subscription_id=sid).json()
    assert r["declined"] == ["ko@agency.gov"]
    assert store.alert_subscription(sid)["emails"] == ["b@navy.mil"]
    assert post.calls == []

    listed = c.get("/v1/alerts").json()["subscriptions"][0]
    assert [o["email"] for o in listed["opted_out"]] == ["k•••@agency.gov"]

    # They rejoin from their own link, which also confirms them.
    assert "Subscribed again" in c.post(f"/u/{token}/undo").text
    assert store.alert_recipients(sid)["ko@agency.gov"]["status"] == "confirmed"
    assert "ko@agency.gov" in store.alert_subscription(sid)["emails"]


def test_addresses_the_admin_adds_are_trusted(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = open_site(site)
    store = app_module.store_for("default")
    r = save(c, ["ko@agency.gov"], ADMIN).json()
    sid = r["subscription"]["subscription_id"]
    assert store.alert_recipients(sid)["ko@agency.gov"]["status"] == "confirmed"
    assert post.calls == [] and r["consent"]["asked"] == []
    assert r["subscription"]["emails"] == ["ko@agency.gov"], "shown in full with the key"


def test_lists_made_before_consent_was_recorded_keep_working(site, monkeypatch):
    """Their addresses were put there by someone holding a key."""
    post = Post(monkeypatch)
    _, app_module = open_site(site)
    store = app_module.store_for("default")
    store.save_alert_subscription(name="Watch", emails=["ko@agency.gov"],
                                  companies=["UEI123"], portfolio_key=PORTFOLIO)
    flag(store)
    ran = alerts.run_alerts(store, NoFirms(), Mailer(app_module.cfg), SITE)
    assert ran["emails"][0]["status"] == "sent"
    assert f"{SITE}/u/" in post.to("ko@agency.gov")[0]["text"]


def test_a_visitor_adds_to_a_list_and_takes_nobody_off(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = open_site(site)
    store = app_module.store_for("default")
    sid = save(c, ["ko@agency.gov"], ADMIN).json()["subscription"]["subscription_id"]

    r = save(c, ["new@navy.mil"], subscription_id=sid)
    assert r.status_code == 200
    assert store.alert_subscription(sid)["emails"] == ["ko@agency.gov", "new@navy.mil"]

    # The page sends no addresses when it only follows a changed portfolio.
    assert save(c, [], subscription_id=sid).status_code == 200
    assert len(store.alert_subscription(sid)["emails"]) == 2
    assert save(c, []).status_code == 400, "a new list needs someone on it"


def test_requests_to_confirm_are_rationed(site, monkeypatch):
    post = Post(monkeypatch)
    monkeypatch.setattr(consent, "ASKS_PER_DAY", 2)
    c, app_module = open_site(site)
    store = app_module.store_for("default")
    sid = save(c, ["a@agency.gov", "b@agency.gov"]).json()["subscription"]["subscription_id"]
    assert len(post.calls) == 2

    r = save(c, ["c@agency.gov"], subscription_id=sid).json()
    assert r["consent"]["held"] == ["c•••@agency.gov"], "the site's day's worth is sent"
    assert len(post.calls) == 2
    assert store.alert_recipients(sid)["c@agency.gov"]["status"] == "pending"


def test_an_address_gets_one_request_a_day_however_many_lists_name_it(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = open_site(site)
    save(c, ["ko@agency.gov"])
    for _ in range(3):
        assert save(c, ["ko@agency.gov"]).json()["consent"]["waiting"] == ["k•••@agency.gov"]
    assert len(post.to("ko@agency.gov")) == 1


def test_an_address_is_asked_at_most_once_a_day(site, monkeypatch):
    Post(monkeypatch)
    _, app_module = open_site(site)
    store = app_module.store_for("default")
    sid = store.save_alert_subscription(name="Watch", emails=["ko@agency.gov"],
                                        companies=["UEI123"], portfolio_key=PORTFOLIO)
    sub = store.alert_subscription(sid)
    row = store.add_alert_recipient(sid, "ko@agency.gov", consent.PENDING)
    mailer = Mailer(app_module.cfg)
    assert consent.ask(store, mailer, sub, row, SITE)[0] == "asked"
    row = store.alert_recipients(sid)["ko@agency.gov"]
    assert consent.ask(store, mailer, sub, row, SITE)[0] == "waiting"


def test_without_a_mail_service_requests_wait_for_the_next_save(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = site(FOCI_PUBLIC="true")
    store = app_module.store_for("default")
    r = save(c, ["ko@agency.gov"]).json()
    assert r["consent"]["not_sent"][0]["email"] == "k•••@agency.gov"
    sid = r["subscription"]["subscription_id"]
    assert store.alert_recipients(sid)["ko@agency.gov"]["asked_at"] is None

    monkeypatch.setenv("RESEND_API_KEY", "re_not_real")
    monkeypatch.setenv("ALERTS_FROM", "alerts@contractscreener.com")
    from foci_screen.api import app as reloaded
    importlib.reload(reloaded)
    again = TestClient(reloaded.app)
    assert save(again, [], subscription_id=sid).json()["consent"]["asked"] == \
        ["k•••@agency.gov"]
    assert len(post.to("ko@agency.gov")) == 1


# ------------------------------------------------------- visitors, rationed

def test_check_now_and_test_emails_wait_between_goes(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = open_site(site)
    sid = save(c, ["ko@agency.gov"], ADMIN).json()["subscription"]["subscription_id"]

    assert c.post("/v1/alerts/run").json()["status"] == "checked"
    again = c.post("/v1/alerts/run").json()
    assert again["status"] == "recently checked" and "10 minutes" in again["detail"]
    assert c.post("/v1/alerts/run", headers=ADMIN).json()["status"] == "checked"

    first = c.post(f"/v1/alerts/subscriptions/{sid}/test")
    assert first.json()["status"] == "sent"
    assert first.json()["recipients"] == ["k•••@agency.gov"]
    assert c.post(f"/v1/alerts/subscriptions/{sid}/test").status_code == 429
    assert c.post(f"/v1/alerts/subscriptions/{sid}/test", headers=ADMIN).status_code == 200


def test_a_test_email_goes_only_to_those_who_confirmed(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = open_site(site)
    sid = save(c, ["ko@agency.gov"]).json()["subscription"]["subscription_id"]
    r = c.post(f"/v1/alerts/subscriptions/{sid}/test").json()
    assert r["status"] == "waiting" and "confirmed" in r["detail"]
    [only] = post.calls
    assert only["body"]["subject"].startswith("Confirm"), "the request to confirm, nothing more"


def test_masking():
    assert consent.mask("julian.higgins@gmail.com") == "j•••@gmail.com"
    assert consent.mask_text("Delivered to 1 of 2. Not delivered: ko@navy.mil (refused)") \
        == "Delivered to 1 of 2. Not delivered: k•••@navy.mil (refused)"


# ------------------------------------------------------------- Resend, and links

class Cfg:
    alerts_send = None
    brevo_api_key = ""
    resend_api_key = ""
    mail_provider = ""
    smtp_host = ""
    smtp_port = 587
    smtp_user = ""
    smtp_password = ""
    smtp_security = "starttls"
    alerts_from = "alerts@contractscreener.com"
    alerts_reply_to = ""
    email_redirect_to = ""
    managed_host = "Vercel"

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_resend_is_preferred_when_more_than_one_service_is_set(monkeypatch):
    post = Post(monkeypatch)
    mailer = Mailer(Cfg(resend_api_key="re_not_real", brevo_api_key="xkeysib-not-real"))
    assert mailer.transport == "resend"
    mailer.send(["ko@agency.gov"], "s", "t")
    assert post.calls[0]["url"] == RESEND_URL
    assert Mailer(Cfg(resend_api_key="re", brevo_api_key="x", mail_provider="brevo")
                  ).transport == "brevo"


def test_replies_can_go_to_a_person(monkeypatch):
    post = Post(monkeypatch)
    Mailer(Cfg(resend_api_key="re_not_real", alerts_reply_to="julian@concord.example")
           ).send(["ko@agency.gov"], "s", "t")
    assert post.calls[0]["body"]["reply_to"] == "julian@concord.example"


def test_asked_to_slow_down_it_waits_and_tries_once_more(monkeypatch):
    waited = []
    monkeypatch.setattr("time.sleep", waited.append)
    post = Post(monkeypatch, [Reply(429, {"message": "Too many requests"},
                                    {"retry-after": "2"}), Reply(200, {"id": "1"})])
    d = Mailer(Cfg(resend_api_key="re_not_real")).send(["ko@agency.gov"], "s", "t")
    assert d.status == "sent" and len(post.calls) == 2 and waited == [2.0]

    Post(monkeypatch, [Reply(429, {"message": "daily_quota_exceeded"})] * 2)
    d = Mailer(Cfg(resend_api_key="re_not_real")).send(["ko@agency.gov"], "s", "t")
    assert d.status == "failed" and "daily_quota_exceeded" in d.detail


def test_links_point_at_the_production_domain_vercel_reports(monkeypatch):
    monkeypatch.delenv("FOCI_PUBLIC_URL", raising=False)
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "contractscreener.com")
    assert Config().public_url == SITE
    monkeypatch.setenv("FOCI_PUBLIC_URL", "https://www.contractscreener.com")
    assert Config().public_url == "https://www.contractscreener.com"
    monkeypatch.delenv("FOCI_PUBLIC_URL")
    monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL")
    assert Config().public_url == ""


def test_the_daily_check_needs_only_the_sites_address():
    workflow = (Path(__file__).resolve().parent.parent / ".github" / "workflows"
                / "alerts.yml").read_text(encoding="utf-8")
    assert 'if [ -z "$SITE" ]; then' in workflow
    assert '[ -z "$KEY" ]' not in workflow, "a key is sent only where one is set"
