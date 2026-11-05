"""Alert emails as flags: a line per flag saying how serious, what and from
where, and the detailed report under it.

    [HIGH] FLAG 1: ACME DYNAMICS LLC — Name on the Treasury SDN list: … (Treasury
         SDN list, SEC EDGAR)
         What the screen found:
           • [HIGH] …
             Found: "…"
"""
from __future__ import annotations

import re

import pytest
from test_adv_alerts import FakeAdv, FakeCfg, subscribe

from foci_screen import alerts
from foci_screen.models import Contract, ContractingOfficer, Entity, Finding, Signal
from foci_screen.notify import flags
from foci_screen.notify.mailer import Mailer
from foci_screen.store import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "flags.db"), tenant_id="acme")
    yield s
    s.close()


def signal(rule, severity, title, evidence, source="sec_edgar", key="", **kw):
    return Signal(rule_id=rule, category=kw.pop("category", "FOCI"), severity=severity,
                  score=kw.pop("score", 20.0), title=title,
                  rationale=kw.pop("rationale", "Why the rule counts this."),
                  evidence=evidence, source=source,
                  source_url=kw.pop("url", "https://www.sec.gov/Archives/x.htm"),
                  document_key=key, **kw)


def flagged(store, signals, severity="high", coverage=None):
    store.start_run("DoD", {}, run_id="r1")
    finding = Finding(
        entity=Entity(name="ACME DYNAMICS LLC", uei="UEI123", parent_name="ACME HOLDINGS"),
        signals=signals, severity=severity, run_id="r1", total_score=25.0,
        contracts=[Contract(award_id="A1", piid="A1", award_amount=2_500_000,
                            awarding_sub_agency="Department of the Air Force",
                            officer=ContractingOfficer())],
        coverage=coverage or {})
    store.save_finding(finding)
    store.create_notice(run_id="r1", entity_key="UEI123", entity_name="ACME DYNAMICS LLC",
                        severity=severity, recipient="", officer_confidence="unresolved",
                        subject="s", body_text="b", trigger_reason="High finding.")


def email(store, sid):
    alerts.run_alerts(store, FakeAdv(), Mailer(FakeCfg()), "https://site.example")
    [delivery] = store.alert_deliveries(sid)
    return delivery


def test_a_flag_is_one_line_with_the_evidence_and_sources_under_it(store):
    sid = subscribe(store, ["UEI123"])
    flagged(store, [
        signal("FOCI-JURIS-01", "high", "Russia nexus in sec_edgar document",
               "the Company sold 40% of its shares to a Moscow fund", key="doc1"),
        signal("SANCTION-02", "high", "Name on the Treasury SDN list: ACME DYNAMIKA OOO",
               "Treasury SDN list: ACME DYNAMIKA OOO", source="screening_lists",
               category="SANCTIONS", score=15.0),
    ], coverage={"SEC filings": "checked",
                 "USPTO patent liens": "not checked: the site has no USPTO key"})
    delivery = email(store, sid)
    body = delivery["body_text"]
    assert delivery["subject"] == 'FOCI-Screener [HIGH]: 1 flag for "Harborlight watch"'
    assert re.search(r"^\[HIGH\] FLAG 1: ACME DYNAMICS LLC — Russia nexus in an SEC filing, "
                     r"and 1 more finding: sanctions or exclusions \(SEC EDGAR, Treasury SDN "
                     r"list\)$", body, re.M)
    lines = body.splitlines()
    under = lines[next(i for i, x in enumerate(lines) if x.startswith("[HIGH] FLAG 1")):]
    assert under[1] == "     What the screen found:", "the detail sits five spaces in"
    assert '         Found: "the Company sold 40% of its shares to a Moscow fund"' in body
    assert "Why it counts: Why the rule counts this." in body
    assert "1 award looked at, $2.5M in all, from Department of the Air Force." in body
    assert "USPTO patent liens: not checked: the site has no USPTO key" in body
    assert "SEC filings: checked" not in body, "only what was not checked is listed"
    assert "https://site.example/#/entity/UEI123" in body


def test_the_same_evidence_in_three_passages_is_one_point(store):
    sid = subscribe(store, ["UEI123"])
    flagged(store, [signal("FOCI-MENTION-01", "medium",
                           "China named in sec_edgar document (no transaction identified)",
                           f"passage {i} names China", score=10.0 - i) for i in range(3)],
            severity="medium")
    body = email(store, sid)["body_text"]
    assert body.count("• [MEDIUM] China named in an SEC filing") == 1
    assert "Also found in 2 other passages" in body
    assert "more finding" not in body, "one finding, in three places"


def test_flags_come_most_serious_first_and_a_routine_change_is_an_update():
    items = [
        {"item_id": "a", "kind": "adv", "headline": "X filed an updated Form ADV.",
         "detail": "", "explainer": "", "where": "", "links": [], "importance": 1,
         "severity": "info", "flag": "X filed an updated Form ADV", "sources": ["Form ADV"]},
        {"item_id": "b", "kind": "adv", "headline": "Y's fund rose to 45% foreign.",
         "detail": "", "explainer": "", "where": "", "links": [], "importance": 6,
         "severity": "high", "flag": "Y's fund rose to 45% foreign", "sources": ["Form ADV"]},
    ]
    items.sort(key=lambda i: (flags.rank(i["severity"]), -i["importance"]))
    subject, text, html = alerts.compose({"name": "Watch"}, items)
    assert subject == 'FOCI-Screener [HIGH]: 1 flag and 1 update for "Watch"'
    head = text.split("—" * 24)[0]
    assert head.index("[HIGH] FLAG 1") < head.index("[INFO] UPDATE 2"), \
        "a list of every flag first, when there is more than one"
    assert 'href="#flag-1"' in html and "background:#b03a2e" in html


@pytest.mark.parametrize("importance,severity", [(7, "high"), (6, "high"), (5, "medium"),
                                                 (4, "medium"), (3, "low"), (1, "info")])
def test_a_filing_change_is_rated_from_its_importance(importance, severity):
    assert flags.from_importance(importance) == severity


def test_a_notice_without_its_finding_still_reads(store):
    """Older notices, and any whose finding is gone: the line still says who
    and how serious."""
    sid = subscribe(store, ["UEI123"])
    store.start_run("DoD", {}, run_id="r1")
    store.create_notice(run_id="r1", entity_key="UEI123", entity_name="ACME DYNAMICS LLC",
                        severity="critical", recipient="", officer_confidence="unresolved",
                        subject="s", body_text="b", trigger_reason="Always notify: a novation.")
    body = email(store, sid)["body_text"]
    assert "[CRITICAL] FLAG 1: ACME DYNAMICS LLC was flagged" in body
    assert "Always notify: a novation." in body
