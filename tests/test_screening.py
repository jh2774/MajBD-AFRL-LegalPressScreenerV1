"""Offline tests: rules, scoring, change detection, and notice rendering.

No network. Everything here runs against synthetic documents so the analytic
behaviour is pinned independently of whether api.sam.gov is up.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foci_screen.models import Contract, ContractingOfficer, Document, Entity  # noqa: E402
from foci_screen.notify import render  # noqa: E402
from foci_screen.risk import engine, lexicon  # noqa: E402
from foci_screen.store import Store, added_text  # noqa: E402


def make_contract(**kw) -> Contract:
    base = dict(award_id="N0001925C0042", piid="N0001925C0042",
                recipient_name="Vector Photonics Inc", recipient_uei="ABC123DEF456",
                awarding_agency="Department of Defense",
                awarding_sub_agency="Department of the Navy",
                award_amount=42_000_000.0, start_date="2025-01-15",
                end_date="2029-01-14",
                description="Development of laser countermeasure subsystem. "
                            "Data delivered under DFARS 252.227-7013.",
                officer=ContractingOfficer(name="Jane Doe", email="jane.doe.n00019@us.navy.mil",
                                           source="FPDS-NG <approvedBy>", confidence="high"))
    base.update(kw)
    c = Contract(**base)
    c.ip_clause_hits = lexicon.find_ip_clauses(c.description)
    return c


ENTITY = Entity(name="Vector Photonics Inc", uei="ABC123DEF456", cik="0001234567")


class TestLexicon(unittest.TestCase):
    def test_covered_and_haven_jurisdictions(self):
        hits = dict(lexicon.find_jurisdictions(
            "The investor is a British Virgin Islands entity backed by a Shanghai fund."))
        self.assertIn("British Virgin Islands", hits)
        self.assertIn("China", hits)

    def test_tiers_and_multipliers(self):
        self.assertEqual(lexicon.jurisdiction_tier("China"), "covered")
        self.assertEqual(lexicon.jurisdiction_tier("Cayman Islands"), "haven")
        self.assertGreater(lexicon.jurisdiction_multiplier("China"),
                           lexicon.jurisdiction_multiplier("Ireland"))

    def test_ip_clause_detection(self):
        clauses = lexicon.find_ip_clauses(
            "Technical data delivered with Government Purpose Rights per "
            "DFARS 252.227-7014.")
        self.assertTrue(any("7014" in c for c in clauses))
        self.assertTrue(any("Government Purpose Rights" in c for c in clauses))

    def test_no_false_positive_on_plain_text(self):
        self.assertEqual(lexicon.find_jurisdictions("Routine maintenance in Ohio."), [])

    def test_terms_match_whole_words_only(self):
        """'SPAC' inside 'space' turned an aerospace history page into a
        CRITICAL foreign-investment notice in an early build."""
        aerospace = ("The space vehicle launched from Vandenberg. Our spacecraft "
                     "programs and spacious facilities support the mission.")
        self.assertEqual(
            lexicon.find_terms(aerospace, lexicon.FOCI_EVENT_TERMS), [])

    def test_real_acquisition_vehicle_still_matches(self):
        hits = dict(lexicon.find_terms(
            "completed a special purpose acquisition transaction",
            lexicon.FOCI_EVENT_TERMS))
        self.assertIn("special purpose acquisition", hits)

    def test_generic_english_does_not_fire(self):
        for phrase in ["a team led by Dr. Chen",
                       "the Series A aircraft variant",
                       "fuel pipe inspection"]:
            self.assertEqual(
                lexicon.find_terms(phrase, lexicon.FOCI_EVENT_TERMS), [],
                f"generic phrase matched: {phrase!r}")


class TestRules(unittest.TestCase):
    def setUp(self):
        self.contracts = [make_contract()]

    def _ctx(self, is_new=False):
        return engine.RuleContext(entity=ENTITY, contracts=self.contracts, is_new=is_new)

    def test_foci_with_transaction_scores_higher_than_bare_mention(self):
        deal = Document(source="web", key="a", text=(
            "Vector Photonics today announced a strategic investment led by "
            "Silk Road Capital Partners Ltd, a British Virgin Islands company, "
            "which will take a board seat."))
        bare = Document(source="web", key="b", text=(
            "Our components are also sold to customers in the British Virgin Islands."))
        deal_signals = engine.rule_foci_jurisdiction(deal, self._ctx())
        bare_signals = engine.rule_foci_jurisdiction(bare, self._ctx())
        self.assertTrue(deal_signals)
        self.assertTrue(bare_signals)
        self.assertGreater(max(s.score for s in deal_signals),
                           max(s.score for s in bare_signals))

    def test_ip_collateral_strong_vs_weak(self):
        strong = Document(source="sec_edgar", key="c", text=(
            "The Borrower entered into an Intellectual Property Security Agreement "
            "granting the Collateral Agent a first priority lien on all patents."))
        weak = Document(source="sec_edgar", key="d", text=(
            "The Company maintains a revolving credit facility for working capital."))
        s_strong = engine.rule_ip_collateral(strong, self._ctx())
        s_weak = engine.rule_ip_collateral(weak, self._ctx())
        self.assertTrue(s_strong)
        self.assertTrue(s_weak)
        self.assertGreater(s_strong[0].score, s_weak[0].score * 1.5)
        self.assertIn("252.227-7013", " ".join(self.contracts[0].ip_clause_hits))

    def test_ip_clause_multiplier_applies(self):
        doc = Document(source="sec_edgar", key="e", text=(
            "Patent Security Agreement dated March 3, granting a security "
            "interest in intellectual property."))
        with_clause = engine.rule_ip_collateral(doc, self._ctx())[0].score
        plain = make_contract(description="Janitorial services.", )
        plain.ip_clause_hits = []
        ctx_plain = engine.RuleContext(entity=ENTITY, contracts=[plain])
        without = engine.rule_ip_collateral(doc, ctx_plain)[0].score
        self.assertGreater(with_clause, without)

    def test_uspto_security_interest_uses_metadata_not_prose(self):
        doc = Document(source="uspto", key="f", text="reel 12345 frame 678",
                       meta={"conveyance": "SECURITY INTEREST",
                             "assignee": "Orient Star Holdings Ltd",
                             "assignee_address": "Road Town, Tortola, British Virgin Islands",
                             "patent_count": 14})
        signals = engine.rule_uspto_security_interest(doc, self._ctx())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].jurisdiction, "British Virgin Islands")
        self.assertIn(signals[0].severity, ("high", "critical"))

    def test_structural_country_rule(self):
        c = make_contract(country_of_incorporation="VGB", recipient_country="USA")
        signals = engine.rule_contract_structural(c, self._ctx())
        self.assertTrue(any(s.jurisdiction == "British Virgin Islands" for s in signals))

    def test_distant_cooccurrence_does_not_escalate(self):
        """The Lockheed case: a missile range in the Marshall Islands on page 1
        and unrelated deal vocabulary far away is not a FOCI event."""
        text = ("A missile launched from a range in the Marshall Islands hurtled "
                "across the Pacific. " + ("Historical narrative filler. " * 120)
                + "Separately, the company announced a joint venture.")
        signals = engine.rule_foci_jurisdiction(
            Document(source="web", key="hist", doc_type="press", text=text), self._ctx())
        self.assertTrue(signals)
        self.assertIn(signals[0].severity, ("info", "low"),
                      "distant co-occurrence must stay a bare mention")

    def test_nearby_cooccurrence_does_escalate(self):
        text = ("The company announced a joint venture with a Marshall Islands "
                "entity that acquires a controlling interest.")
        signals = engine.rule_foci_jurisdiction(
            Document(source="web", key="deal", doc_type="press", text=text), self._ctx())
        self.assertTrue(signals)
        self.assertGreaterEqual(signals[0].severity_rank(), 2)  # medium or above

    def test_new_evidence_scores_higher(self):
        doc = Document(source="web", key="g", text=(
            "Announced a change of control transaction with a Shenzhen investor."))
        old = engine.rule_foci_jurisdiction(doc, self._ctx(is_new=False))
        new = engine.rule_foci_jurisdiction(doc, self._ctx(is_new=True))
        self.assertGreater(max(s.score for s in new), max(s.score for s in old))


class TestEvidenceQuality(unittest.TestCase):
    """Risk-factor boilerplate must not score like a completed transaction."""

    def setUp(self):
        self.contracts = [make_contract()]
        self.ctx = engine.RuleContext(entity=ENTITY, contracts=self.contracts)

    def test_risk_factor_scores_far_below_an_executed_agreement(self):
        risk_factor = Document(
            source="sec_edgar", key="rf", doc_type="10-K",
            text=("If we were to become insolvent, or if we may be required to "
                  "divest a business, there can be no assurance that our "
                  "intellectual property would be unaffected."))
        executed = Document(
            source="sec_edgar", key="ex", doc_type="8-K",
            text=("The Grantor hereby grants to the Collateral Agent a security "
                  "interest in intellectual property, pursuant to the "
                  "Intellectual Property Security Agreement dated today."))
        rf = engine.rule_ip_transfer(risk_factor, self.ctx)
        ex = engine.rule_ip_collateral(executed, self.ctx)
        self.assertTrue(rf and ex)
        self.assertLess(rf[0].score, ex[0].score / 3)
        self.assertIn(rf[0].severity, ("info", "low"))
        self.assertGreater(ex[0].severity_rank(), rf[0].severity_rank())

    def test_event_of_default_boilerplate_is_damped(self):
        """Every investment-grade revolver defines default to include bankruptcy.

        Reading that as a distress signal flags healthy primes, which is how a
        screening feed loses its audience.
        """
        boilerplate = Document(
            source="sec_edgar", key="eod", doc_type="8-K",
            text=("An Event of Default includes the bankruptcy or insolvency of "
                  "the Company or a Material Subsidiary (as defined in the "
                  "364-Day Revolving Credit Agreement)."))
        actual = Document(
            source="sec_edgar", key="real", doc_type="8-K",
            text=("On March 3 the Company filed a voluntary petition and entered "
                  "receivership; a 363 sale of assets is contemplated."))
        weak = engine.rule_ip_transfer(boilerplate, self.ctx)
        strong = engine.rule_ip_transfer(actual, self.ctx)
        self.assertTrue(weak and strong)
        self.assertLess(weak[0].score, strong[0].score / 2)
        self.assertIn("event-of-default clause", weak[0].rationale)

    def test_unsecured_revolver_is_a_weak_ip_collateral_match(self):
        doc = Document(source="sec_edgar", key="rev", doc_type="8-K",
                       text=("Bank of America, N.A., as administrative agent under "
                             "the 364-Day Revolving Credit Agreement."))
        signals = engine.rule_ip_collateral(doc, self.ctx)
        self.assertTrue(signals)
        self.assertIn(signals[0].severity, ("info", "low"))
        self.assertIn("Weak match", signals[0].rationale)

    def test_caveat_is_explained_to_the_reader(self):
        doc = Document(source="sec_edgar", key="rf2", doc_type="10-K",
                       text="We may divest certain assets from time to time.")
        signals = engine.rule_ip_transfer(doc, self.ctx)
        self.assertTrue(signals)
        self.assertIn("Weight reduced", signals[0].rationale)
        self.assertIn("periodic report", signals[0].rationale)
        self.assertIn("conditionally", signals[0].rationale)

    def test_8k_exhibit_is_not_damped(self):
        doc = Document(source="sec_edgar", key="ex2", doc_type="8-K",
                       text=("Patent Security Agreement executed and delivered; "
                             "the Grantor pledged intellectual property to the "
                             "Collateral Agent."))
        weight, caveat = engine._evidence_weight(doc, doc.text)
        self.assertEqual(weight, 1.0)
        self.assertEqual(caveat, "")

    def test_foci_in_risk_factors_is_damped_too(self):
        plain = Document(source="web", key="p", doc_type="press",
                         text="We completed a minority stake sale to a Cayman Islands fund.")
        hedged = Document(source="sec_edgar", key="h", doc_type="10-K",
                          text=("We may in the future pursue a minority stake sale, "
                                "potentially with a Cayman Islands fund."))
        s_plain = engine.rule_foci_jurisdiction(plain, self.ctx)
        s_hedged = engine.rule_foci_jurisdiction(hedged, self.ctx)
        self.assertTrue(s_plain and s_hedged)
        self.assertGreater(s_plain[0].score, s_hedged[0].score * 3)


class TestCorrelation(unittest.TestCase):
    def test_compound_signal_emitted_and_dominant(self):
        contracts = [make_contract()]
        foci = Document(source="web", key="h", text=(
            "Strategic investment by a Cayman Islands fund; the investor receives "
            "a board seat."))
        ip = Document(source="sec_edgar", key="i", text=(
            "Intellectual Property Security Agreement granting a security interest "
            "in intellectual property to the Collateral Agent."))
        ctx_entity = ENTITY
        signals = engine.evaluate_documents(
            [(foci, None), (ip, None)], ctx_entity, contracts)
        finding = engine.build_finding(ctx_entity, contracts, signals)
        rule_ids = {s.rule_id for s in finding.signals}
        self.assertIn("COMPOUND-01", rule_ids)
        self.assertIn(finding.severity, ("high", "critical"))

    def test_bare_mention_cannot_trigger_compound(self):
        """A geographic mention plus generic financing vocabulary is an
        ordinary company, not a compound risk."""
        contracts = [make_contract()]
        geographic = Document(
            source="web", key="geo", doc_type="press",
            text=("A missile launched from a range in the Marshall Islands. "
                  + "Historical filler. " * 100))
        generic_credit = Document(
            source="sec_edgar", key="cred", doc_type="8-K",
            text=("Bank of America, N.A., as administrative agent under the "
                  "revolving credit facility."))
        signals = engine.evaluate_documents(
            [(geographic, None), (generic_credit, None)], ENTITY, contracts)
        self.assertIn("FOCI-MENTION-01", {s.rule_id for s in signals})
        finding = engine.build_finding(ENTITY, contracts, signals)
        self.assertNotIn("COMPOUND-01", {s.rule_id for s in finding.signals})
        self.assertIn(finding.severity, ("info", "low", "medium"))

    def test_no_compound_without_both_families(self):
        contracts = [make_contract()]
        only_ip = Document(source="sec_edgar", key="j", text=(
            "Patent Security Agreement with First National Bank as agent."))
        signals = engine.evaluate_documents([(only_ip, None)], ENTITY, contracts)
        finding = engine.build_finding(ENTITY, contracts, signals)
        self.assertNotIn("COMPOUND-01", {s.rule_id for s in finding.signals})

    def test_identical_evidence_is_deduped(self):
        contracts = [make_contract()]
        docs = [(Document(source="web", key=f"k{i}",
                          text="Minority stake acquired by a Seychelles vehicle."), None)
                for i in range(8)]
        signals = engine.evaluate_documents(docs, ENTITY, contracts)
        finding = engine.build_finding(ENTITY, contracts, signals)
        seychelles = [s for s in finding.signals if s.jurisdiction == "Seychelles"]
        self.assertEqual(len(seychelles), 1, "repeat sightings must collapse to one signal")

    def test_many_weak_signals_cannot_manufacture_a_critical(self):
        """The corroboration cap: severity may exceed the worst individual
        signal by at most one band, and only when two signals reach it."""
        contracts = [make_contract()]
        signals = [
            engine.Signal(rule_id=f"X{i}", category="IP_TRANSFER", severity="low",
                          score=6.0, title=f"weak {i}", rationale="r",
                          evidence=f"distinct evidence {i}", source="web")
            for i in range(12)
        ]
        finding = engine.build_finding(ENTITY, contracts, signals)
        self.assertGreater(finding.total_score, 6.0)
        self.assertIn(finding.severity, ("low", "medium"),
                      "twelve low signals must not become high/critical")

    def test_a_single_strong_signal_still_lands(self):
        contracts = [make_contract()]
        signals = [engine.Signal(rule_id="S1", category="IP_COLLATERAL",
                                 severity="critical", score=34.0,
                                 title="executed IP security agreement",
                                 rationale="r", evidence="e", source="sec_edgar")]
        finding = engine.build_finding(ENTITY, contracts, signals)
        self.assertEqual(finding.severity, "critical")

    def test_scoring_has_diminishing_returns(self):
        """Many distinct medium signals must not outrank one critical."""
        contracts = [make_contract()]
        places = ["Seychelles", "Panama", "Belize", "Mauritius", "Bahamas",
                  "Gibraltar", "Cyprus", "Malta"]
        docs = [(Document(source="web", key=f"k{i}",
                          text=f"Minority stake acquired by a {p} vehicle."), None)
                for i, p in enumerate(places)]
        signals = engine.evaluate_documents(docs, ENTITY, contracts)
        finding = engine.build_finding(ENTITY, contracts, signals)
        self.assertGreater(len(finding.signals), 4)
        self.assertLess(finding.total_score, sum(s.score for s in finding.signals))


class TestChangeDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.tmp.name) / "t.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_new_then_unchanged_then_modified(self):
        doc = Document(source="web", key="acme/news", text="Line one\nLine two")
        self.assertEqual(self.store.observe(doc).kind, "new")
        self.assertEqual(self.store.observe(doc).kind, "unchanged")

        doc2 = Document(source="web", key="acme/news",
                        text="Line one\nLine two\nStrategic investment from a BVI fund")
        change = self.store.observe(doc2)
        self.assertEqual(change.kind, "modified")
        self.assertIn("BVI", change.added_text)
        self.assertNotIn("Line one", change.added_text)
        self.assertTrue(change.is_material)

    def test_added_text_isolates_the_delta(self):
        delta = added_text("a\nb\nc", "a\nb\nc\nd")
        self.assertEqual(delta.strip(), "d")

    def test_rules_run_on_delta_only(self):
        """The whole point: steady-state boilerplate must not re-fire."""
        boilerplate = ("We operate globally including in the Cayman Islands.\n"
                       "Contact us for details.")
        doc = Document(source="web", key="x/legal", text=boilerplate)
        self.store.observe(doc)

        updated = Document(source="web", key="x/legal", text=(
            boilerplate + "\nToday we completed a change of control transaction."))
        change = self.store.observe(updated)
        signals = engine.evaluate_documents([(updated, change)], ENTITY, [make_contract()])
        evidence = " ".join(s.evidence for s in signals)
        self.assertIn("change of control", evidence)
        self.assertNotIn("Contact us for details", evidence)
        # The Cayman nexus came from unchanged context, so it must be reported
        # as the standing-nexus variant, not as a brand-new disclosure.
        self.assertIn("FOCI-JURIS-02", {s.rule_id for s in signals})
        self.assertEqual({s.jurisdiction for s in signals}, {"Cayman Islands"})

    def test_context_fallback_needs_an_event(self):
        """A standing foreign nexus alone must not fire on unrelated edits."""
        boilerplate = "We operate globally including in the Cayman Islands."
        doc = Document(source="web", key="y/legal", text=boilerplate)
        self.store.observe(doc)
        updated = Document(source="web", key="y/legal",
                           text=boilerplate + "\nWe updated our office hours.")
        change = self.store.observe(updated)
        signals = engine.evaluate_documents([(updated, change)], ENTITY, [make_contract()])
        self.assertEqual([s for s in signals if s.category == "FOCI"], [])


class TestRendering(unittest.TestCase):
    def _finding(self):
        contracts = [make_contract()]
        docs = [
            (Document(source="web", key="m", url="https://example.com/news",
                      text="Strategic investment led by a British Virgin Islands fund "
                           "which receives a board seat."), None),
            (Document(source="sec_edgar", key="n", url="https://sec.gov/x",
                      text="Intellectual Property Security Agreement granting a "
                           "security interest in intellectual property."), None),
        ]
        signals = engine.evaluate_documents(docs, ENTITY, contracts)
        signals += engine.evaluate_contracts(contracts, ENTITY)
        return engine.build_finding(ENTITY, contracts, signals, run_id="testrun")

    def test_subject_carries_severity_and_piid(self):
        finding = self._finding()
        subject = render.subject_for(finding)
        self.assertIn(finding.severity.upper(), subject)
        self.assertIn("N0001925C0042", subject)
        self.assertIn("FOCI/IP", subject, "both families present -> combined tag")

    def test_body_has_rationale_evidence_and_disclaimer(self):
        body = render.render_text(self._finding(), "testrun")
        self.assertIn("Why flagged:", body)
        self.assertIn("Source text:", body)
        self.assertIn("SUGGESTED VERIFICATION STEPS", body)
        self.assertIn("not a FOCI determination", body)
        self.assertIn("32 CFR Part 117", body)
        self.assertIn("DFARS 252.227-7013", body)

    def test_addresses_the_resolved_ko(self):
        finding = self._finding()
        self.assertEqual(finding.top_officer().email, "jane.doe.n00019@us.navy.mil")
        self.assertIn("Dear Jane Doe", render.render_text(finding))

    def test_eml_is_written_and_parseable(self):
        import email

        with tempfile.TemporaryDirectory() as tmp:
            finding = self._finding()
            path = render.write_eml(finding, tmp, sender="me@example.gov",
                                    recipient="jane.doe.n00019@us.navy.mil",
                                    run_id="testrun")
            self.assertTrue(path.is_file())
            msg = email.message_from_bytes(path.read_bytes())
            self.assertEqual(msg["To"], "jane.doe.n00019@us.navy.mil")
            self.assertIn(finding.severity.upper(), msg["Subject"])
            self.assertEqual(msg["X-FOCI-Severity"], finding.severity)
            self.assertEqual(msg["X-FOCI-Run"], "testrun")


class TestGmailGuards(unittest.TestCase):
    def test_default_config_does_not_send(self):
        from foci_screen.config import Config
        from foci_screen.notify.gmail import GmailNotifier, status_banner

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(out_dir=tmp, gmail_enabled=False, gmail_send=False)
            self.assertIn("OFF", status_banner(cfg))
            contracts = [make_contract()]
            finding = engine.build_finding(
                ENTITY, contracts,
                engine.evaluate_contracts(contracts, ENTITY), run_id="r1")
            result = GmailNotifier(cfg).deliver(finding, run_id="r1")
            self.assertEqual(result.status, "rendered")
            self.assertTrue(Path(result.path).is_file())

    def test_redirect_overrides_recipient(self):
        from foci_screen.config import Config
        from foci_screen.notify.gmail import GmailNotifier

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(out_dir=tmp, gmail_enabled=False,
                         email_redirect_to="analyst@example.gov")
            contracts = [make_contract()]
            finding = engine.build_finding(
                ENTITY, contracts,
                engine.evaluate_contracts(contracts, ENTITY), run_id="r2")
            result = GmailNotifier(cfg).deliver(finding, run_id="r2")
            self.assertEqual(result.recipient, "analyst@example.gov")

    def test_unresolved_ko_is_suppressed_not_guessed(self):
        from foci_screen.config import Config
        from foci_screen.notify.gmail import GmailNotifier

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(out_dir=tmp, gmail_enabled=False)
            contracts = [make_contract(officer=ContractingOfficer())]
            finding = engine.build_finding(
                ENTITY, contracts,
                engine.evaluate_contracts(contracts, ENTITY), run_id="r3")
            result = GmailNotifier(cfg).deliver(finding, run_id="r3")
            self.assertEqual(result.status, "suppressed")
            self.assertEqual(result.recipient, "")


class TestFPDSParsing(unittest.TestCase):
    def test_officer_email_extracted_and_ranked(self):
        from xml.etree import ElementTree as ET

        from foci_screen.connectors.fpds import FPDSConnector

        xml = """<entry xmlns="http://www.w3.org/2005/Atom">
          <content><award>
            <transactionInformation>
              <createdBy>CLERK.SMITH.N00019@JSF.MIL</createdBy>
              <lastModifiedBy>JANE.DOEBAKEWELL.N00019@JSF.MIL</lastModifiedBy>
              <approvedBy>KO.WARRANT.N00019@JSF.MIL</approvedBy>
            </transactionInformation>
            <purchaserInformation><contractingOfficeID>N00019</contractingOfficeID></purchaserInformation>
          </award></content></entry>"""
        entry = ET.fromstring(xml)
        officer = FPDSConnector(None)._officer_from_entry(entry)
        self.assertEqual(officer.email, "ko.warrant.n00019@jsf.mil")   # approver wins
        self.assertEqual(officer.confidence, "high")
        self.assertEqual(officer.name, "Ko Warrant")

    def test_falls_back_to_last_modifier(self):
        from xml.etree import ElementTree as ET

        from foci_screen.connectors.fpds import FPDSConnector

        xml = """<entry xmlns="http://www.w3.org/2005/Atom"><content><award>
            <transactionInformation>
              <lastModifiedBy>JANE.DOEBAKEWELL.N00019@JSF.MIL</lastModifiedBy>
            </transactionInformation></award></content></entry>"""
        officer = FPDSConnector(None)._officer_from_entry(ET.fromstring(xml))
        self.assertEqual(officer.confidence, "medium")
        self.assertEqual(officer.name, "Jane Doebakewell")


class TestWebNormalisation(unittest.TestCase):
    def test_volatile_content_is_stripped(self):
        from foci_screen.connectors.webwatch import normalise_page

        page_a = ("<html><body><main><p>Investor News</p>"
                  "<p>Updated 10:31 AM</p><p>© 2025 Acme</p>"
                  "<p>Acme announces Q3 results.</p></main></body></html>")
        page_b = page_a.replace("10:31 AM", "11:47 AM")
        self.assertEqual(normalise_page(page_a), normalise_page(page_b))

    def test_real_change_is_preserved(self):
        from foci_screen.connectors.webwatch import normalise_page

        a = "<html><body><main><p>Acme announces Q3 results.</p></main></body></html>"
        b = ("<html><body><main><p>Acme announces Q3 results.</p>"
             "<p>Acme announces investment from Cayman partner.</p></main></body></html>")
        self.assertNotEqual(normalise_page(a), normalise_page(b))
        self.assertIn("Cayman", normalise_page(b))


class StubHttp:
    """Deterministic HTTP double: maps a URL substring to a canned response."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []
        self.stats = {"hits": 0, "misses": 0, "errors": 0}

    def get(self, url, params=None, **kw):
        self.calls.append((url, params or {}))
        for needle, payload in self.routes.items():
            if needle in url:
                return payload
        return {"status": 404, "text": "", "json": None, "url": url}

    def post(self, url, **kw):
        return self.get(url, **kw)


