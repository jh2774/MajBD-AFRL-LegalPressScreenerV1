"""Smaller registry connectors: IAPD, OFAC, USPTO, SAM.gov.

Grouped in one module because each is a thin wrapper over a single endpoint.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from difflib import SequenceMatcher

from ..models import Document

log = logging.getLogger("foci.registries")


# --------------------------------------------------------------------- IAPD
class IAPDConnector:
    """Investment Adviser Public Disclosure.

    Relevant because the investor arriving at a contractor is frequently an
    SEC-registered adviser, and IAPD gives its office country and disclosure
    history without a key. Endpoint is the one the IAPD site itself calls.
    """
    name = "iapd"
    SEARCH = "https://api.adviserinfo.sec.gov/search/firm"

    def __init__(self, http) -> None:
        self.http = http

    def search_firm(self, query: str, hits: int = 10) -> list[Document]:
        if not query.strip():
            return []
        resp = self.http.get(self.SEARCH, params={
            "query": query, "hits": hits, "type": "Firm",
            "investmentAdvisors": "true", "start": 0})
        data = resp.get("json") or {}
        rows = ((data.get("hits") or {}).get("hits") or [])
        docs: list[Document] = []
        for row in rows:
            src = row.get("_source") or {}
            addr = self._address(src.get("firm_ia_address_details"))
            country = addr.get("country", "")
            firm = src.get("firm_name") or ""
            sec_no = src.get("firm_ia_full_sec_number") or ""
            crd = str(src.get("firm_source_id") or "")
            text = (
                f"IAPD firm: {firm}\nSEC number: {sec_no}\nCRD: {crd}\n"
                f"Status: {src.get('firm_ia_scope', '')}\n"
                f"Has disclosures: {src.get('firm_ia_disclosure_fl', '')}\n"
                f"Office: {addr.get('street1', '')} {addr.get('city', '')} {country}\n"
                f"Other names: {', '.join(src.get('firm_other_names') or [])}")
            docs.append(Document(
                source="iapd", key=f"iapd:{crd or firm}", title=firm,
                url=f"https://adviserinfo.sec.gov/firm/summary/{crd}" if crd else "",
                text=text, doc_type="adviser_registration",
                meta={"firm_name": firm, "sec_number": sec_no, "crd": crd,
                      "country": country,
                      "has_disclosures": src.get("firm_ia_disclosure_fl") == "Y"}))
        return docs

    @staticmethod
    def _address(raw) -> dict:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            return (parsed or {}).get("officeAddress") or {}
        except Exception:
            return {}


# --------------------------------------------------------------------- OFAC
class OFACConnector:
    """OFAC Specially Designated Nationals list (keyless CSV).

    Name matching only. A hit is a question, never a conclusion — SDN entries
    are adjudicated on identifiers (DOB, passport, address), which this does
    not attempt.
    """
    name = "ofac"
    SDN_CSV = "https://www.treasury.gov/ofac/downloads/sdn.csv"

    def __init__(self, http) -> None:
        self.http = http
        self._rows: list[tuple[str, str, str]] | None = None

    def _load(self) -> list[tuple[str, str, str]]:
        if self._rows is not None:
            return self._rows
        resp = self.http.get(self.SDN_CSV)
        rows: list[tuple[str, str, str]] = []
        if resp.get("status") == 200 and resp.get("text"):
            reader = csv.reader(io.StringIO(resp["text"]))
            for row in reader:
                if len(row) >= 4:
                    rows.append((row[0].strip(), row[1].strip().strip('"'),
                                 row[3].strip().strip('"')))
        self._rows = rows
        return rows

    def screen(self, name: str, threshold: float = 0.90) -> list[Document]:
        target = _simplify(name)
        if len(target) < 5:
            return []
        out: list[Document] = []
        for ent_num, sdn_name, program in self._load():
            candidate = _simplify(sdn_name)
            if not candidate:
                continue
            if abs(len(candidate) - len(target)) > 12:
                continue
            ratio = SequenceMatcher(None, target, candidate).ratio()
            if ratio >= threshold:
                out.append(Document(
                    source="ofac", key=f"ofac:{ent_num}", title=sdn_name,
                    url="https://sanctionssearch.ofac.treas.gov/Details.aspx?id=" + ent_num,
                    text=(f"OFAC SDN entry {ent_num}: {sdn_name} (program {program}). "
                          f"Similarity to screened name '{name}': {ratio:.2f}. "
                          f"Name match only — adjudicate against identifiers."),
                    doc_type="sanctions_match",
                    meta={"score": round(ratio, 3), "program": program}))
        return out[:5]


# -------------------------------------------------------------------- USPTO
class USPTOConnector:
    """USPTO patent assignment records — recorded IP security interests.

    The legacy keyless endpoint (assignment-api.uspto.gov) has been retired in
    favour of the Open Data Portal, which requires a key. Without one this
    connector reports itself unavailable rather than silently returning nothing,
    so a screen never implies "no encumbrance found" when it simply could not
    look. Set USPTO_API_KEY (free, developer.uspto.gov) to enable.
    """
    name = "uspto"
    ODP_SEARCH = "https://api.uspto.gov/api/v1/patent/applications/search"
    LEGACY = "https://assignment-api.uspto.gov/patent/lookup"

    def __init__(self, http, api_key: str = "") -> None:
        self.http = http
        self.api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def assignments(self, assignor_name: str, rows: int = 20) -> list[Document]:
        docs = self._legacy_lookup(assignor_name, rows)
        if docs:
            return docs
        if not self.api_key:
            return []
        return self._odp_lookup(assignor_name, rows)

    def _legacy_lookup(self, name: str, rows: int) -> list[Document]:
        resp = self.http.get(self.LEGACY, params={
            "query": name, "filter_assignorName": "", "rows": rows})
        if resp.get("status") != 200:
            return []
        data = resp.get("json") or {}
        found = ((data.get("response") or {}).get("docs") or [])
        return [self._to_doc(d) for d in found]

    def _odp_lookup(self, name: str, rows: int) -> list[Document]:
        resp = self.http.get(self.ODP_SEARCH,
                             params={"q": f'applicantName:"{name}"', "rows": rows},
                             headers={"X-API-KEY": self.api_key})
        if resp.get("status") != 200:
            log.info("USPTO ODP returned %s", resp.get("status"))
            return []
        data = resp.get("json") or {}
        return [self._to_doc(d) for d in (data.get("patentFileWrapperDataBag") or [])]

    @staticmethod
    def _to_doc(raw: dict) -> Document:
        reel = str(raw.get("reelNo") or raw.get("reel_no") or "")
        frame = str(raw.get("frameNo") or raw.get("frame_no") or "")
        conveyance = str(raw.get("conveyanceText") or raw.get("conveyance_text") or "")
        assignee = _first(raw.get("assigneeName") or raw.get("assignee_name"))
        assignor = _first(raw.get("assignorName") or raw.get("assignor_name"))
        address = " ".join(filter(None, [
            _first(raw.get("assigneeAddress1")), _first(raw.get("assigneeCity")),
            _first(raw.get("assigneeCountryName")), _first(raw.get("assigneePostcode"))]))
        recorded = str(raw.get("recordedDate") or raw.get("recorded_date") or "")
        patents = raw.get("patNum") or raw.get("inventionTitle") or []
        count = len(patents) if isinstance(patents, list) else 1
        return Document(
            source="uspto", key=f"uspto:{reel}-{frame}",
            title=f"{conveyance} {assignor} -> {assignee}",
            url=(f"https://assignment.uspto.gov/patent/index.html#/patent/search/"
                 f"resultAbstract?reelFrame={reel}%2F{frame}"),
            text=(f"USPTO assignment reel/frame {reel}/{frame} recorded {recorded}.\n"
                  f"Conveyance: {conveyance}\nAssignor: {assignor}\n"
                  f"Assignee: {assignee}\nAssignee address: {address}\n"
                  f"Properties: {count}"),
            published=recorded, doc_type="patent_assignment",
            meta={"conveyance": conveyance, "assignee": assignee, "assignor": assignor,
                  "assignee_address": address, "patent_count": count,
                  "reel_frame": f"{reel}/{frame}"})


# ------------------------------------------------------------------ SAM.gov
class SAMConnector:
    """SAM.gov entity registration and contract opportunities.

    Requires a free api.data.gov key. Used for two things: the entity's
    registered/physical address and business types (FOCI structure), and the
    solicitation's point of contact, which is the most authoritative KO email
    when it is available.
    """
    name = "samgov"
    ENTITY = "https://api.sam.gov/entity-information/v3/entities"
    OPPS = "https://api.sam.gov/opportunities/v2/search"

    def __init__(self, http, api_key: str = "") -> None:
        self.http = http
        self.api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def entity(self, uei: str) -> Document | None:
        if not (self.available and uei):
            return None
        resp = self.http.get(self.ENTITY, params={
            "api_key": self.api_key, "ueiSAM": uei,
            "includeSections": "entityRegistration,coreData"})
        data = resp.get("json") or {}
        records = data.get("entityData") or []
        if not records:
            return None
        rec = records[0]
        reg = rec.get("entityRegistration") or {}
        core = rec.get("coreData") or {}
        phys = (core.get("physicalAddress") or {})
        mail = (core.get("mailingAddress") or {})
        general = (core.get("generalInformation") or {})
        text = (
            f"SAM registration for {reg.get('legalBusinessName', '')} (UEI {uei})\n"
            f"CAGE: {reg.get('cageCode', '')}\n"
            f"Registration status: {reg.get('registrationStatus', '')}\n"
            f"Expiration: {reg.get('registrationExpirationDate', '')}\n"
            f"Physical address: {phys.get('addressLine1', '')} {phys.get('city', '')} "
            f"{phys.get('stateOrProvinceCode', '')} {phys.get('countryCode', '')}\n"
            f"Mailing country: {mail.get('countryCode', '')}\n"
            f"Entity structure: {general.get('entityStructureDesc', '')}\n"
            f"State of incorporation: {general.get('stateOfIncorporationCode', '')}\n"
            f"Country of incorporation: {general.get('countryOfIncorporationCode', '')}")
        return Document(
            source="samgov", key=f"sam:{uei}",
            title=f"SAM entity {reg.get('legalBusinessName', uei)}",
            url=f"https://sam.gov/entity/{uei}", text=text, doc_type="sam_entity",
            meta={"uei": uei, "cage": reg.get("cageCode", ""),
                  "country": phys.get("countryCode", ""),
                  "country_of_incorporation": general.get("countryOfIncorporationCode", "")})

    def opportunity_poc(self, solicitation_number: str) -> dict:
        """Return the primary point of contact for a solicitation, if published."""
        if not (self.available and solicitation_number):
            return {}
        resp = self.http.get(self.OPPS, params={
            "api_key": self.api_key, "solnum": solicitation_number, "limit": 5})
        data = resp.get("json") or {}
        for opp in data.get("opportunitiesData") or []:
            for poc in opp.get("pointOfContact") or []:
                if poc.get("email"):
                    return {"name": poc.get("fullName", ""), "email": poc["email"],
                            "phone": poc.get("phone", ""),
                            "type": poc.get("type", ""),
                            "title": poc.get("title", ""),
                            "url": opp.get("uiLink", "")}
        return {}


# ------------------------------------------------------------------ helpers
_PUNCT_RX = re.compile(r"[^a-z0-9 ]")
_STOP = {"the", "inc", "incorporated", "corp", "corporation", "llc", "ltd",
         "limited", "company", "co", "plc", "lp", "llp", "group", "holdings"}


def _simplify(name: str) -> str:
    tokens = _PUNCT_RX.sub(" ", (name or "").lower()).split()
    return " ".join(t for t in tokens if t not in _STOP)


def _first(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")
