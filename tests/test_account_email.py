"""The emails an account is sent: confirming an address, resetting a password,
and joining an organisation — one organisation per account.

No test here talks to a mail service: `requests.post` is replaced by the
recorder from test_mailer, so what is checked is what would be sent.
"""
from __future__ import annotations

import importlib
import logging
import re

import pytest
from fastapi.testclient import TestClient
from test_mailer import Post

WEB = {"X-Foci-Client": "web"}
PASSWORD = "correct horse battery"
MAIL = {"BREVO_API_KEY": "xkeysib-not-real", "ALERTS_FROM": "alerts@concord.example"}


@pytest.fixture()
def make_client(tmp_path, monkeypatch):
    def make(**env):
        base = {"FOCI_API_KEYS": "", "FOCI_DB": str(tmp_path / "api.db"),
                "FOCI_CACHE": str(tmp_path / "cache"), "FOCI_OUT": str(tmp_path / "out"),
                "FOCI_SIGNUP": "open", "FOCI_PUBLIC": "",
                "FOCI_PUBLIC_URL": "https://site.example"}
        for name in ("DATABASE_URL", "REDIS_URL", "BREVO_API_KEY", "RESEND_API_KEY",
                     "SMTP_HOST", "ALERTS_FROM", "FOCI_EMAIL_REDIRECT_TO", "ALERTS_SEND"):
            monkeypatch.delenv(name, raising=False)
        for k, v in {**base, **env}.items():
            monkeypatch.setenv(k, v)
        from foci_screen.api import app as app_module
        importlib.reload(app_module)
        return app_module
    return make


def client(app_module) -> TestClient:
    return TestClient(app_module.app, base_url="https://testserver")


def link_in(text: str, path: str) -> str:
    return re.search(rf"https://site\.example{re.escape(path)}[\w-]+", text).group(0)


def token_of(link: str) -> str:
    return link.rsplit("/", 1)[-1]


def signup(c, email="owner@agency.gov", **extra):
    return c.post("/v1/auth/signup", headers=WEB,
                  json={"email": email, "password": PASSWORD, **extra})


def sent_to(post, address):
    return [call["body"] for call in post.calls
            if call["body"]["to"] == [{"email": address}]]


# ---------------------------------------------------------- confirmation

def test_signing_up_sends_a_confirmation_link_that_works_once(make_client, monkeypatch):
    app_module = make_client(**MAIL)
    post = Post(monkeypatch)
    c = client(app_module)
    r = signup(c, organisation="Concord Contracts")
    assert r.json()["verification_email"]["sent"] is True
    assert r.json()["account"]["organisation"] == "Concord Contracts"
    assert r.json()["account"]["email_verified"] is False

    [mail] = sent_to(post, "owner@agency.gov")
    assert "Concord Contracts" in mail["textContent"]
    link = link_in(mail["textContent"], "/account/verify/")

    page = c.get(link.replace("https://site.example", ""))
    assert page.status_code == 200 and "confirmed" in page.text
    assert c.get("/v1/me").json()["account"]["email_verified"] is True
    assert c.get(link.replace("https://site.example", "")).status_code == 404


def test_without_a_mail_service_the_link_goes_to_the_server_log(make_client, caplog):
    app_module = make_client()
    with caplog.at_level(logging.WARNING, logger="foci.api"):
        r = signup(client(app_module))
    assert r.status_code == 201
    assert r.json()["verification_email"]["sent"] is False
    assert "/account/verify/" in caplog.text


def test_the_organisation_defaults_to_the_domain(make_client):
    app_module = make_client()
    assert signup(client(app_module)).json()["account"]["organisation"] == "agency.gov"


# ------------------------------------------------------------- reset

def test_a_password_can_be_reset_by_email(make_client, monkeypatch):
    app_module = make_client(**MAIL)
    first = client(app_module)
    signup(first)
    post = Post(monkeypatch)

    c = client(app_module)
    r = c.post("/v1/auth/forgot", headers=WEB, json={"email": "Owner@Agency.gov"})
    assert "token" not in r.text and "/reset/" not in r.text, "the link only goes by email"
    [mail] = sent_to(post, "owner@agency.gov")
    token = token_of(link_in(mail["textContent"], "/#/reset/"))

    done = c.post("/v1/auth/reset", headers=WEB,
                  json={"token": token, "password": "a brand new phrase"})
    assert done.status_code == 200 and done.json()["account"]["email_verified"] is True
    assert c.get("/v1/auth/status").json()["signed_in"] is True
    # Every other session ended; the old password no longer works.
    assert first.get("/v1/auth/status").json()["signed_in"] is False
    assert client(app_module).post("/v1/auth/login", headers=WEB, json={
        "email": "owner@agency.gov", "password": PASSWORD}).status_code == 401
    # The link works once.
    assert c.post("/v1/auth/reset", headers=WEB,
                  json={"token": token, "password": "another new phrase"}).status_code == 410


