"""Jurisdiction and terminology lexicons.

Tiering follows the statutory language a KO will recognise:

  covered   - "covered nation" per 10 U.S.C. 4872(d)(2) (China, Russia, Iran, DPRK)
  adjacent  - jurisdictions under PRC/Russia control or comprehensive sanctions
  haven     - opaque/secrecy jurisdictions where beneficial ownership is hidden;
              the point is not tax, it's that FOCI cannot be ruled out
  conduit   - legitimate but commonly used to intermediate foreign capital

Multipliers scale a rule's base weight. They are judgement calls, not law;
they live here so an analyst can tune them without touching rule code.
"""
from __future__ import annotations

import re

JURISDICTIONS: dict[str, dict] = {
    # --- covered nations -------------------------------------------------
    "China": {"tier": "covered", "mult": 3.0, "patterns": [
        r"\bchina\b", r"\bchinese\b", r"\bPRC\b", r"people'?s republic of china",
        r"\bPeople'?s Liberation Army\b", r"\bshanghai\b", r"\bshenzhen\b",
        r"\bbeijing\b", r"\bguangzhou\b", r"\bhangzhou\b", r"\bCNY\b",
        r"\bRMB\b", r"renminbi", r"有限公司"]},
    "Hong Kong": {"tier": "covered", "mult": 2.8, "patterns": [
        r"\bhong ?kong\b", r"\bHKSAR\b", r"\bH\.?K\.?\s*(?:limited|ltd)\b"]},
    "Macau": {"tier": "covered", "mult": 2.8, "patterns": [r"\bmacau\b", r"\bmacao\b"]},
    "Russia": {"tier": "covered", "mult": 3.0, "patterns": [
        r"\brussia\b", r"\brussian federation\b", r"\bmoscow\b", r"\bOOO\b",
        r"\bPJSC\b", r"\bsberbank\b"]},
    "Iran": {"tier": "covered", "mult": 3.0, "patterns": [r"\biran\b", r"\biranian\b", r"\btehran\b"]},
    "North Korea": {"tier": "covered", "mult": 3.0, "patterns": [
        r"\bnorth korea\b", r"\bDPRK\b", r"\bpyongyang\b"]},

    # --- adjacent / sanctioned ------------------------------------------
    "Belarus": {"tier": "adjacent", "mult": 2.4, "patterns": [r"\bbelarus\b", r"\bminsk\b"]},
    "Venezuela": {"tier": "adjacent", "mult": 2.2, "patterns": [r"\bvenezuela\b", r"\bcaracas\b"]},
    "Cuba": {"tier": "adjacent", "mult": 2.2, "patterns": [r"\bcuba\b", r"\bhavana\b"]},
    "Syria": {"tier": "adjacent", "mult": 2.2, "patterns": [r"\bsyria\b", r"\bdamascus\b"]},

    # --- opaque / secrecy jurisdictions ----------------------------------
    "British Virgin Islands": {"tier": "haven", "mult": 2.2, "patterns": [
        r"british virgin islands", r"\bB\.?V\.?I\.?\b", r"\btortola\b", r"road town"]},
    "Cayman Islands": {"tier": "haven", "mult": 2.1, "patterns": [
        r"cayman islands", r"\bgeorge town,? grand cayman\b", r"\bgrand cayman\b"]},
    "Bermuda": {"tier": "haven", "mult": 1.9, "patterns": [r"\bbermuda\b", r"\bhamilton, bermuda\b"]},
    "Seychelles": {"tier": "haven", "mult": 2.2, "patterns": [r"\bseychelles\b", r"\bvictoria, mahe\b"]},
    "Marshall Islands": {"tier": "haven", "mult": 2.1, "patterns": [r"marshall islands", r"\bmajuro\b"]},
    "Panama": {"tier": "haven", "mult": 2.0, "patterns": [r"\bpanama\b", r"panama city, panama"]},
    "Belize": {"tier": "haven", "mult": 2.1, "patterns": [r"\bbelize\b"]},
    "Mauritius": {"tier": "haven", "mult": 2.0, "patterns": [r"\bmauritius\b", r"port louis"]},
    "Bahamas": {"tier": "haven", "mult": 1.9, "patterns": [r"\bbahamas\b", r"\bnassau\b"]},
    "Nevis": {"tier": "haven", "mult": 2.1, "patterns": [r"\bnevis\b", r"saint kitts", r"st\.? kitts"]},
    "Anguilla": {"tier": "haven", "mult": 2.0, "patterns": [r"\banguilla\b"]},
    "Vanuatu": {"tier": "haven", "mult": 2.1, "patterns": [r"\bvanuatu\b"]},
    "Samoa": {"tier": "haven", "mult": 2.0, "patterns": [r"\bsamoa\b", r"\bapia\b"]},
    "Gibraltar": {"tier": "haven", "mult": 1.8, "patterns": [r"\bgibraltar\b"]},
    "Isle of Man": {"tier": "haven", "mult": 1.8, "patterns": [r"isle of man", r"\bdouglas, iom\b"]},
    "Jersey": {"tier": "haven", "mult": 1.8, "patterns": [r"\bjersey, channel\b", r"\bst\.? helier\b"]},
    "Guernsey": {"tier": "haven", "mult": 1.8, "patterns": [r"\bguernsey\b", r"st\.? peter port"]},
    "Liechtenstein": {"tier": "haven", "mult": 1.9, "patterns": [r"\bliechtenstein\b", r"\bvaduz\b"]},
    "Cyprus": {"tier": "haven", "mult": 2.0, "patterns": [r"\bcyprus\b", r"\bnicosia\b", r"\blimassol\b"]},
    "Malta": {"tier": "haven", "mult": 1.8, "patterns": [r"\bmalta\b", r"\bvalletta\b"]},
    "Curacao": {"tier": "haven", "mult": 1.9, "patterns": [r"\bcura[cç]ao\b", r"willemstad"]},
    "Barbados": {"tier": "haven", "mult": 1.7, "patterns": [r"\bbarbados\b", r"bridgetown"]},

    # --- conduits ---------------------------------------------------------
    "Singapore": {"tier": "conduit", "mult": 1.3, "patterns": [r"\bsingapore\b"]},
    "United Arab Emirates": {"tier": "conduit", "mult": 1.4, "patterns": [
        r"\bunited arab emirates\b", r"\bU\.?A\.?E\.?\b", r"\bdubai\b", r"\babu dhabi\b"]},
    "Luxembourg": {"tier": "conduit", "mult": 1.3, "patterns": [r"\bluxembourg\b", r"\bS\.?[àa]\.?r\.?l\.?\b"]},
    "Netherlands": {"tier": "conduit", "mult": 1.2, "patterns": [r"\bnetherlands\b", r"\bB\.?V\.?\b(?! ?islands)"]},
    "Ireland": {"tier": "conduit", "mult": 1.15, "patterns": [r"\bireland\b", r"\bdublin\b"]},
    "Switzerland": {"tier": "conduit", "mult": 1.2, "patterns": [r"\bswitzerland\b", r"\bzug\b", r"\bzurich\b"]},
    "Hungary": {"tier": "conduit", "mult": 1.2, "patterns": [r"\bhungary\b", r"\bbudapest\b"]},
}

