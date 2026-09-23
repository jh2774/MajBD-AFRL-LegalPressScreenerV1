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
        ff = _text(latest, "foreignFunding")
        if ff and not contract.foreign_funding:
            contract.foreign_funding = ff
        desc = _text(latest, "descriptionOfContractRequirement")
        if desc and len(desc) > len(contract.description):
            contract.description = desc
        return contract

    def _officer_from_entry(self, entry) -> ContractingOfficer:
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
