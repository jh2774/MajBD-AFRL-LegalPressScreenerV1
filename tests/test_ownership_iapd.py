"""Offline tests: 5%+ holders from Schedule 13D/13G, and their IAPD records.

The fixtures are cut down from live responses: a 2026 structured Schedule 13G
for Boeing, the SGML header of Lockheed's own 13D on Terran Orbital, and IAPD
search results as the endpoint actually returns them — loose single-word
matches included.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foci_screen.connectors import edgar_codes  # noqa: E402
from foci_screen.connectors.registries import IAPDConnector  # noqa: E402
from foci_screen.connectors.sec_edgar import EdgarConnector  # noqa: E402
from foci_screen.models import Contract, Document, Entity  # noqa: E402
from foci_screen.pipeline import Screener, ScreenOptions  # noqa: E402
from foci_screen.risk import engine  # noqa: E402

CIK = "0000012927"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/12927"


class StubHttp:
    """Maps a URL substring to a canned response; the longest match wins."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params or {}))
        for needle in sorted(self.routes, key=len, reverse=True):
            if needle in url:
                return self.routes[needle]
        return {"status": 404, "text": "", "json": None, "url": url}


def ok(text="", json=None):
    return {"status": 200, "text": text, "json": json}


def person(name, place, kind, percent):
    return f"""
  <coverPageHeaderReportingPersonDetails>
   <reportingPersonName>{name}</reportingPersonName>
   <citizenshipOrOrganization>{place}</citizenshipOrOrganization>
   <classPercent>{percent}</classPercent>
   <typeOfReportingPerson>{kind}</typeOfReportingPerson>
  </coverPageHeaderReportingPersonDetails>"""


def schedule_xml(*persons, issuer=CIK):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13g"
                 xmlns:com="http://www.sec.gov/edgar/common">
 <formData>
  <coverPageHeader>
   <issuerInfo><issuerCik>{issuer}</issuerCik><issuerName>BOEING CO</issuerName></issuerInfo>
  </coverPageHeader>{''.join(persons)}
 </formData>
</edgarSubmission>"""


def header(subject_cik, subject, filer, incorporated):
    return f"""<SEC-HEADER>
