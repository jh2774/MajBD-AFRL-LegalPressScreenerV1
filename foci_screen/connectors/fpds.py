"""FPDS-NG ATOM feed — contracting officer identity and foreign-ownership flags.

This is the connector that makes step 3 possible without a SAM.gov key. FPDS
records the account that created, approved and last modified each contract
action, and for DoD those are the contracting officer's real .mil/.gov
addresses (e.g. JANE.DOE.N00019@JSF.MIL).

Caveats worth knowing before you email anyone:
  * lastModifiedBy is whoever last touched the record, which may be a
    specialist or an administrator rather than the warranted KO;
  * on older actions the person may have moved on.
Both are reflected in ContractingOfficer.confidence and stated in the email
footer so a recipient can redirect rather than be misled.
"""
from __future__ import annotations

import logging
import re
from xml.etree import ElementTree as ET

from ..models import Contract, ContractingOfficer

log = logging.getLogger("foci.fpds")

FEED = "https://www.fpds.gov/ezsearch/FEEDS/ATOM"
ATOM_NS = "{http://www.w3.org/2005/Atom}"

_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _text(elem, path: str) -> str:
    """Namespace-agnostic search: FPDS namespaces vary by feed version."""
    if elem is None:
        return ""
    for node in elem.iter():
        tag = node.tag.split("}")[-1]
        if tag == path:
            return (node.text or "").strip()
    return ""


def _all_text(elem, path: str) -> list[str]:
    out = []
    if elem is None:
        return out
    for node in elem.iter():
        if node.tag.split("}")[-1] == path and node.text:
            out.append(node.text.strip())
    return out


def _flag(elem, *names: str) -> bool | None:
    """An FPDS true/false field, or None when the record does not carry it."""
    for name in names:
        value = _text(elem, name).lower()
        if value in ("true", "y", "yes"):
            return True
        if value in ("false", "n", "no"):
            return False
    return None


def _attr(elem, path: str, name: str = "name") -> str:
    """An attribute of the first element called `path` — FPDS puts the readable
    name of a code there: <contractingOfficeID name="AFRL/RGKA">FA2280</...>."""
    if elem is None:
        return ""
    for node in elem.iter():
        if node.tag.split("}")[-1] == path:
            return (node.get(name) or "").strip()
    return ""


# FPDS gives agencies as codes; awards from USAspending spell them out. The
# defence ones are spelled the USAspending way so the two sources agree.
_DOD_SUBTIERS = {"5700": "Department of the Air Force", "2100": "Department of the Army",
                 "1700": "Department of the Navy"}


def _amount(text: str) -> float:
    try:
        return float(text or 0)
    except ValueError:
        return 0.0


def contract_from_entry(entry) -> Contract | None:
    """One FPDS contract action as an award, or None without a PIID.

    The office that awarded it is kept (`contracting_office_id` and its name),
    which is what lets a whole office be watched. The amount is the award's
    total obligated to date when the action carries it, so the newest action
    on an award gives its total rather than one modification's slice.
    """
    piid = _text(entry, "PIID")
    if not piid:
        return None
    sub_code = _text(entry, "contractingOfficeAgencyID")
    sub_name = _DOD_SUBTIERS.get(sub_code) or _attr(entry, "contractingOfficeAgencyID").title()
    agency = ("Department of Defense" if sub_code in _DOD_SUBTIERS or sub_code.startswith("97")
              else _attr(entry, "departmentID").title() or sub_name)
    total = _amount(_text(entry, "totalObligatedAmount"))
    contract = Contract(
        award_id=piid, piid=piid,
        recipient_name=_text(entry, "vendorName"),
        recipient_uei=_text(entry, "UEI"),
        parent_recipient_name=_text(entry, "ultimateParentUEIName"),
        parent_recipient_uei=_text(entry, "ultimateParentUEI"),
        awarding_agency=agency, awarding_sub_agency=sub_name,
        award_amount=total or _amount(_text(entry, "obligatedAmount")),
        start_date=(_text(entry, "signedDate") or "")[:10],
        end_date=(_text(entry, "currentCompletionDate")
                  or _text(entry, "ultimateCompletionDate") or "")[:10],
        description=_text(entry, "descriptionOfContractRequirement"),
        naics_code=_text(entry, "principalNAICSCode"),
        naics_description=_attr(entry, "principalNAICSCode", "description"),
        psc_code=_text(entry, "productOrServiceCode"),
        psc_description=_attr(entry, "productOrServiceCode", "description"),
        solicitation_id=_text(entry, "solicitationID"),
        contracting_office_id=_text(entry, "contractingOfficeID"),
        contracting_office_name=_attr(entry, "contractingOfficeID"),
        country_of_incorporation=_text(entry, "countryOfIncorporation"),
        state_of_incorporation=_text(entry, "stateOfIncorporation"),
        foreign_owned_and_located=_text(entry, "isForeignOwnedAndLocated").lower()
        in ("true", "y", "yes"),
        foreign_funding=_foreign_funding(entry),
        source_url=f'https://www.fpds.gov/ezsearch/search.do?q=PIID:"{piid}"',
    )
    FPDSConnector._apply_registration(contract, _registration(entry))
    officer = FPDSConnector._officer_from_entry(entry)
    if officer.is_addressable:
        contract.officer = officer
    return contract


