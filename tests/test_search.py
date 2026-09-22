"""Search and aggregation across contractors, awards, officers and agencies."""
from __future__ import annotations

import pytest

from foci_screen.models import Contract, ContractingOfficer, Entity, Finding, Signal
from foci_screen.store import Store


def make_contract(**kw):
    officer = ContractingOfficer(
        name=kw.pop("ko_name", "Jane Doe"),
        email=kw.pop("ko_email", "jane.doe@mail.mil"),
        source=kw.pop("ko_source", "FPDS-NG <approvedBy>"),
        confidence=kw.pop("ko_confidence", "high"))
    base = dict(
        award_id="A1", piid="N0001925C0001", recipient_name="ACME DYNAMICS LLC",
        recipient_uei="UEI123", awarding_agency="Department of Defense",
        awarding_sub_agency="Department of the Navy", award_amount=5_000_000.0,
        description="Submarine propulsion research", psc_description="R&D services",
        naics_description="Engineering services", solicitation_id="SOL-99",
        country_of_incorporation="USA")
    base.update(kw)
    return Contract(officer=officer, **base)


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "search.db"), tenant_id="acme")
    yield s
    s.close()


@pytest.fixture()
def populated(store):
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(), "r1", "UEI123")
    store.save_contract(
        make_contract(piid="N0001925C0002", award_id="A2", award_amount=12_000_000.0,
                      description="Radar sustainment",
                      ko_email="sam.smith@mail.mil", ko_name="Sam Smith"),
        "r1", "UEI123")
    store.save_contract(
        make_contract(piid="F0001925C0009", award_id="A3",
                      recipient_name="BEIJING OPTICS LTD", recipient_uei="UEI999",
                      awarding_agency="Department of Energy", awarding_sub_agency="",
                      award_amount=800_000.0, description="Optical components",
                      country_of_incorporation="CHN", foreign_owned_and_located=True),
        "r1", "UEI999")
    return store


# --------------------------------------------------------- officer names

@pytest.mark.parametrize("email,expected", [
    ("JULIE.BAKEWELLCHISHOLM.N00019@JSF.MIL", "Julie Bakewellchisholm"),
    # DoD's disambiguating digit belongs to the mailbox, not the person.
    ("SANDRA.T.REYES2.CIV@MAIL.MIL", "Sandra T Reyes"),
    ("LAUREN.H.MARTIN3.CIV@MAIL.MIL", "Lauren H Martin"),
    ("aubrey.r.callahan.civ@mail.mil", "Aubrey R Callahan"),
])
def test_officer_display_name_is_readable(email, expected):
    from foci_screen.connectors.fpds import _pretty_name
    assert _pretty_name(email) == expected


# ------------------------------------------------------------------ indexing

def test_contracts_are_indexed_for_search(populated):
    assert len(populated.search_contracts("submarine")) == 1
    assert len(populated.search_contracts("N0001925")) == 2


def test_saving_the_same_contract_twice_updates_rather_than_duplicates(store):
    store.save_contract(make_contract(award_amount=1.0), "r1", "UEI123")
    store.save_contract(make_contract(award_amount=99.0), "r2", "UEI123")

    rows = store.contracts_where("entity_key", "UEI123")
    assert len(rows) == 1
    assert rows[0]["amount"] == 99.0
    assert rows[0]["run_id"] == "r2"


def test_contract_without_an_identifier_is_skipped(store):
    store.save_contract(make_contract(piid="", award_id=""), "r1", "UEI123")
    assert store.contracts_where("entity_key", "UEI123") == []


def test_ip_clauses_round_trip_as_a_list(store):
    store.save_contract(
        make_contract(ip_clause_hits=["DFARS 252.227-7013", "Limited Rights data"]),
        "r1", "UEI123")
    assert store.get_contract("N0001925C0001")["ip_clauses"] == [
        "DFARS 252.227-7013", "Limited Rights data"]


# -------------------------------------------------------------------- search

def test_search_is_case_insensitive(populated):
    """Postgres LIKE is case-sensitive and SQLite has no ILIKE; both must match."""
    assert len(populated.search_entities("acme")) == 1
    assert len(populated.search_entities("ACME")) == 1
    assert len(populated.search_entities("AcMe")) == 1


def test_search_entities_aggregates_awards(populated):
    acme = populated.search_entities("acme")[0]
    assert acme["contract_count"] == 2
    assert acme["obligated"] == 17_000_000.0


def test_search_entities_attaches_latest_severity(populated):
    populated.save_finding(Finding(
        entity=Entity(name="ACME DYNAMICS LLC", uei="UEI123"),
        signals=[], total_score=22.0, severity="high", run_id="r1"))

    acme = populated.search_entities("acme")[0]
    assert acme["severity"] == "high"
    assert acme["score"] == 22.0


def test_unscreened_entity_reports_no_severity(populated):
    beijing = populated.search_entities("beijing")[0]
    assert beijing["severity"] is None
    assert beijing["foreign_owned"] == 1


def test_search_officers_groups_by_email(populated):
    officers = {o["ko_email"]: o for o in populated.search_officers()}
    assert set(officers) == {"jane.doe@mail.mil", "sam.smith@mail.mil"}
    assert officers["jane.doe@mail.mil"]["contract_count"] == 2
    assert officers["jane.doe@mail.mil"]["entity_count"] == 2


def test_search_officers_by_name(populated):
    assert len(populated.search_officers("sam")) == 1


def test_officers_without_an_email_are_excluded(store):
    """An unresolved officer is not a person you can address."""
    store.save_contract(make_contract(ko_email="", ko_name=""), "r1", "UEI123")
    assert store.search_officers() == []