<TYPE>SC 13D/A
<SUBJECT-COMPANY>
<COMPANY-DATA>
<CONFORMED-NAME>{subject}
<CIK>{subject_cik}
</COMPANY-DATA>
</SUBJECT-COMPANY>
<FILED-BY>
<COMPANY-DATA>
<CONFORMED-NAME>{filer}
<CIK>0000999999
<STATE-OF-INCORPORATION>{incorporated}
</COMPANY-DATA>
</FILED-BY>
</SEC-HEADER>"""


def submissions(*rows):
    """rows: (form, filed, accession, primary document)."""
    return ok(json={"name": "BOEING CO", "filings": {"recent": {
        "form": [r[0] for r in rows],
        "filingDate": [r[1] for r in rows],
        "accessionNumber": [r[2] for r in rows],
        "primaryDocument": [r[3] for r in rows],
        "items": ["" for _ in rows]}}})


def xml_route(accession):
    return f"{ARCHIVE}/{accession.replace('-', '')}/primary_doc.xml"


def header_route(accession):
    return f"{ARCHIVE}/{accession.replace('-', '')}/{accession}.hdr.sgml"


def iapd_hit(firm, crd, country, sec_number="801-11953", disclosures="Y"):
    return {"_source": {
        "firm_name": firm, "firm_source_id": crd,
        "firm_ia_full_sec_number": sec_number, "firm_ia_scope": "ACTIVE",
        "firm_ia_disclosure_fl": disclosures,
        "firm_ia_address_details": (
            '{"officeAddress": {"street1": "1 Main St", "city": "X", '
            f'"country": "{country}"}}}}')}}


def iapd_results(*hits):
    return ok(json={"hits": {"hits": list(hits)}})


class TestOwnershipFilings(unittest.TestCase):

    def test_structured_13g_gives_each_holder_with_stake_type_and_place(self):
        acc = "0000315066-26-000359"
        http = StubHttp({
            "submissions/CIK": submissions(
                ("SCHEDULE 13G/A", "2026-02-05", acc, "xslSCHEDULE_13G_X02/primary_doc.xml")),
            xml_route(acc): ok(schedule_xml(
                person("FMR LLC", "DE", "HC", "7.0"),
                person("Abigail P. Johnson", "X1", "IN", "7.0"))),
        })
        docs = EdgarConnector(http).ownership_filings(CIK)
        self.assertEqual([d.meta["holder"] for d in docs], ["FMR LLC", "Abigail P. Johnson"])
        fmr = docs[0].meta
        self.assertEqual((fmr["percent"], fmr["person_type"], fmr["place_code"]),
                         (7.0, "HC", "DE"))
        self.assertEqual(docs[0].doc_type, "SCHEDULE 13G/A")
        self.assertEqual(docs[0].published, "2026-02-05")

    def test_filings_the_registrant_made_about_other_companies_are_ignored(self):
        """Lockheed's list carries its own 13D on Terran Orbital. Read as a
        holder of Lockheed, Lockheed would be investing in itself."""
        own, about_us = "0001123292-24-000328", "0000102909-19-000111"
        http = StubHttp({
            "submissions/CIK": submissions(
                ("SC 13D/A", "2024-11-01", own, "lockheed.htm"),
                ("SC 13G/A", "2019-02-11", about_us, "lmt.txt")),
            header_route(own): ok(header("0001835512", "Terran Orbital Corp",
                                         "BOEING CO", "DE")),
            header_route(about_us): ok(header(CIK, "BOEING CO",
                                              "VANGUARD GROUP INC", "PA")),
        })
        docs = EdgarConnector(http).ownership_filings(CIK)
        self.assertEqual([d.meta["holder"] for d in docs], ["VANGUARD GROUP INC"])
        self.assertIsNone(docs[0].meta["percent"])  # the old form has no cover page
        self.assertEqual(docs[0].meta["place_code"], "PA")

    def test_structured_filing_about_another_issuer_is_ignored(self):
        acc = "0000000001-26-000001"
        http = StubHttp({
            "submissions/CIK": submissions(("SCHEDULE 13D", "2026-01-02", acc, "x.xml")),
            xml_route(acc): ok(schedule_xml(person("Someone Else LP", "E9", "PN", "9"),
                                            issuer="0001835512")),
        })
        self.assertEqual(EdgarConnector(http).ownership_filings(CIK), [])

    def test_a_holder_at_zero_percent_is_not_revived_by_an_older_filing(self):
        """The Vanguard Group files at 0% to say it is below 5%. Its 2019 filing
        under "VANGUARD GROUP INC" must not bring it back as a current holder."""
        new, old = "0000102909-26-000812", "0000102909-19-000111"
        http = StubHttp({
            "submissions/CIK": submissions(
                ("SCHEDULE 13G/A", "2026-03-26", new, "primary_doc.xml"),
                ("SC 13G/A", "2019-02-11", old, "lmt.txt")),
            xml_route(new): ok(schedule_xml(person("The Vanguard Group", "PA", "IA", "0"))),
            header_route(old): ok(header(CIK, "BOEING CO", "VANGUARD GROUP INC", "PA")),
        })
        self.assertEqual(EdgarConnector(http).ownership_filings(CIK), [])

    def test_document_text_never_spells_out_the_place_of_organisation(self):
        """Spelled out, "Cayman Islands" would be read by the prose rule as a
        mention in a filing — the tool matching text it wrote itself."""
        acc = "0000000002-26-000002"
        http = StubHttp({
            "submissions/CIK": submissions(("SCHEDULE 13D", "2026-06-01", acc, "p.xml")),
            xml_route(acc): ok(schedule_xml(
                person("Harbour Peak Holdings Ltd", "E9", "CO", "12.5"))),
        })
        doc = EdgarConnector(http).ownership_filings(CIK)[0]
        self.assertNotIn("Cayman", doc.text)
        self.assertIn("E9", doc.text)

    def test_only_ownership_forms_are_fetched_and_the_limit_holds(self):
        rows = [("8-K", "2026-06-01", "0000000003-26-000003", "a.htm")]
        rows += [("SCHEDULE 13G", "2026-05-01", f"0000000004-26-00000{i}", "p.xml")
                 for i in range(5)]
        http = StubHttp({"submissions/CIK": submissions(*rows)})
        EdgarConnector(http).ownership_filings(CIK, limit=3)
        fetched = [u for u, _ in http.calls if "Archives" in u]
        self.assertEqual(len(fetched), 3)
        self.assertTrue(all(u.endswith("primary_doc.xml") for u in fetched))


class TestAdviserMatching(unittest.TestCase):

    def test_loose_single_word_results_are_not_taken_as_the_holder(self):
        """What the old name search stored for defence contractors."""
        http = StubHttp({"adviserinfo": iapd_results(
            iapd_hit("NAVY FEDERAL INVESTMENT SERVICES, LLC", "1", "United States"),
            iapd_hit("MARINER ADVISOR NETWORK", "2", "United States"))})
        self.assertEqual(IAPDConnector(http).find_adviser("Navy Capital Partners LP"), [])

    def test_an_affiliate_abroad_is_not_the_holder(self):
        """BlackRock, Inc. is not BlackRock Investment Management (UK) Limited;
        taking the affiliate's office country would misstate the holder's."""
        http = StubHttp({"adviserinfo": iapd_results(
            iapd_hit("BLACKROCK INVESTMENT MANAGEMENT (UK) LIMITED", "162379",
                     "United Kingdom"),
            iapd_hit("BLACKROCK (SINGAPORE) LIMITED", "164594", "Singapore"))})
        self.assertEqual(IAPDConnector(http).find_adviser("BlackRock, Inc."), [])

    def test_the_same_firm_under_different_suffixes_matches(self):
        http = StubHttp({"adviserinfo": iapd_results(
            iapd_hit("VANGUARD MARKETING CORP", "9", "United States"),
            iapd_hit("VANGUARD GROUP INC", "105958", "United States"))})
        docs = IAPDConnector(http).find_adviser(
            "The Vanguard Group", percent=9.06, filing="SCHEDULE 13G/A filed 2026-01-30")
        self.assertEqual(len(docs), 1)
        meta = docs[0].meta
        self.assertEqual((meta["crd"], meta["investor"], meta["percent"]),
                         ("105958", "The Vanguard Group", 9.06))
        self.assertEqual(meta["match_score"], 1.0)

    def test_the_stored_record_is_the_same_whichever_contractor_asked(self):
        """The record is one snapshot per adviser. Holder details in its text
        would make each contractor's screen report it as changed."""
        http = StubHttp({"adviserinfo": iapd_results(
            iapd_hit("VANGUARD GROUP INC", "105958", "United States"))})
        iapd = IAPDConnector(http)
        a = iapd.find_adviser("VANGUARD GROUP INC", percent=9.06)[0]
        b = iapd.find_adviser("VANGUARD GROUP INC", percent=6.99)[0]
        self.assertEqual((a.key, a.text), (b.key, b.text))

    def test_a_broker_dealer_without_an_adviser_registration_is_skipped(self):
        http = StubHttp({"adviserinfo": iapd_results(
            iapd_hit("HARBOUR PEAK HOLDINGS", "7", "Cayman Islands", sec_number=None))})
        self.assertEqual(IAPDConnector(http).find_adviser("Harbour Peak Holdings Ltd"), [])


