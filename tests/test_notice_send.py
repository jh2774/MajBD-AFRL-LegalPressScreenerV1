"""Sending an approved notice to the contracting officer, an address the
reviewer chooses, or both.

Without Gmail — as on the Vercel site, where a Gmail sign-in cannot be kept —
notices go through the site's own mail service. A copy to a chosen address
needs only that; the officer's copy needs NOTICES_SEND as well, because there
are no drafts to stop at. Gmail keeps working as before, copies included.

No test talks to a mail service: `requests.post` is replaced with a recorder.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from test_mailer import Post
from test_notice_delivery import FakeGmail, _arm, _notice

from foci_screen.notify.mailer import RESEND_URL

AUTH = {"X-API-Key": "secret-key"}
KO = "ko@mail.mil"
ME = "reviewer@concord.example"
ENV = ("DATABASE_URL", "REDIS_URL", "GMAIL_ENABLED", "GMAIL_SEND", "FOCI_EMAIL_REDIRECT_TO",
       "BREVO_API_KEY", "RESEND_API_KEY", "MAIL_PROVIDER", "SMTP_HOST", "ALERTS_FROM",
       "ALERTS_REPLY_TO", "ALERTS_SEND", "NOTICES_SEND", "FOCI_PUBLIC", "FOCI_ADMIN_EMAILS",
       "FOCI_PUBLIC_URL", "VERCEL_PROJECT_PRODUCTION_URL")


@pytest.fixture()
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "n.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    for name in ENV:
        monkeypatch.delenv(name, raising=False)

    def make(**env):
        env = {"FOCI_API_KEYS": "acme:secret-key", "RESEND_API_KEY": "re_not_real",
               "ALERTS_FROM": "alerts@contractscreener.com", **env}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from foci_screen.api import app as app_module
        importlib.reload(app_module)
        tenant = "default" if env.get("FOCI_PUBLIC") else "acme"
        return TestClient(app_module.app), app_module, app_module.store_for(tenant)

    return make


def approve(c, notice_id, **body):
    return c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH, json=body)


def send(c, notice_id, **body):
    return c.post(f"/v1/notices/{notice_id}/deliver", headers=AUTH, json=body)


# ------------------------------------------------------- to the officer

def test_officers_are_not_mailed_until_notices_send_is_set(site, monkeypatch):
    post = Post(monkeypatch)
    c, _, store = site()
    notice_id = _notice(store)

    r = approve(c, notice_id).json()
    assert r["status"] == "approved", "approved, not delivered"
    assert r["delivery"]["mode"] == "render_only"
    assert r["delivery"]["outcome"] == "rendered"
    assert "NOTICES_SEND" in r["delivery"]["detail"]
    assert post.calls == []
    assert store.notice_sends([notice_id])[notice_id][0]["status"] == "rendered"


def test_with_notices_send_the_officer_is_mailed_through_resend(site, monkeypatch):
    post = Post(monkeypatch)
    c, _, store = site(NOTICES_SEND="true", ALERTS_REPLY_TO=ME)
    notice_id = _notice(store)

    r = approve(c, notice_id, note="checked the 10-K").json()
    assert r["status"] == "sent" and r["delivery"]["to"] == KO
    [call] = post.calls
    assert call["url"] == RESEND_URL
    assert call["body"]["to"] == [KO]
    assert call["body"]["subject"] == "[HIGH] FOCI screening flag: ACME"
    assert call["body"]["text"] == store.get_notice(notice_id)["body_text"]
    assert call["body"]["reply_to"] == ME, "a reply from the officer reaches a person"
    assert call["body"]["headers"]["X-FOCI-Notice"] == notice_id

    notice = store.get_notice(notice_id)
    assert notice["status"] == "sent" and notice["delivered_to"] == KO
    [logged] = store.notice_sends([notice_id])[notice_id]
    assert (logged["role"], logged["via"], logged["sent_by"]) == \
        ("officer", "resend", "api-key:acme")

    assert send(c, notice_id).status_code == 409, "never to the officer twice"
    assert len(post.calls) == 1


def test_the_edited_text_is_what_is_sent(site, monkeypatch):
    post = Post(monkeypatch)
    c, _, store = site(NOTICES_SEND="true")
    notice_id = _notice(store)
    edited = store.get_notice(notice_id)["body_text"].replace("ACME flagged.",
                                                             "ACME was flagged; see below.")
    approve(c, notice_id, body_text=edited)
    assert post.calls[0]["body"]["text"] == edited


# --------------------------------------------------------- to an address you choose

def test_a_copy_to_a_chosen_address_needs_only_the_mail_service(site, monkeypatch):
    post = Post(monkeypatch)
    c, _, store = site()
    notice_id = _notice(store)

    r = approve(c, notice_id, send_to="copy", copy_to=ME).json()
    assert r["copy"]["outcome"] == "sent" and r["copy"]["to"] == ME
    assert r["delivery"]["outcome"] == "not_requested"
    assert [x["body"]["to"] for x in post.calls] == [[ME]]
    assert store.get_notice(notice_id)["status"] == "approved", \
        "a copy elsewhere does not count as reaching the officer"


def test_both_sends_two_copies_and_records_each(site, monkeypatch):
    post = Post(monkeypatch)
    c, _, store = site(NOTICES_SEND="true")
    notice_id = _notice(store)

    r = approve(c, notice_id, send_to="both", copy_to=ME).json()
    assert r["status"] == "sent" and r["copy"]["outcome"] == "sent"
    assert sorted(x["body"]["to"][0] for x in post.calls) == [KO, ME]
    assert [s["role"] for s in store.notice_sends([notice_id])[notice_id]] == \
        ["officer", "copy"]


def test_a_copy_first_then_the_officer(site, monkeypatch):
    """Read it in your own inbox, then send it on."""
    post = Post(monkeypatch)
    c, _, store = site(NOTICES_SEND="true")
    notice_id = _notice(store)

    approve(c, notice_id, send_to="copy", copy_to=ME)
    assert send(c, notice_id, send_to="officer").json()["status"] == "sent"
    assert [x["body"]["to"][0] for x in post.calls] == [ME, KO]
    # A copy can still go afterwards, as often as asked; the officer never twice.
    assert send(c, notice_id, send_to="copy", copy_to="colleague@concord.example") \
        .status_code == 200
    assert send(c, notice_id, send_to="both", copy_to=ME).status_code == 409


def test_nobody_yet(site, monkeypatch):
    post = Post(monkeypatch)
    c, _, store = site(NOTICES_SEND="true")
    notice_id = _notice(store)
    r = approve(c, notice_id, send_to="none").json()
    assert r["status"] == "approved" and r["delivery"]["outcome"] == "not_requested"
    assert post.calls == [] and store.notice_sends([notice_id]) == {}


def test_a_notice_with_no_officer_can_still_go_to_you(site, monkeypatch):
    Post(monkeypatch)
    c, _, store = site(NOTICES_SEND="true")
    notice_id = _notice(store, recipient="")
    assert approve(c, notice_id).status_code == 409
    assert approve(c, notice_id, send_to="copy", copy_to=ME).json()["copy"]["to"] == ME


def test_what_cannot_be_sent(site, monkeypatch):
    post = Post(monkeypatch)
    c, _, store = site(NOTICES_SEND="true")
    pending, rejected = _notice(store), _notice(store)
    c.post(f"/v1/notices/{rejected}/reject", headers=AUTH, json={"note": "fp"})

    for notice_id in (pending, rejected):
        assert send(c, notice_id, send_to="copy", copy_to=ME).status_code == 409
    assert approve(c, pending, send_to="copy").status_code == 422, "copy to whom?"
    assert approve(c, pending, send_to="copy", copy_to="not an address").status_code == 422
    assert post.calls == []


def test_the_pilot_redirect_covers_officers_and_copies(site, monkeypatch):
    post = Post(monkeypatch)
    c, _, store = site(NOTICES_SEND="true", FOCI_EMAIL_REDIRECT_TO="pilot@concord.example")
    notice_id = _notice(store)
    r = approve(c, notice_id, send_to="both", copy_to=ME).json()
    assert [x["body"]["to"] for x in post.calls] == [["pilot@concord.example"]] * 2
    assert post.calls[0]["body"]["subject"].startswith(f"[for {KO}]")
    assert r["delivery"]["mode"] == "redirected"


# ----------------------------------------------------------------- Gmail

def test_with_gmail_a_copy_is_a_draft_addressed_to_you(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module, store = site()
    gmail = _arm(app_module, monkeypatch)
    notice_id = _notice(store)

    r = approve(c, notice_id, send_to="both", copy_to=ME).json()
    assert r["status"] == "drafted"
    assert [m["To"] for m in gmail.drafts_made] == [KO, ME]
    assert post.calls == [], "Gmail, when it is on, is the route for both"
    assert isinstance(gmail, FakeGmail)


# ------------------------------------------------------------ what is shown

def test_health_and_the_page_say_where_notices_go(site, monkeypatch):
    c, _, _ = site(NOTICES_SEND="true")
    assert c.get("/health").json()["notices"]["mode"] == "send"
    d = c.get("/v1/notices", headers=AUTH).json()
    assert d["sending"]["mode"] == "send" and d["sending"]["copies"] is True
    assert d["can_decide"] is True and d["reply_to"] is False


def test_visitors_see_where_copies_went_masked(site, monkeypatch):
    Post(monkeypatch)
    c, _, store = site(FOCI_PUBLIC="true", FOCI_API_KEYS="default:secret-key",
                       NOTICES_SEND="true")
    notice_id = _notice(store)
    approve(c, notice_id, send_to="both", copy_to=ME)

    seen = c.get("/v1/notices").json()
    assert seen["can_decide"] is False and "detail" not in seen["sending"]
    [n] = seen["notices"]
    assert n["recipient"] == KO, "the officer's address is public record"
    assert ME not in str(n) and "r•••@concord.example" in str(n)
    assert n["sends"][1]["sent_by"] == "api-key:default"

    full = c.get("/v1/notices", headers=AUTH).json()["notices"][0]
    assert full["sends"][1]["recipient"] == ME
