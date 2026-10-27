"""Signing in by email, for the people who look after the site.

A sign-in does what the admin key does, and records the person: approvals
used to say "api-key:default", which names nobody. The link is emailed, works
once and briefly, and the form says nothing about who may sign in. The
session cookie counts on a change only alongside a header the site's own
pages send, which a form on another site cannot.
"""
from __future__ import annotations

import importlib
import re

import pytest
from fastapi.testclient import TestClient
from test_mailer import Post
from test_notice_delivery import _notice

ADMIN = "julian@concord.example"
OTHER = "someone@example.com"
SITE_HEADER = {"X-FOCI-Session": "1"}
ENV = ("DATABASE_URL", "REDIS_URL", "GMAIL_ENABLED", "FOCI_EMAIL_REDIRECT_TO", "BREVO_API_KEY",
       "RESEND_API_KEY", "MAIL_PROVIDER", "SMTP_HOST", "ALERTS_FROM", "ALERTS_SEND",
       "NOTICES_SEND", "FOCI_PUBLIC", "FOCI_ADMIN_EMAILS", "FOCI_PUBLIC_URL",
       "VERCEL_PROJECT_PRODUCTION_URL")


@pytest.fixture()
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FOCI_API_KEYS", "")        # not a developer's own .env

    def make(**env):
        env = {"FOCI_PUBLIC": "true", "FOCI_ADMIN_EMAILS": ADMIN,
               "RESEND_API_KEY": "re_not_real", "ALERTS_FROM": "alerts@contractscreener.com",
               **env}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from foci_screen.api import app as app_module
        importlib.reload(app_module)
        return TestClient(app_module.app), app_module

    return make


def link_in(post) -> str:
    text = post.calls[-1]["body"]["text"]
    return re.search(r"http://testserver(/auth/sign-in/[\w-]+)", text).group(1)


def sign_in(c, post) -> None:
    assert c.post("/v1/auth/sign-in", json={"email": ADMIN}).status_code == 200
    r = c.post(link_in(post), follow_redirects=False)
    assert r.status_code == 303


# -------------------------------------------------------------- the link

def test_without_a_list_there_is_no_sign_in(site):
    c, _ = site(FOCI_ADMIN_EMAILS="")
    assert c.get("/v1/auth").json() == {"sign_in": False, "email": ""}
    assert c.post("/v1/auth/sign-in", json={"email": ADMIN}).status_code == 404
    assert c.get("/health").json()["sign_in"] is False


def test_only_a_listed_address_is_sent_a_link_and_the_answer_does_not_say(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    listed = c.post("/v1/auth/sign-in", json={"email": " Julian@Concord.example "}).json()
    unlisted = c.post("/v1/auth/sign-in", json={"email": OTHER}).json()
    assert listed == unlisted, "the form must not reveal who may sign in"
    assert [x["body"]["to"] for x in post.calls] == [[ADMIN]]


def test_a_link_is_not_redirected_or_held(site, monkeypatch):
    """The pilot redirect and ALERTS_SEND=false keep mail from other people;
    a sign-in link goes to the person who asked for it."""
    post = Post(monkeypatch)
    c, _ = site(FOCI_EMAIL_REDIRECT_TO="pilot@concord.example", ALERTS_SEND="false")
    c.post("/v1/auth/sign-in", json={"email": ADMIN})
    assert post.calls[0]["body"]["to"] == [ADMIN]


def test_links_are_rationed(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    for _ in range(8):
        c.post("/v1/auth/sign-in", json={"email": ADMIN})
    assert len(post.calls) == 5


def test_opening_the_link_asks_and_it_works_once(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    c.post("/v1/auth/sign-in", json={"email": ADMIN})
    path = link_in(post)

    page = c.get(path)
    assert page.status_code == 200 and 'method="post"' in page.text
    assert c.get("/v1/auth").json()["email"] == "", "opening it signed nobody in"

    r = c.post(path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/#/"
    cookie = r.headers["set-cookie"].lower()
    assert "foci_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert c.get("/v1/auth").json()["email"] == ADMIN

    assert c.post(path, follow_redirects=False).status_code == 404, "once only"
    assert c.get(path).status_code == 404


def test_an_expired_link_does_not_work(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = site()
    c.post("/v1/auth/sign-in", json={"email": ADMIN})
    store = app_module.store_for("default")
    with store._tx() as cur:
        cur.execute("UPDATE sign_in_links SET expires_at='2000-01-01T00:00:00Z'")
    assert c.post(link_in(post), follow_redirects=False).status_code == 404


def test_the_cookie_is_secure_behind_https(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    c.post("/v1/auth/sign-in", json={"email": ADMIN})
    r = c.post(link_in(post), follow_redirects=False,
               headers={"X-Forwarded-Proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()


# ------------------------------------------------------------ the session

def test_a_session_does_what_the_admin_key_does(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    assert c.put("/v1/policy", json={}, headers=SITE_HEADER).status_code == 401, \
        "no key and not signed in"
    sign_in(c, post)
    assert c.put("/v1/policy", json={}, headers=SITE_HEADER).status_code == 200
    assert c.get("/v1/alerts").json()["can_edit_addresses"] is True


def test_a_cookie_alone_cannot_change_anything(site, monkeypatch):
    """What a form on another site would send: the cookie, without the header."""
    post = Post(monkeypatch)
    c, _ = site()
    sign_in(c, post)
    assert c.put("/v1/policy", json={}).status_code == 401
    assert c.get("/v1/auth").json()["email"] == ADMIN, "reading needs no header"


def test_approvals_and_sends_carry_the_persons_name(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = site(NOTICES_SEND="true")
    sign_in(c, post)
    store = app_module.store_for("default")
    notice_id = _notice(store)

    r = c.post(f"/v1/notices/{notice_id}/approve", headers=SITE_HEADER,
               json={"decided_by": "someone else"})
    assert r.json()["status"] == "sent"
    assert store.get_notice(notice_id)["decided_by"] == ADMIN
    assert store.notice_sends([notice_id])[notice_id][0]["sent_by"] == ADMIN


def test_taking_someone_off_the_list_ends_their_session(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    sign_in(c, post)
    cookies = dict(c.cookies)

    c2, _ = site(FOCI_ADMIN_EMAILS="someone-else@concord.example")
    c2.cookies.update(cookies)
    assert c2.get("/v1/auth").json()["email"] == ""
    assert c2.put("/v1/policy", json={}, headers=SITE_HEADER).status_code == 401


def test_signing_out(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    sign_in(c, post)
    secret = c.cookies.get("foci_session")
    assert c.post("/v1/auth/sign-out", headers=SITE_HEADER).status_code == 204
    c.cookies.set("foci_session", secret)       # an old copy of the cookie
    assert c.get("/v1/auth").json()["email"] == "", "the session itself has ended"


def test_a_closed_site_can_be_used_by_signing_in_alone(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site(FOCI_PUBLIC="")
    r = c.get("/v1/overview")
    assert r.status_code == 401 and "Sign in" in r.json()["detail"], \
        "closed to visitors, not unconfigured"
    sign_in(c, post)
    assert c.get("/v1/overview").status_code == 200
    assert not any("FOCI_API_KEYS" in w for w in c.get("/health").json()["warnings"])


def test_sign_in_without_a_mail_service_is_flagged(site):
    c, _ = site(RESEND_API_KEY="")
    assert c.post("/v1/auth/sign-in", json={"email": ADMIN}).status_code == 503
    assert any("Sign-in is set up" in w for w in c.get("/health").json()["warnings"])