# ISO-3166 alpha-3 codes as they appear in SAM/FPDS/USAspending country fields.
COUNTRY_CODE_TO_JURISDICTION = {
    "CHN": "China", "HKG": "Hong Kong", "MAC": "Macau", "RUS": "Russia",
    "IRN": "Iran", "PRK": "North Korea", "BLR": "Belarus", "VEN": "Venezuela",
    "CUB": "Cuba", "SYR": "Syria", "VGB": "British Virgin Islands",
    "CYM": "Cayman Islands", "BMU": "Bermuda", "SYC": "Seychelles",
    "MHL": "Marshall Islands", "PAN": "Panama", "BLZ": "Belize",
    "MUS": "Mauritius", "BHS": "Bahamas", "KNA": "Nevis", "AIA": "Anguilla",
    "VUT": "Vanuatu", "WSM": "Samoa", "GIB": "Gibraltar", "IMN": "Isle of Man",
    "JEY": "Jersey", "GGY": "Guernsey", "LIE": "Liechtenstein", "CYP": "Cyprus",
    "MLT": "Malta", "CUW": "Curacao", "BRB": "Barbados", "SGP": "Singapore",
    "ARE": "United Arab Emirates", "LUX": "Luxembourg", "NLD": "Netherlands",
    "IRL": "Ireland", "CHE": "Switzerland", "HUN": "Hungary",
}