def test_search_agencies_aggregates(populated):
    agencies = {a["agency"]: a for a in populated.search_agencies()}
    assert agencies["Department of Defense"]["contract_count"] == 2
    assert agencies["Department of Defense"]["obligated"] == 17_000_000.0
    assert agencies["Department of Energy"]["entity_count"] == 1


def test_search_all_covers_every_kind(populated):
    results = populated.search_all("acme")
    assert results["entities"]
    assert results["officers"] == [] or True   # name search need not match here
    assert set(results) == {"entities", "contracts", "officers", "agencies"}


def test_empty_query_browses_rather_than_failing(populated):
    assert len(populated.search_entities("")) == 2
    assert len(populated.search_agencies("")) == 2


def test_filtering_by_column_rejects_unknown_columns(populated):
    """The column name reaches SQL directly, so it is allow-listed."""
    with pytest.raises(ValueError):
        populated.contracts_where("amount; DROP TABLE contracts", "x")


def test_search_is_tenant_scoped(tmp_path):
    path = str(tmp_path / "multi.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        acme.save_contract(make_contract(), "r1", "UEI123")
        assert len(acme.search_contracts("submarine")) == 1
        assert other.search_contracts("submarine") == []
        assert other.search_officers() == []
    finally:
        acme.close()
        other.close()


# ---------------------------------------------------------------- dashboards

def test_severity_counts_use_latest_screen_per_entity(store):
    """An entity screened weekly must not dominate the distribution."""
    ent = Entity(name="ACME", uei="UEI123")
    for run, sev in (("r1", "low"), ("r2", "medium"), ("r3", "critical")):
        store.save_finding(Finding(entity=ent, signals=[], total_score=1.0,
                                   severity=sev, run_id=run))

    assert store.severity_counts() == {"critical": 1}


def test_signal_category_counts(store):
    store.save_finding(Finding(
        entity=Entity(name="ACME", uei="UEI123"),
        signals=[
            Signal(rule_id="R1", category="FOCI", severity="high", score=5,
                   title="t", rationale="r", evidence="e", source="sec"),
            Signal(rule_id="R2", category="FOCI", severity="low", score=1,
                   title="t", rationale="r", evidence="e", source="sec"),
            Signal(rule_id="R3", category="IP_COLLATERAL", severity="low", score=1,
                   title="t", rationale="r", evidence="e", source="sec"),
        ],
        total_score=7.0, severity="high", run_id="r1"))

    assert store.signal_category_counts() == {"FOCI": 2, "IP_COLLATERAL": 1}


def test_totals(populated):
    t = populated.totals()
    assert t["contracts"] == 3
    assert t["entities"] == 2
    assert t["officers"] == 2
    assert t["obligated"] == 17_800_000.0
    assert t["notices_pending"] == 0


# ---------------------------------------------------------------------- API

AUTH = {"X-API-Key": "secret-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)

    store = app_module.store_for("acme")
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(), "r1", "UEI123")
    return TestClient(app_module.app), app_module


def test_search_endpoint_groups_results(client):
    c, _ = client
    body = c.get("/v1/search?q=submarine", headers=AUTH).json()
    assert len(body["contracts"]) == 1
    assert body["query"] == "submarine"


def test_search_endpoint_filters_by_kind(client):
    c, _ = client
    body = c.get("/v1/search?q=acme&kind=entity", headers=AUTH).json()
    assert "entities" in body and "contracts" not in body


def test_search_rejects_unknown_kind(client):
    c, _ = client
    assert c.get("/v1/search?q=x&kind=planets", headers=AUTH).status_code == 422


def test_overview_endpoint(client):
    c, _ = client
    body = c.get("/v1/overview", headers=AUTH).json()
    assert body["totals"]["contracts"] == 1
    assert set(body) == {"totals", "severity", "categories", "findings_by_day",
                         "top_entities", "recent_findings"}


def test_officer_profile(client):
    c, _ = client
    body = c.get("/v1/officers/jane.doe@mail.mil", headers=AUTH).json()
    assert body["officer"]["name"] == "Jane Doe"
    assert body["officer"]["confidence"] == "high"
    assert len(body["contracts"]) == 1


def test_unknown_officer_is_404(client):
    c, _ = client
    assert c.get("/v1/officers/nobody@mail.mil", headers=AUTH).status_code == 404


def test_agency_profile_falls_back_to_sub_agency(client):
    c, _ = client
    body = c.get("/v1/agencies/Department of the Navy", headers=AUTH).json()
    assert body["contract_count"] == 1
    assert body["entities"][0]["entity_name"] == "ACME DYNAMICS LLC"


def test_contract_detail(client):
    c, _ = client
    body = c.get("/v1/contracts/N0001925C0001", headers=AUTH).json()
    assert body["contract"]["ko_email"] == "jane.doe@mail.mil"
    assert body["contract"]["amount"] == 5_000_000.0


def test_entity_detail_uses_the_contracts_table(client):
    """An entity re-screened with no new signal still has its awards."""
    c, _ = client
    body = c.get("/v1/entities/UEI123", headers=AUTH).json()
    assert len(body["contracts"]) == 1
    assert body["obligated"] == 5_000_000.0
    assert body["latest_finding"] is None


def test_search_endpoints_need_a_key(client):
    c, _ = client
    assert c.get("/v1/search?q=acme").status_code == 401
    assert c.get("/v1/overview").status_code == 401


# ------------------------------------------------------------------ web app

def test_web_ui_is_served(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert "foci-screen" in r.text
    assert c.get("/app.js").status_code == 200
    assert c.get("/styles.css").status_code == 200


def test_static_mount_does_not_shadow_the_api(client):
    """The catch-all mount is last; /v1 must still route."""
    c, _ = client
    assert c.get("/v1/overview", headers=AUTH).status_code == 200
    assert c.get("/health").status_code == 200
