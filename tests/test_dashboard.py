"""The signed-in dashboard is the account's own work; suggestions forgive the
way company names are typed; and the site's emails greet people by name.

No test talks to a mail service or to USAspending: `requests.post` is replaced
with a recorder, and the remote suggestions with nothing.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from test_mailer import Post
from test_search import make_contract

from foci_screen import aliases
from foci_screen import portfolio as pf
from foci_screen.notify import names

WEB = {"X-Foci-Client": "web"}
PASSWORD = "correct horse battery"
ME = "jordan.smith@firm.example"
ONE = pf.encode(pf.from_entity_keys("Watch", ["UEI1"]))
ENV = ("DATABASE_URL", "REDIS_URL", "GMAIL_ENABLED", "FOCI_EMAIL_REDIRECT_TO", "BREVO_API_KEY",
       "MAIL_PROVIDER", "SMTP_HOST", "ALERTS_SEND", "ALERTS_REPLY_TO", "NOTICES_SEND",
       "FOCI_PUBLIC_URL", "VERCEL_PROJECT_PRODUCTION_URL", "FOCI_SIGNUP")


@pytest.fixture()
def site(tmp_path, monkeypatch):
    def make(**env):
        base = {"FOCI_API_KEYS": "default:admin-key", "FOCI_DB": str(tmp_path / "d.db"),
                "FOCI_CACHE": str(tmp_path / "cache"), "FOCI_OUT": str(tmp_path / "out"),
                "FOCI_PUBLIC": "true", "RESEND_API_KEY": "re_not_real",
                "ALERTS_FROM": "alerts@contractscreener.com"}
        for name in ENV:
            monkeypatch.delenv(name, raising=False)
        for k, v in {**base, **env}.items():
            monkeypatch.setenv(k, v)
        from foci_screen.api import app as app_module
        importlib.reload(app_module)
        monkeypatch.setattr(app_module, "_remote_suggestions", lambda q: [])
        return TestClient(app_module.app), app_module
    return make


def signup(c, email=ME, **extra):
    return c.post("/v1/auth/signup", headers=WEB,
                  json={"email": email, "password": PASSWORD, **extra})


def confirm(app_module, email=ME):
    store = app_module.store_for("default")
    store.mark_email_verified(store.user_by_email(email)["user_id"])


def seed(store, *names_and_keys):
    store.start_run("DoD", {}, run_id="seed")
    for n, (name, key) in enumerate(names_and_keys):
        store.save_contract(make_contract(piid=f"P{n}", award_id=f"A{n}", recipient_name=name,
                                          recipient_uei=key), "seed", key)


def sent_to(post, address):
    return [call["body"] for call in post.calls if address in call["body"]["to"]]


# ------------------------------------------------------------------ names

@pytest.mark.parametrize("address,expected", [
    ("jordan.smith@agency.gov", "Jordan"),
    ("JANE.DOE2.CIV@MAIL.MIL", "Jane"),
    ("anne-marie.dupont@firm.example", "Anne-Marie"),
    ("jsmith@agency.gov", ""),               # no way to tell
    ("j.smith@agency.gov", ""),              # an initial is not a name
    ("contracts.team@agency.gov", ""),       # a desk, not a person
    ("info@agency.gov", ""),
])
def test_a_first_name_from_an_address_only_when_it_is_clear(address, expected):
    assert names.first_name_from_address(address) == expected


@pytest.mark.parametrize("full,expected", [
    ("Jordan A. Smith", "Jordan"), ("SMITH, JORDAN A", "Jordan"), ("J. Smith", ""), ("", ""),
])
def test_a_first_name_from_a_full_name(full, expected):
    assert names.first_name(full) == expected


def test_the_greeting_tops_both_versions_of_an_email():
    text, html = names.greet("Body", '<div style="x"><p>Hi</p></div>', "Jordan")
    assert text == "Hello Jordan,\n\nBody"
    assert html == '<div style="x"><p>Hello Jordan,</p><p>Hi</p></div>'
    assert names.greet("Body", None, "") == ("Hello,\n\nBody", None)


# ---------------------------------------------------------- matching names

def test_typos_and_shortenings_find_the_company():
    known = [("1", "LOCKHEED MARTIN CORP"), ("2", "GENERAL DYNAMICS CORPORATION"),
             ("3", "RAYTHEON COMPANY")]
    best = {q: [k for k, _, _ in aliases.close_matches(q, known)]
            for q in ("lockhed martn", "gen dyn", "raythoen", "zz top")}
    assert best == {"lockhed martn": ["1"], "gen dyn": ["2"], "raythoen": ["3"], "zz top": []}


def test_the_legal_form_word_need_not_match(tmp_path):
    from foci_screen.store import Store
    store = Store(str(tmp_path / "s.db"), tenant_id="t")
    seed(store, ("ACME CORP", "UEI1"), ("SHERLOCK HOLDINGS", "UEI2"), ("LOCKWOOD INC", "UEI3"))
    assert [r["entity_key"] for r in store.search_entities("Acme Corporation")] == ["UEI1"]
    # A name starting with what was typed, then a word starting with it.
    assert [r["entity_key"] for r in store.search_entities("lock")] == ["UEI3", "UEI2"]
    store.close()


def test_a_mistyped_name_is_suggested_as_a_close_match(site, monkeypatch):
    c, app_module = site()
    seed(app_module.store_for("default"), ("LOCKHEED MARTIN CORP", "UEI1"))
    d = c.get("/v1/suggest?q=lockhed").json()
    assert [(i["key"], i.get("close")) for i in d["local"]] == [("UEI1", True)]
    assert d["local"][0]["detail"] == "close match · 1 award"
    exact = c.get("/v1/suggest?q=lockheed").json()["local"]
    assert exact[0]["key"] == "UEI1" and not exact[0].get("close")


# ------------------------------------------------------------ the dashboard

def test_the_dashboard_is_what_you_searched_opened_and_screened(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    workspace = signup(c).json()["account"]["workspace"]
    seed(app_module.store_for(workspace), ("ACME DYNAMICS LLC", "UEI123"))
    monkeypatch.setattr(app_module.queue, "enqueue", lambda *a, **kw: None)

    c.get("/v1/search?q=Acme")
    c.get("/v1/search?q=acme&offset=10")          # the next page is the same search
    c.get("/v1/entities/UEI123")
    run = c.post("/v1/screens", headers=WEB, json={"recipient": "ACME DYNAMICS LLC"}).json()

    d = c.get("/v1/me/activity").json()
    assert [s["query"] for s in d["searches"]] == ["Acme"]
    assert [(x["key"], x["name"]) for x in d["companies"]] == [("UEI123", "ACME DYNAMICS LLC")]
    assert [(x["run_id"], x["status"]) for x in d["screens"]] == [(run["run_id"], "queued")]

    assert c.delete("/v1/me/activity", headers=WEB).status_code == 204
    assert c.get("/v1/me/activity").json() == {"searches": [], "companies": [], "screens": []}


def test_nothing_is_kept_for_a_visitor_or_shown_to_another_account(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    anyone = TestClient(app_module.app)
    anyone.get("/v1/search?q=visitor")
    signup(c)
    c.get("/v1/search?q=mine")
    other = TestClient(app_module.app)
    signup(other, "pat.lee@elsewhere.example")
    assert other.get("/v1/me/activity").json()["searches"] == []
    assert [s["query"] for s in c.get("/v1/me/activity").json()["searches"]] == ["mine"]
    assert anyone.get("/v1/me/activity").status_code == 401


def test_only_the_latest_few_are_kept(site, monkeypatch):
    Post(monkeypatch)
    c, app_module = site()
    signup(c)
    monkeypatch.setattr(app_module.Store, "ACTIVITY_KEPT", 3)
    for q in ("one", "two", "three", "four"):
        c.get(f"/v1/search?q={q}")
    site_store = app_module.store_for("default")
    kept = site_store.activity(site_store.user_by_email(ME)["user_id"], "search", 10)
    assert len(kept) == 3


# ------------------------------------------------------- greeting by name

def test_signing_up_with_a_name_is_greeted_by_it(site, monkeypatch):
    post = Post(monkeypatch)
    c, _ = site()
    account = signup(c, "j.s@firm.example", name="Jordan Smith").json()["account"]
    assert account["name"] == "Jordan Smith"
    [mail] = sent_to(post, "j.s@firm.example")
    assert mail["text"].startswith("Hello Jordan,\n\n")
    changed = c.put("/v1/me/name", headers=WEB, json={"name": "Jo"}).json()["account"]
    assert changed["name"] == "Jo"


def test_alerts_and_requests_to_confirm_greet_each_reader(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = site()
    signup(c, name="Jordan Smith")
    confirm(app_module)
    sid = c.post("/v1/alerts/subscriptions", headers=WEB, json={
        "name": "Watch", "emails": [ME, "pat.lee@elsewhere.example", "desk@elsewhere.example"],
        "portfolio_key": ONE}).json()["subscription"]["subscription_id"]
    [ask] = sent_to(post, "pat.lee@elsewhere.example")
    assert ask["text"].startswith("Hello Pat,\n\n") and "<p>Hello Pat,</p>" in ask["html"]
    assert sent_to(post, "desk@elsewhere.example")[0]["text"].startswith("Hello,\n\n")

    c.post(f"/v1/alerts/subscriptions/{sid}/test", headers=WEB)
    test = [m for m in sent_to(post, ME) if "test alert" in m["subject"]]
    assert test[0]["text"].startswith("Hello Jordan,\n\n")


def test_a_shared_portfolio_greets_each_colleague(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module = site()
    workspace = signup(c, name="Jordan Smith").json()["account"]["workspace"]
    confirm(app_module)
    app_module.store_for("default").create_user(
        email="casey.jones@firm.example", password_hash="x", tenant_id=workspace,
        role="member", verified=True)
    app_module.store_for("default").create_user(
        email="cj@firm.example", password_hash="x", tenant_id=workspace, role="member",
        verified=True, name="Charlie Jay")
    r = c.post("/v1/portfolio/email", headers=WEB, json={
        "key": ONE, "to": [ME, "casey.jones@firm.example", "cj@firm.example"]})
    assert r.json()["status"] == "sent"
    assert sent_to(post, ME)[-1]["text"].startswith("Hello Jordan,")
    assert sent_to(post, "casey.jones@firm.example")[0]["text"].startswith("Hello Casey,")
    assert sent_to(post, "cj@firm.example")[0]["text"].startswith("Hello Charlie,")