# --- event vocabularies ----------------------------------------------------

# Terms must be specific enough that their presence genuinely implies an
# ownership event. Bare "led by", "series a" and "PIPE" were removed after they
# fired on ordinary prose ("a team led by", "Series A aircraft", pipeline
# references); each now requires its financing context.
FOCI_EVENT_TERMS = [
    "strategic investment", "minority investment", "minority stake", "equity stake",
    "change of control", "change in control", "acquisition of a controlling",
    "controlling interest", "definitive agreement", "merger agreement",
    "tender offer", "share purchase agreement", "subscription agreement",
    "joint venture", "board observer", "board seat", "board designee",
    "voting agreement", "proxy agreement", "special security agreement",
    "beneficial ownership", "beneficial owner",
    "series a funding", "series a round", "series a financing", "series a preferred",
    "series b funding", "series b round", "series b financing",
    "series c funding", "series c round", "series d funding",
    "led the round", "round led by", "financing led by", "investment led by",
    "funding round", "anchor investor", "strategic partner",
    "capital injection", "recapitalization", "reverse merger",
    "special purpose acquisition", "private investment in public equity",
    "convertible note", "sovereign wealth",
]

IP_COLLATERAL_TERMS = [
    "intellectual property security agreement", "patent security agreement",
    "trademark security agreement", "copyright security agreement",
    "collateral assignment of patents", "collateral assignment",
    "security interest in the patents", "security interest in intellectual property",
    "grant of security interest", "granted a security interest",
    "pledge of intellectual property", "pledged intellectual property",
    "first priority lien", "first-priority security interest",
    "collateral agent", "administrative agent", "secured party",
    "UCC-1", "UCC financing statement", "all assets lien",
    "credit agreement", "loan and security agreement", "venture debt",
    "secured term loan", "revolving credit facility", "debenture",
    "foreclose on the collateral", "event of default",
]

IP_TRANSFER_TERMS = [
    "assignment of patents", "patent assignment", "sale of intellectual property",
    "divest", "divestiture", "exclusive license", "exclusive licence",
    "technology transfer agreement", "nunc pro tunc assignment",
    "assignment for the benefit of creditors", "chapter 11", "chapter 7",
    "receivership", "administration", "insolvency", "363 sale",
    "asset purchase agreement", "wind down", "wind-down", "liquidation",
    "transfer of technical data", "source code escrow", "release of source code",
]

# USPTO assignment conveyance strings that indicate encumbrance rather than sale.
USPTO_SECURITY_CONVEYANCES = [
    "SECURITY INTEREST", "SECURITY AGREEMENT", "COLLATERAL", "LIEN", "PLEDGE",
    "MORTGAGE",
]