def ownership_doc(place, form="SCHEDULE 13D", percent=12.5, holder="Harbour Peak Holdings Ltd"):
    return Document(
        source="sec_edgar", key=f"13dg:{CIK}:x", title=f"{holder} — {form}",
        url=f"{ARCHIVE}/x/", published="2026-06-01", doc_type=form,
        text=(f"Form {form} filed 2026-06-01 reports {holder} as a beneficial owner, "
              f"{percent}% of the class. Place of organisation (EDGAR code): {place}."),
        meta={"ownership": True, "holder": holder, "form": form, "percent": percent,
              "person_type": "CO", "place_code": place})


class TestOwnershipRules(unittest.TestCase):

    def setUp(self):
        self.ctx = engine.RuleContext(
            entity=Entity(name="BOEING CO", cik=CIK),
            contracts=[Contract(award_id="FA863418C2701")])

    def fired(self, doc):
        return engine.evaluate_documents([(doc, None)], self.ctx.entity, self.ctx.contracts)

    def test_holder_organised_in_a_listed_jurisdiction_is_flagged(self):
        signals = self.fired(ownership_doc("E9"))
        self.assertEqual([s.rule_id for s in signals], ["FOCI-OWNER-01"])
        self.assertEqual(signals[0].jurisdiction, "Cayman Islands")
        self.assertIn("12.5%", signals[0].rationale)

    def test_a_13d_scores_above_a_passive_13g(self):
        active = self.fired(ownership_doc("F4", form="SCHEDULE 13D"))[0]
        passive = self.fired(ownership_doc("F4", form="SCHEDULE 13G"))[0]
        self.assertGreater(active.score, passive.score)
        self.assertIn("passive", passive.rationale)

    def test_edgar_names_the_lexicon_spells_differently_still_resolve(self):
        self.assertEqual(self.fired(ownership_doc("D8"))[0].jurisdiction,
                         "British Virgin Islands")
        self.assertEqual(edgar_codes.place_name("M4"), "North Korea")

    def test_domestic_and_allied_holders_are_not_flagged(self):
        for code in ("DE", "PA", "X1", "Q8", "X0", ""):
            with self.subTest(code=code):
                self.assertEqual(self.fired(ownership_doc(code)), [])

    def test_iapd_record_is_flagged_once_not_also_as_a_bare_mention(self):
        doc = Document(
            source="iapd", key="iapd:7", title="HARBOUR PEAK ADVISERS",
            url="https://adviserinfo.sec.gov/firm/summary/7",
            text=("IAPD firm: HARBOUR PEAK ADVISERS\nSEC number: 801-1\nCRD: 7\n"
                  "Status: ACTIVE\nHas disclosures: N\nOffice: 1 Main St X Cayman Islands\n"
                  "Other names: "),
            meta={"firm_name": "HARBOUR PEAK ADVISERS", "sec_number": "801-1",
                  "crd": "7", "country": "Cayman Islands", "has_disclosures": False,
                  "investor": "Harbour Peak Holdings Ltd", "percent": 12.5,
                  "filing": "SCHEDULE 13D filed 2026-06-01"})
        signals = self.fired(doc)
        self.assertEqual([s.rule_id for s in signals], ["FOCI-IAPD-01"])
        self.assertIn("Harbour Peak Holdings Ltd", signals[0].rationale)
        self.assertIn("12.5%", signals[0].rationale)