def _foreign_funding(entry) -> str:
    """The award's foreign funding as FPDS spells it out ("Foreign Funds FMS"),
    or "" when it has none. The element holds a code — A and B are foreign
    money, X "not applicable", on nearly every award — and its description."""
    code = _text(entry, "foreignFunding").upper()
    if code in ("A", "B"):
        return _attr(entry, "foreignFunding", "description").title() or (
            "Foreign Funds FMS" if code == "A" else "Foreign Funds non-FMS")
    return ""


def _registration(entry) -> dict:
    """The vendor's SAM registration details, as FPDS copies them onto an award.

    FPDS spells the sole-proprietor field `isSolePropreitorship`; the correct
    spelling is tried too, in case it is ever fixed.
    """
    return {
        "cage": _text(entry, "cageCode"),
        "sole_proprietor": _flag(entry, "isSolePropreitorship", "isSoleProprietorship"),
        "foreign_government": bool(_flag(entry, "isForeignGovernment")),
        "organizational_type": _text(entry, "organizationalType"),
        "name": _text(entry, "UEILegalBusinessName") or _text(entry, "vendorName"),
    }


def _pretty_name(email: str) -> str:
    """JANE.DOEBAKEWELL.N00019@JSF.MIL -> 'Jane Doebakewell'.

    DoD appends a disambiguating digit when two people share a name
    (JOHN.Q.ROE2.CIV@MAIL.MIL). It belongs to the mailbox, not the person,
    and "Dear John Q Roe2," in a notice to a federal official reads as a
    broken tool. Dropped for display; the address itself is untouched.
    """
    local = email.split("@")[0]
    parts = [p for p in local.split(".") if p and not re.fullmatch(r"[A-Z]\d{4,}", p)]
    parts = [p for p in parts if not re.fullmatch(r"\d+", p)]
    parts = [re.sub(r"\d+$", "", p) or p for p in parts]
    parts = [p for p in parts if p.lower() not in {"civ", "mil", "ctr"}]
    if not parts:
        return ""
    return " ".join(p.capitalize() for p in parts[:3])