# Contract clauses that make an IP event materially worse for the Government.
IP_CLAUSE_PATTERNS = {
    "DFARS 252.227-7013 (Technical Data - Noncommercial Items)": r"252\.?227[-\s]?7013",
    "DFARS 252.227-7014 (Noncommercial Computer Software)": r"252\.?227[-\s]?7014",
    "DFARS 252.227-7017 (Identification of Restrictions)": r"252\.?227[-\s]?7017",
    "DFARS 252.227-7018 (SBIR Data Rights)": r"252\.?227[-\s]?7018",
    "DFARS 252.204-7012 (Covered Defense Information)": r"252\.?204[-\s]?7012",
    "FAR 52.227-11 (Patent Rights - Contractor Retention)": r"52\.?227[-\s]?11\b",
    "FAR 52.227-14 (Rights in Data - General)": r"52\.?227[-\s]?14\b",
    "Government Purpose Rights": r"government purpose rights",
    "Limited Rights data": r"\blimited rights\b",
    "Restricted Rights software": r"\brestricted rights\b",
    "SBIR/STTR data rights": r"\bSBIR\b|\bSTTR\b",
    "Technical data package": r"technical data package|\bTDP\b",
}

# Lender/agent name fragments — a security interest held by one of these is a
# financing event; held by an unfamiliar offshore vehicle it is a FOCI question.
FINANCIAL_COUNTERPARTY_HINTS = [
    "bank", "capital", "credit", "finance", "financial", "lending", "lenders",
    "partners lp", "fund", "holdings", "trust", "agent", "asset management",
]


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def _term_regex(term: str) -> re.Pattern:
    """Whole-term matching.

    Substring matching is not safe here. `"SPAC"` is a substring of `"space"`,
    which on an aerospace contractor's website appears on nearly every page —
    enough, in an early build, to escalate a Lockheed history page describing a
    missile range in the Marshall Islands into a CRITICAL notice about foreign
    investment. Lookarounds rather than \\b so terms with leading or trailing
    punctuation (UCC-1) still anchor correctly.
    """
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)


JURISDICTION_REGEX = {name: _compile(meta["patterns"]) for name, meta in JURISDICTIONS.items()}
IP_CLAUSE_REGEX = {label: re.compile(p, re.IGNORECASE) for label, p in IP_CLAUSE_PATTERNS.items()}
_TERM_CACHE: dict[str, re.Pattern] = {}


def _snippet(text: str, start: int, end: int, pad: int = 100) -> str:
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    return text[lo:hi].replace("\n", " ").strip()


def find_jurisdictions_with_pos(text: str) -> list[tuple[str, str, int]]:
    """Return [(jurisdiction, snippet, match_offset)] for every hit."""
    hits: list[tuple[str, str, int]] = []
    for name, regexes in JURISDICTION_REGEX.items():
        for rx in regexes:
            m = rx.search(text)
            if m:
                hits.append((name, _snippet(text, m.start(), m.end(), 90), m.start()))
                break
    return hits


def find_jurisdictions(text: str) -> list[tuple[str, str]]:
    """Return [(jurisdiction, matched snippet)] for every hit in `text`."""
    return [(n, s) for n, s, _ in find_jurisdictions_with_pos(text)]


def jurisdiction_multiplier(name: str) -> float:
    return JURISDICTIONS.get(name, {}).get("mult", 1.0)


def jurisdiction_tier(name: str) -> str:
    return JURISDICTIONS.get(name, {}).get("tier", "unknown")


def find_terms_with_pos(text: str, terms: list[str]) -> list[tuple[str, str, int]]:
    """Return [(term, snippet, match_offset)] for each whole-term hit."""
    out: list[tuple[str, str, int]] = []
    for term in terms:
        rx = _TERM_CACHE.get(term)
        if rx is None:
            rx = _TERM_CACHE[term] = _term_regex(term)
        m = rx.search(text)
        if m:
            out.append((term, _snippet(text, m.start(), m.end(), 110), m.start()))
    return out


def find_terms(text: str, terms: list[str]) -> list[tuple[str, str]]:
    """Return [(term, surrounding snippet)] for each term present in `text`."""
    return [(t, s) for t, s, _ in find_terms_with_pos(text, terms)]


def find_ip_clauses(text: str) -> list[str]:
    return [label for label, rx in IP_CLAUSE_REGEX.items() if rx.search(text or "")]