class TestGather(unittest.TestCase):
    """The pipeline looks up the holders, not the contractor's own name."""

    def test_holders_are_looked_up_in_iapd_but_individuals_are_not(self):
        acc = "0000315066-26-000359"
        http = StubHttp({
            "submissions/CIK": submissions(
                ("SCHEDULE 13G/A", "2026-02-05", acc, "primary_doc.xml")),
            xml_route(acc): ok(schedule_xml(
                person("FMR LLC", "DE", "HC", "7.0"),
                person("Abigail P. Johnson", "X1", "IN", "7.0"))),
            "adviserinfo": iapd_results(),
        })
        screener = object.__new__(Screener)
        screener.edgar = EdgarConnector(http)
        screener.iapd = IAPDConnector(http)
        screener.ofac = SimpleNamespace(screen=lambda name: [])
        screener.exclusions = SimpleNamespace(screen=lambda *a, **k: [])
        screener.uspto = SimpleNamespace(available=False)
        screener.patent_dataset = SimpleNamespace(available=False)
        screener.sam = SimpleNamespace(available=False)
        screener.alerts = SimpleNamespace(available=lambda: False)
        entity = Entity(name="THE BOEING COMPANY", cik=CIK, aliases=["BOEING CO"])

        screener._gather(entity, ScreenOptions(agency="x", fetch_filing_bodies=False),
                         progress=lambda msg: None)

        asked = [p.get("query") for u, p in http.calls if "adviserinfo" in u]
        self.assertEqual(asked, ["FMR LLC"])


if __name__ == "__main__":
    unittest.main()