class FPDSConnector:
    name = "fpds"

    def __init__(self, http) -> None:
        self.http = http

    def fetch_award(self, piid: str) -> list[ET.Element]:
        if not piid:
            return []
        resp = self.http.get(FEED, params={"FEEDNAME": "PUBLIC", "q": f'PIID:"{piid}"'})
        if resp.get("status") != 200 or not resp.get("text"):
            return []
        try:
            root = ET.fromstring(resp["text"])
        except ET.ParseError as exc:
            log.debug("FPDS parse error for %s: %s", piid, exc)
            return []
        return list(root.iter(f"{ATOM_NS}entry"))

    # FPDS's feed gives ten actions a page, and a modification is an action of
    # its own: an office's month is one query, read page by page.
    PAGE = 10

    def search_page(self, query: str, start: int = 0) -> tuple[list[Contract], bool, bool]:
        """One page of a feed query: (awards, more pages after this, answered).

        Modifications of one award on the page are folded into it, keeping the
        newest action's details. `answered` is False when FPDS did not reply
        usably, so a caller can try the page again rather than skip it.
        """
        resp = self.http.get(FEED, params={"FEEDNAME": "PUBLIC", "q": query,
                                           "start": str(start)})
        if resp.get("status") != 200 or not resp.get("text"):
            return [], False, False
        try:
            root = ET.fromstring(resp["text"])
        except ET.ParseError as exc:
            log.debug("FPDS parse error for %s at %s: %s", query, start, exc)
            return [], False, False
        entries = list(root.iter(f"{ATOM_NS}entry"))
        more = any(link.get("rel") == "next" for link in root.iter(f"{ATOM_NS}link"))
        newest: dict[str, tuple[str, Contract]] = {}
        for entry in entries:
            contract = contract_from_entry(entry)
            if contract is None:
                continue
            signed = _text(entry, "signedDate")
            held = newest.get(contract.piid)
            if held is None or signed > held[0]:
                newest[contract.piid] = (signed, contract)
        return [c for _, c in newest.values()], more and bool(entries), True

    def enrich(self, contract: Contract) -> Contract:
        # `fpds_piid` is the prime's PIID for a subaward: a subcontract has no
        # contracting officer of its own, and FPDS has no record of it at all.
        entries = self.fetch_award(contract.fpds_piid or contract.award_id)
        if not entries:
            return contract

        # Newest modification carries the most current custodian of the file.
        def signed(e):
            return _text(e, "signedDate") or _text(e, "lastModifiedDate") or ""

        entries.sort(key=signed, reverse=True)
        latest = entries[0]

        officer = self._officer_from_entry(latest)
        if officer.is_addressable:
            contract.officer = officer
        contract.contracting_office_id = (
            _text(latest, "contractingOfficeID") or contract.contracting_office_id)
        contract.solicitation_id = (
            _text(latest, "solicitationID") or contract.solicitation_id)

        if contract.is_subaward:
            # Everything below describes the vendor on the FPDS record, which
            # for a subaward is the *prime*. Copying it onto the subcontractor
            # would attribute one company's ownership, parent and requirement
            # to another — the precise mistake this tool exists to avoid. Who
            # to notify is a fact about the prime contract; who the supplier is
            # is not.
            return contract

        coi = _text(latest, "countryOfIncorporation")
        if coi:
            contract.country_of_incorporation = coi
        soi = _text(latest, "stateOfIncorporation")
        if soi:
            contract.state_of_incorporation = soi
        if _text(latest, "isForeignOwnedAndLocated").lower() in ("true", "y", "yes"):
            contract.foreign_owned_and_located = True
        parent = _text(latest, "ultimateParentUEIName")
        if parent and not contract.parent_recipient_name:
            contract.parent_recipient_name = parent
        parent_uei = _text(latest, "ultimateParentUEI")
        if parent_uei and not contract.parent_recipient_uei:
            contract.parent_recipient_uei = parent_uei
        ff = _foreign_funding(latest)
        if ff and not contract.foreign_funding:
            contract.foreign_funding = ff
        self._apply_registration(contract, _registration(latest))
        desc = _text(latest, "descriptionOfContractRequirement")
        if desc and len(desc) > len(contract.description):
            contract.description = desc
        return contract

    @staticmethod
    def _apply_registration(contract: Contract, reg: dict) -> None:
        if reg["cage"]:
            contract.cage_code = reg["cage"]
        if reg["sole_proprietor"] is not None:
            contract.is_sole_proprietor = reg["sole_proprietor"]
        if reg["foreign_government"]:
            contract.is_foreign_government = True
        if reg["organizational_type"]:
            contract.organizational_type = reg["organizational_type"]

    def vendor_registration(self, uei: str) -> dict | None:
        """What FPDS records about a vendor's registration, from its own awards.

        A subcontractor has no FPDS record under the prime's award, but one
        that has ever held a prime contract has records of its own, and those
        carry its SAM registration — including whether it is a sole
        proprietor. None when FPDS holds no award to this UEI.
        """
        if not uei:
            return None
        resp = self.http.get(FEED, params={"FEEDNAME": "PUBLIC",
                                           "q": f'VENDOR_UEI:"{uei}"'})
        if resp.get("status") != 200 or not resp.get("text"):
            return None
        try:
            root = ET.fromstring(resp["text"])
        except ET.ParseError:
            return None
        entries = list(root.iter(f"{ATOM_NS}entry"))
        # The feed matches loosely enough that the UEI is checked, not assumed.
        entries = [e for e in entries if _text(e, "UEI").upper() == uei.upper()]
        if not entries:
            return None
        entries.sort(key=lambda e: _text(e, "signedDate") or "", reverse=True)
        return _registration(entries[0])

    @staticmethod
    def _officer_from_entry(entry) -> ContractingOfficer:
        """Prefer the approver (warranted official) over the last editor."""
        candidates = [
            ("approvedBy", "high"),
            ("lastModifiedBy", "medium"),
            ("createdBy", "low"),
        ]
        for tag, confidence in candidates:
            raw = _text(entry, tag)
            if not raw:
                continue
            m = _EMAIL_RX.search(raw)
            if not m:
                continue
            email = m.group(0)
            return ContractingOfficer(
                name=_pretty_name(email), email=email.lower(),
                phone=_text(entry, "phoneNo"),
                office_code=_text(entry, "contractingOfficeID"),
                source=f"FPDS-NG <{tag}>", confidence=confidence)
        return ContractingOfficer()

    def contract_text(self, contract: Contract) -> str:
        """Flat text of the FPDS record, for change detection across screens."""
        entries = self.fetch_award(contract.piid or contract.award_id)
        if not entries:
            return ""
        interesting = [
            "vendorName", "ultimateParentUEIName", "countryOfIncorporation",
            "stateOfIncorporation", "isForeignOwnedAndLocated", "foreignFunding",
            "obligatedAmount", "currentCompletionDate", "descriptionOfContractRequirement",
            "contractingOfficeID", "solicitationID", "lastModifiedBy", "approvedBy",
            "streetAddress", "city", "countryCode",
        ]
        lines: list[str] = []
        for e in entries[:5]:
            for tag in interesting:
                for value in _all_text(e, tag)[:1]:
                    lines.append(f"{tag}: {value}")
        return "\n".join(lines)