def test_the_reply_does_not_say_who_has_an_account(make_client, monkeypatch):
    app_module = make_client(**MAIL)
    signup(client(app_module))
    post = Post(monkeypatch)
    c = client(app_module)
    known = c.post("/v1/auth/forgot", headers=WEB, json={"email": "owner@agency.gov"})
    unknown = c.post("/v1/auth/forgot", headers=WEB, json={"email": "nobody@agency.gov"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert sent_to(post, "nobody@agency.gov") == []


def test_reset_emails_cannot_be_used_to_flood_an_inbox(make_client, monkeypatch):
    app_module = make_client(**MAIL)
    signup(client(app_module))
    post = Post(monkeypatch)
    c = client(app_module)
    for _ in range(3):
        c.post("/v1/auth/forgot", headers=WEB, json={"email": "owner@agency.gov"})
    assert len(sent_to(post, "owner@agency.gov")) == 1


def test_a_reset_reaches_the_person_even_during_a_pilot_redirect(make_client, monkeypatch):
    """The redirect keeps alerts about companies from reaching people early;
    sending someone's own reset link elsewhere would only lock them out."""
    app_module = make_client(**MAIL, FOCI_EMAIL_REDIRECT_TO="pilot@concord.example")
    signup(client(app_module))
    post = Post(monkeypatch)
    client(app_module).post("/v1/auth/forgot", headers=WEB, json={"email": "owner@agency.gov"})
    assert len(sent_to(post, "owner@agency.gov")) == 1
    assert sent_to(post, "pilot@concord.example") == []


# --------------------------------------------------------- organisation

def invite(c, email="colleague@agency.gov", role="member"):
    return c.post("/v1/org/invites", headers=WEB, json={"email": email, "role": role})


def test_an_invited_colleague_joins_the_same_workspace(make_client, monkeypatch):
    app_module = make_client(**MAIL)
    owner = client(app_module)
    workspace = signup(owner, organisation="Concord").json()["account"]["workspace"]
    app_module.store_for(workspace).create_notice(
        run_id="r1", entity_key="UEI1", entity_name="SHARED LLC", severity="high",
        recipient="ko@mail.mil", officer_confidence="high", subject="s", body_text="b")
    post = Post(monkeypatch)

    r = invite(owner)
    assert r.status_code == 201 and r.json()["delivery"]["sent"] and r.json()["link"] == ""
    [mail] = sent_to(post, "colleague@agency.gov")
    assert mail["subject"] == "owner@agency.gov invited you to Concord on FOCI-Screener"
    token = token_of(link_in(mail["textContent"], "/#/join/"))

    colleague = client(app_module)
    about = colleague.get(f"/v1/auth/invite/{token}").json()
    assert about["organisation"] == "Concord" and about["invited_by"] == "owner@agency.gov"

    joined = colleague.post("/v1/auth/join", headers=WEB,
                            json={"token": token, "password": "colleague phrase"})
    account = joined.json()["account"]
    assert account["workspace"] == workspace and account["role"] == "member"
    assert account["email_verified"] is True
    assert [n["entity_name"] for n in colleague.get("/v1/notices").json()["notices"]] \
        == ["SHARED LLC"]

    org = owner.get("/v1/org").json()
    assert [m["email"] for m in org["members"]] == ["owner@agency.gov",
                                                   "colleague@agency.gov"]
    assert org["invites"] == []
    assert colleague.post("/v1/auth/join", headers=WEB, json={
        "token": token, "password": "colleague phrase"}).status_code == 410


def test_an_account_belongs_to_one_organisation(make_client):
    app_module = make_client()
    owner = client(app_module)
    signup(owner)
    signup(client(app_module), "elsewhere@other.example")
    r = invite(owner, "elsewhere@other.example")
    assert r.status_code == 409 and "one organisation" in r.json()["detail"]


def test_only_owners_manage_the_organisation(make_client):
    app_module = make_client()
    owner = client(app_module)
    signup(owner)
    token = token_of(invite(owner).json()["link"])
    member = client(app_module)
    member.post("/v1/auth/join", headers=WEB,
                json={"token": token, "password": "member phrase"})

    assert invite(member, "third@agency.gov").status_code == 403
    assert member.put("/v1/org", headers=WEB, json={"name": "Mine now"}).status_code == 403
    assert member.get("/v1/org").json()["invites"] == []

    me = owner.get("/v1/org").json()
    member_id = next(m["user_id"] for m in me["members"] if m["role"] == "member")
    assert owner.delete(f"/v1/org/members/{me['you']}", headers=WEB).status_code == 409
    assert owner.delete(f"/v1/org/members/{member_id}", headers=WEB).status_code == 204
    assert member.get("/v1/auth/status").json()["signed_in"] is False


def test_without_a_mail_service_the_owner_gets_the_link_to_pass_on(make_client):
    app_module = make_client()
    owner = client(app_module)
    signup(owner)
    r = invite(owner).json()
    assert r["delivery"]["sent"] is False
    assert "/#/join/" in r["link"]


def test_a_withdrawn_invitation_cannot_be_used(make_client):
    app_module = make_client()
    owner = client(app_module)
    signup(owner)
    token = token_of(invite(owner).json()["link"])
    assert owner.delete("/v1/org/invites/colleague@agency.gov", headers=WEB).status_code == 204
    assert client(app_module).post("/v1/auth/join", headers=WEB, json={
        "token": token, "password": "colleague phrase"}).status_code == 410


def test_renaming(make_client):
    app_module = make_client()
    owner = client(app_module)
    signup(owner)
    assert owner.put("/v1/org", headers=WEB, json={"name": "Concord Group"}
                     ).json()["name"] == "Concord Group"
    assert owner.get("/v1/me").json()["account"]["organisation"] == "Concord Group"