class TestAttributionSafety(unittest.TestCase):
    """Regression guards for the worst failure mode this tool has.

    A screen that attributes another registrant's IP security agreement to the
    contractor under review, or that matches its own search phrase as evidence,
    is worse than no screen at all: it emails a federal contracting officer a
    confident, wrong allegation about a named company.
    """

    def test_connector_narration_never_triggers_a_prose_rule(self):
        """Text the tool writes about a filing must not be read as evidence.

        Item labels like "Bankruptcy or Receivership" contain vocabulary the
        prose rules look for. The connector emits codes only; the meaning is
        applied by the structured rule, which is tested separately below.
        """
        from foci_screen.connectors.sec_edgar import ITEM_MEANING

        contracts = [make_contract()]
        narrations = [
            Document(source="sec_edgar", key=f"n{i}",
                     text=(f"Form 8-K filed 2025-01-01 by Acme Corp. "
                           f"Reported items: {code}."))
            for i, code in enumerate(ITEM_MEANING)
        ]
        signals = engine.evaluate_documents([(d, None) for d in narrations],
                                            ENTITY, contracts)
        prose_rules = {"FOCI-JURIS-01", "FOCI-JURIS-02", "IPCOL-01", "IPXFER-01"}
        offenders = [s for s in signals if s.rule_id in prose_rules]
        self.assertEqual(offenders, [], f"narration matched a prose rule: {offenders}")

    def test_item_labels_are_not_emitted_into_document_text(self):
        """The connector-level half of the guard above."""
        from foci_screen.connectors.sec_edgar import EdgarConnector

        submissions = {"name": "Acme Corp", "filings": {"recent": {
            "form": ["8-K"], "accessionNumber": ["0000936468-25-000001"],
            "filingDate": ["2025-04-01"], "items": ["1.03,5.01"],
            "primaryDocument": ["a8k.htm"]}}}
        http = StubHttp({"data.sec.gov": {"status": 200, "json": submissions,
                                          "text": "", "url": ""}})
        docs = EdgarConnector(http).recent_filings("0000936468")
        self.assertEqual(len(docs), 1)
        self.assertNotIn("Receivership", docs[0].text)
        self.assertNotIn("Changes in Control", docs[0].text)
        self.assertEqual(docs[0].meta["item_codes"], ["1.03", "5.01"])

    def test_8k_item_codes_fire_the_structured_rule(self):
        """The meaning must still be caught — just through metadata."""
        doc = Document(source="sec_edgar", key="s1", url="https://sec.gov/x",
                       published="2025-04-01",
                       text="Form 8-K filed 2025-04-01 by Acme Corp. Reported items: 1.03, 5.01.",
                       meta={"item_codes": ["1.03", "5.01"], "company": "Acme Corp"})
        ctx = engine.RuleContext(entity=ENTITY, contracts=[make_contract()])
        signals = engine.rule_edgar_8k_items(doc, ctx)
        by_id = {s.rule_id: s for s in signals}
        self.assertIn("EDGAR-8K-5.01", by_id)
        self.assertIn("EDGAR-8K-1.03", by_id)
        self.assertEqual(by_id["EDGAR-8K-5.01"].category, "FOCI")
        self.assertEqual(by_id["EDGAR-8K-1.03"].category, "IP_TRANSFER")
        self.assertIn("FAR 42.12", by_id["EDGAR-8K-5.01"].rationale)

    def test_unknown_item_codes_are_ignored(self):
        doc = Document(source="sec_edgar", key="s2",
                       text="Reported items: 7.01, 9.01.",
                       meta={"item_codes": ["7.01", "9.01"], "company": "Acme"})
        ctx = engine.RuleContext(entity=ENTITY, contracts=[make_contract()])
        self.assertEqual(engine.rule_edgar_8k_items(doc, ctx), [])

    def test_full_text_search_requires_a_cik(self):
        """Without a CIK the phrase query cannot be attributed, so return nothing."""
        from foci_screen.connectors.sec_edgar import EdgarConnector

        http = StubHttp({})
        docs = EdgarConnector(http).full_text_search("Lockheed Martin", cik="")
        self.assertEqual(docs, [])
        self.assertEqual(http.calls, [], "must not even issue the query")

    def test_full_text_search_discards_other_registrants(self):
        from foci_screen.connectors.sec_edgar import EdgarConnector

        hit = {"hits": {"hits": [
            {"_id": "0001-23-000001:ex10.htm",
             "_source": {"ciks": ["0000999999"], "display_names": ["Someone Else Inc"],
                         "form": "8-K", "file_date": "2025-02-02"}}]}}
        http = StubHttp({
            "efts.sec.gov": {"status": 200, "json": hit, "text": "", "url": ""},
            "Archives": {"status": 200, "text": "<p>Patent Security Agreement</p>",
                         "json": None, "url": ""},
        })
        docs = EdgarConnector(http).full_text_search("Acme", cik="0000936468")
        self.assertEqual(docs, [], "hit belongs to a different CIK and must be dropped")

    def test_full_text_search_keeps_own_filing_with_real_body(self):
        from foci_screen.connectors.sec_edgar import EdgarConnector

        hit = {"hits": {"hits": [
            {"_id": "0000936468-25-000009:ex10-1.htm",
             "_source": {"ciks": ["0000936468"], "display_names": ["Acme Corp"],
                         "form": "8-K", "file_date": "2025-03-03",
                         "file_type": "EX-10.1"}}]}}
        body = ("<html><body>INTELLECTUAL PROPERTY SECURITY AGREEMENT dated as of "
                "March 3, 2025, granting the Collateral Agent a first priority lien "
                "on all patents of the Grantor.</body></html>")
        http = StubHttp({
            "efts.sec.gov": {"status": 200, "json": hit, "text": "", "url": ""},
            "Archives": {"status": 200, "text": body, "json": None, "url": ""},
        })
        docs = EdgarConnector(http).full_text_search("Acme", cik="0000936468")
        self.assertEqual(len(docs), 1)
        doc = docs[0]
        self.assertIn("first priority lien", doc.text)
        self.assertNotIn("EDGAR full-text hit", doc.text)
        self.assertTrue(doc.meta["verified_registrant"])
        signals = engine.rule_ip_collateral(
            doc, engine.RuleContext(entity=ENTITY, contracts=[make_contract()]))
        self.assertTrue(signals)
        self.assertIn("first priority lien", signals[0].evidence)

    def test_body_that_cannot_be_fetched_yields_no_document(self):
        from foci_screen.connectors.sec_edgar import EdgarConnector

        hit = {"hits": {"hits": [
            {"_id": "0000936468-25-000009:ex10-1.htm",
             "_source": {"ciks": ["0000936468"], "display_names": ["Acme Corp"],
                         "form": "8-K", "file_date": "2025-03-03"}}]}}
        http = StubHttp({"efts.sec.gov": {"status": 200, "json": hit, "text": "",
                                          "url": ""}})
        self.assertEqual(EdgarConnector(http).full_text_search("Acme", cik="0000936468"),
                         [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
