"""What FPDS carries from a vendor's SAM registration, and what the screen does
with it. The fixture is cut down from a live FPDS award to Boeing."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from foci_screen.connectors.fpds import FPDSConnector, _registration
from foci_screen.models import Contract, Entity
from foci_screen.risk import engine

ENTRY = """<entry xmlns="http://www.w3.org/2005/Atom"><content>
<ns1:award xmlns:ns1="https://www.fpds.gov/FPDS">
 <ns1:vendor><ns1:vendorHeader><ns1:vendorName>BOEING COMPANY, THE</ns1:vendorName>
 </ns1:vendorHeader><ns1:vendorSiteDetails>
  <ns1:vendorBusinessTypes><ns1:businessOrOrganizationType>
   <ns1:isForeignGovernment>{foreign_gov}</ns1:isForeignGovernment>
  </ns1:businessOrOrganizationType>
  <ns1:isSolePropreitorship>{sole}</ns1:isSolePropreitorship>
  </ns1:vendorBusinessTypes>
  <ns1:vendorOrganizationFactors>
   <ns1:organizationalType>CORPORATE NOT TAX EXEMPT</ns1:organizationalType>
  </ns1:vendorOrganizationFactors>
  <ns1:vendorUEIInformation><ns1:UEI>{uei}</ns1:UEI>
   <ns1:UEILegalBusinessName>BOEING COMPANY, THE</ns1:UEILegalBusinessName>
   <ns1:cageCode>76301</ns1:cageCode></ns1:vendorUEIInformation>
 </ns1:vendorSiteDetails></ns1:vendor>
 <ns1:signedDate>2024-01-01 00:00:00</ns1:signedDate>
</ns1:award></content></entry>"""


def entry(sole="false", foreign_gov="false", uei="JJM4FRDZJDX1"):
    return ET.fromstring(ENTRY.format(sole=sole, foreign_gov=foreign_gov, uei=uei))


def test_registration_fields_are_read_from_an_award():
    reg = _registration(entry())
    assert reg == {"cage": "76301", "sole_proprietor": False, "foreign_government": False,
                   "organizational_type": "CORPORATE NOT TAX EXEMPT",
                   "name": "BOEING COMPANY, THE"}


def test_fpds_misspelling_is_what_is_read():
    """FPDS spells it isSolePropreitorship. Reading the correct spelling only
    would leave every sole proprietor unflagged."""
    assert _registration(entry(sole="true"))["sole_proprietor"] is True


def test_an_absent_flag_is_unknown_not_false():
    xml = ENTRY.replace("<ns1:isSolePropreitorship>{sole}</ns1:isSolePropreitorship>", "")
    e = ET.fromstring(xml.format(foreign_gov="false", uei="X"))
    assert _registration(e)["sole_proprietor"] is None


class StubHttp:
    def __init__(self, text):
        self.text, self.calls = text, []

    def get(self, url, params=None, **kw):
        self.calls.append(params or {})
        return {"status": 200, "text": self.text}


def feed(*entries):
    return "<feed xmlns='http://www.w3.org/2005/Atom'>" + "".join(
        ET.tostring(e, encoding="unicode") for e in entries) + "</feed>"


def test_a_vendor_is_looked_up_by_its_own_uei():
    http = StubHttp(feed(entry(sole="true", uei="U1")))
    reg = FPDSConnector(http).vendor_registration("U1")
    assert reg["sole_proprietor"] is True
    assert http.calls[0]["q"] == 'VENDOR_UEI:"U1"'


def test_records_for_another_vendor_are_not_taken_as_this_one():
    http = StubHttp(feed(entry(sole="true", uei="SOMEONEELSE")))
    assert FPDSConnector(http).vendor_registration("U1") is None


def test_no_uei_means_no_lookup():
    http = StubHttp(feed())
    assert FPDSConnector(http).vendor_registration("") is None
    assert http.calls == []


def test_enrich_copies_the_registration_onto_a_prime_award():
    http = StubHttp(feed(entry(sole="false", foreign_gov="true")))
    c = FPDSConnector(http).enrich(Contract(award_id="A1", piid="A1"))
    assert (c.cage_code, c.is_sole_proprietor, c.is_foreign_government) == (
        "76301", False, True)


def test_enrich_does_not_copy_the_prime_onto_a_subaward():
    http = StubHttp(feed(entry(sole="true", foreign_gov="true")))
    c = FPDSConnector(http).enrich(Contract(award_id="S1", piid="S1", is_subaward=True,
                                            prime_award_id="A1"))
    assert (c.cage_code, c.is_sole_proprietor, c.is_foreign_government) == (
        "", None, False)


def test_a_foreign_government_awardee_fires():
    contract = Contract(award_id="A1", piid="A1", is_foreign_government=True)
    signals = engine.evaluate_contracts([contract], Entity(name="MINISTRY OF DEFENCE"))
    assert [s.rule_id for s in signals] == ["STRUCT-FOREIGN-GOV-01"]


def test_an_ordinary_awardee_does_not():
    signals = engine.evaluate_contracts([Contract(award_id="A1")], Entity(name="ACME"))
    assert "STRUCT-FOREIGN-GOV-01" not in [s.rule_id for s in signals]
