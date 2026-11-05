"""The U.S. Government's export and sanctions screening lists, and the name
matching that decides whether a contractor appears on one.

**Which lists.** The Consolidated Screening List (trade.gov) gathers twelve
lists into one keyless file: Treasury's SDN, sectoral, menu-based and Chinese
military-industrial companies lists; Commerce's Entity List, Military End
User, Unverified and Denied Persons lists; State's ITAR debarments and
nonproliferation sanctions. For defence suppliers the Commerce lists matter as
much as the SDN list, and the file carries every entry's other names. It is
downloaded at most once a day — trade.gov rate-limits the file hard — and when
it cannot be had, Treasury's own SDN and consolidated files, with their alias
files, stand in, and the screen says that the Commerce and State lists were
not checked.

**How names are matched.** By the *distinctive* words of a name — what is left
when legal forms ("LLC", "Limited") and generic business words
("International", "Technologies", "Systems") are taken away — never by how
alike two whole strings look. Whole-string similarity is what flagged HERMEUS
CORP as the sanctioned "HERMES", ADA TECHNOLOGIES as "0DAY TECHNOLOGIES" and
RUNE TECHNOLOGIES as "NEW TECHNOLOGIES": in each pair the generic word was
most of the string. Three kinds of match, strongest first:

  exact    the names are the same once legal forms, punctuation and spacing
           are set aside ("ROSOBORON EXPORT" is "ROSOBORONEXPORT");
  core     the distinctive words are the same and only generic words differ
           ("HUAWEI TECHNOLOGIES USA INC" and "Huawei Technologies Co., Ltd.");
  variant  the same distinctive words, one long word spelled slightly
           differently — a transliteration ("ROSOBORONEKSPORT"); or one name's
           distinctive words are all of the other's plus one more, every
           shared word at least five letters long.

A match is always a name match, which the rule says: an entry is adjudicated
on identifiers (address, registration numbers), which this does not attempt.
People, ships and aircraft are not matched against companies.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..models import Document

log = logging.getLogger("foci.lists")

CSL_URL = ("https://data.trade.gov/downloadable_consolidated_screening_list/v1/"
           "consolidated.csv")
CSL_PAGE = "https://www.trade.gov/consolidated-screening-list"
OFAC_FILES = {
    "sdn": "https://www.treasury.gov/ofac/downloads/sdn.csv",
    "alt": "https://www.treasury.gov/ofac/downloads/alt.csv",
    "cons_prim": "https://www.treasury.gov/ofac/downloads/consolidated/cons_prim.csv",
    "cons_alt": "https://www.treasury.gov/ofac/downloads/consolidated/cons_alt.csv",
}
OFAC_DETAILS = "https://sanctionssearch.ofac.treas.gov/Details.aspx?id="
DAY = 24 * 3600

# Short, readable names for the lists, as the email and the contractor page
# show them. Matched on the start of the CSL's `source` column.
LIST_NAMES = [
    ("Specially Designated Nationals", "Treasury SDN list"),
    ("Sectoral Sanctions", "Treasury sectoral sanctions list (SSI)"),
    ("Non-SDN Chinese Military", "Treasury Chinese military companies list (CMIC)"),
    ("Non-SDN Menu-Based", "Treasury menu-based sanctions list"),
    ("Palestinian Legislative", "Treasury PLC list"),
    ("Capta", "Treasury CAPTA list"),
    ("Entity List", "Commerce Entity List"),
    ("Military End User", "Commerce Military End User list"),
    ("Unverified List", "Commerce Unverified List"),
    ("Denied Persons", "Commerce Denied Persons List"),
    ("ITAR Debarred", "State Department ITAR debarred list"),
    ("Nonproliferation", "State Department nonproliferation sanctions"),
]

# ------------------------------------------------------------------ names

_PUNCT = re.compile(r"[^a-z0-9 ]+")
LEGAL_FORMS = {
    "the", "inc", "incorporated", "corp", "corporation", "llc", "ltd", "limited",
    "company", "co", "plc", "lp", "llp", "pllc", "pc", "sa", "ag", "gmbh", "bv", "nv",
    "srl", "spa", "pte", "pty", "oy", "ab", "as", "kk", "jsc", "ojsc", "cjsc", "pjsc",
    "ooo", "zao", "oao", "pao", "fze", "fzco", "fzc", "dba", "aka", "fka",
    "open", "joint", "stock", "closed", "public", "private", "sdn", "bhd",
}
# Words so common in company names that sharing one says nothing about being
# the same company. Not legal forms: a name made of nothing else
# ("GLOBAL SYSTEMS INC") is matched on all of it, exactly.
GENERIC = {
    "international", "intl", "global", "worldwide", "world", "technologies",
    "technology", "tech", "techs", "systems", "system", "solutions", "solution",
    "services", "service", "industries", "industry", "industrial", "enterprises",
    "enterprise", "trading", "trade", "traders", "aerospace", "aero", "aviation",
    "defense", "defence", "engineering", "engineers", "manufacturing", "research",
    "labs", "lab", "laboratories", "laboratory", "partners", "associates",
    "consulting", "consultants", "logistics", "energy", "group", "holdings",
    "holding", "management", "capital", "ventures", "america", "american", "usa",
    "us", "of", "and", "for", "new", "national", "general", "united", "science",
    "sciences", "scientific", "innovations", "innovation", "innovative", "dynamics",
    "advanced", "applied", "electronics", "electronic", "electric", "software",
    "digital", "networks", "network", "communications", "communication",
    "telecom", "materials", "products", "product", "development", "design",
    "designs", "resources", "import", "export", "imports", "exports", "investment",
    "investments", "integrated", "integration", "concepts", "corporate", "office",
    "agency", "center", "centre", "institute", "university", "foundation", "trust",
    "fund", "bank", "finance", "financial", "machinery", "equipment", "devices",
    "instruments", "precision", "power", "robotics", "space", "air", "marine",
    "maritime", "shipping", "transport", "transportation", "construction",
    "information", "data", "analytics", "machine", "machines",
    "medical", "health", "bio", "pharma", "chemical", "chemicals", "metals",
    "petroleum", "oil", "gas", "mining", "branch", "subsidiary", "division",
    "operations", "works", "factory", "plant", "east", "west", "north",
    "south", "asia", "pacific", "europe", "china", "russia", "iran", "middle",
}
# Shared words must be at least this long for a "variant" match.
VARIANT_MIN = 5
# One long word may differ by about one letter, and the whole names must
# still read alike.
SPELLING_MIN_LENGTH = 8
SPELLING_RATIO = 0.9
WHOLE_NAME_RATIO = 0.88


def tokens(name: str) -> list[str]:
    """A name's words, lower case, without punctuation or legal forms."""
    words = _PUNCT.sub(" ", (name or "").lower().replace("&", " and ")).split()
    return [w for w in words if w not in LEGAL_FORMS]


def distinctive(words: list[str]) -> list[str]:
    return [w for w in words if w not in GENERIC and not w.isdigit()]


@dataclass(frozen=True)
class Key:
    full: str                    # every word but legal forms, run together
    core: tuple[str, ...]        # the distinctive words, in order
    rest: frozenset[str] = frozenset()   # the generic words

    @property
    def core_text(self) -> str:
        return "".join(self.core)


def key_of(name: str) -> Key:
    words = tokens(name)
    core = distinctive(words)
    return Key("".join(words), tuple(core), frozenset(w for w in words if w not in core))


def _close(a: str, b: str) -> bool:
    return (min(len(a), len(b)) >= SPELLING_MIN_LENGTH and a[:2] == b[:2]
            and SequenceMatcher(None, a, b).ratio() >= SPELLING_RATIO)


def compare(a: Key, b: Key) -> str:
    """"exact", "core", "variant", or "" — how name `a` matches name `b`."""
    if not a.full or not b.full:
        return ""
    if a.full == b.full:
        return "exact"
    if not a.core or not b.core:
        return ""                # all generic words: only an exact match counts
    if a.core_text == b.core_text and len(a.core_text) >= 3:
        # One distinctive word is a common one often enough ("Dynamic",
        # "Phoenix") that it needs the rest of the name to agree too: the
        # same kind of company, or nothing else said by one of them.
        if (len(a.core) >= 2 or not a.rest or not b.rest or a.rest & b.rest):
            return "core"
        return ""
    if len(a.core) == len(b.core):
        pairs = list(zip(a.core, b.core, strict=True))
        if (all(x == y or _close(x, y) for x, y in pairs) and any(x != y for x, y in pairs)
                and SequenceMatcher(None, a.full, b.full).ratio() >= WHOLE_NAME_RATIO):
            return "variant"
        return ""
    short, long_ = (a.core, b.core) if len(a.core) < len(b.core) else (b.core, a.core)
    if (len(short) >= 2 and len(long_) == len(short) + 1 and set(short) <= set(long_)
            and all(len(w) >= VARIANT_MIN for w in short)):
        return "variant"
    return ""


# ------------------------------------------------------------------ places

COUNTRY_NAMES = {
    "RU": "Russia", "CN": "China", "HK": "Hong Kong", "IR": "Iran", "AE": "the UAE",
    "KP": "North Korea", "BY": "Belarus", "SY": "Syria", "CU": "Cuba", "VE": "Venezuela",
    "TR": "Turkey", "PK": "Pakistan", "IN": "India", "LB": "Lebanon", "MY": "Malaysia",
    "SG": "Singapore", "TW": "Taiwan", "KZ": "Kazakhstan", "AM": "Armenia",
    "UZ": "Uzbekistan", "KG": "Kyrgyzstan", "GE": "Georgia", "IQ": "Iraq",
    "AF": "Afghanistan", "GB": "the United Kingdom", "DE": "Germany", "FR": "France",
    "NL": "the Netherlands", "CY": "Cyprus", "LR": "Liberia", "MM": "Burma",
    "TH": "Thailand", "VN": "Vietnam", "KR": "South Korea", "JP": "Japan",
    "IL": "Israel", "EG": "Egypt", "SA": "Saudi Arabia", "OM": "Oman", "QA": "Qatar",
}
US = {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}
# Words that, added to a listed company's name, say "its U.S. arm".
US_ARM_WORDS = {"usa", "us", "america", "american", "americas"}


def countries(addresses: str) -> list[str]:
    """The country of each address, as the list gives it (the part after the
    last comma: usually a two-letter code)."""
    out = []
    for address in (addresses or "").split(";"):
        part = address.rsplit(",", 1)[-1].strip().upper().rstrip(".")
        if part and not part.isdigit() and len(part) <= 40:
            out.append(part)
    return list(dict.fromkeys(out))


def readable_countries(codes: list[str]) -> str:
    names = [COUNTRY_NAMES.get(c, c.title() if len(c) > 3 else c) for c in codes]
    return ", ".join(names[:4]) + (" and elsewhere" if len(names) > 4 else "")


# ------------------------------------------------------------------ entries

@dataclass
class Entry:
    entry_id: str
    name: str
    list_name: str              # readable, from LIST_NAMES
    source: str                 # the list as the file names it
    programs: str = ""
    other_names: list[str] = field(default_factory=list)
    addresses: str = ""
    listed_on: str = ""
    notice: str = ""            # Federal Register notice
    list_url: str = ""
    info_url: str = ""
    remarks: str = ""


def list_name(source: str) -> str:
    for start, readable in LIST_NAMES:
        if source.startswith(start):
            return readable
    return source.split(" - ")[0].strip() or "a U.S. screening list"


class Index:
    """Entries by the keys of every name they go by. An exact match is a
    dictionary hit; the others are looked for only among names sharing a
    distinctive word's first three letters, and decided by `compare`."""

    def __init__(self, entries: list[Entry]) -> None:
        self.entries = entries
        self.by_full: dict[str, list[tuple[int, str]]] = {}
        self.by_word: dict[str, list[tuple[int, str, Key]]] = {}
        for i, e in enumerate(entries):
            for name in dict.fromkeys([e.name, *e.other_names]):
                k = key_of(name)
                if not k.full:
                    continue
                self.by_full.setdefault(k.full, []).append((i, name))
                for w in set(k.core):
                    self.by_word.setdefault(w[:3], []).append((i, name, k))

    def lookup(self, name: str) -> list[tuple[Entry, str, str]]:
        """[(entry, the name of it that matched, kind)], strongest first, each
        entry once."""
        k = key_of(name)
        if not k.full:
            return []
        found: dict[int, tuple[str, str]] = {}
        rank = {"exact": 0, "core": 1, "variant": 2}

        def take(i: int, matched: str, kind: str) -> None:
            if i not in found or rank[kind] < rank[found[i][1]]:
                found[i] = (matched, kind)

        for i, matched in self.by_full.get(k.full, []):
            take(i, matched, "exact")
        if k.core:
            seen = set()
            for w in set(k.core):
                for i, matched, other in self.by_word.get(w[:3], []):
                    if (i, matched) in seen or i in found:
                        continue
                    seen.add((i, matched))
                    kind = compare(k, other)
                    if kind:
                        take(i, matched, kind)
        out = [(self.entries[i], matched, kind) for i, (matched, kind) in found.items()]
        return sorted(out, key=lambda x: (rank[x[2]], x[0].list_name, x[0].name))


# ------------------------------------------------------------------ reading

def _split(value: str) -> list[str]:
    return [p.strip() for p in (value or "").split(";") if p.strip()]


def from_csl(text: str) -> list[Entry]:
    """Entries from the Consolidated Screening List CSV. People, ships and
    aircraft are left out: the screen looks for companies."""
    out = []
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        kind = (row.get("type") or "").strip().lower()
        if kind in ("individual", "vessel", "aircraft"):
            continue
        name = (row.get("name") or "").strip()
        if not name:
            continue
        source = (row.get("source") or "").strip()
        out.append(Entry(
            entry_id=(row.get("_id") or row.get("entity_number") or name)[:40],
            name=name, list_name=list_name(source), source=source,
            programs=(row.get("programs") or "").strip(),
            other_names=_split(row.get("alt_names", "")),
            addresses=(row.get("addresses") or "").strip(),
            listed_on=(row.get("start_date") or "").strip(),
            notice=(row.get("federal_register_notice") or "").strip(),
            list_url=(row.get("source_list_url") or "").strip(),
            info_url=(row.get("source_information_url") or "").strip(),
            remarks=(row.get("remarks") or "").strip()))
    return out


def _ofac_rows(text: str) -> list[list[str]]:
    return [[c.strip().strip('"') for c in row]
            for row in csv.reader(io.StringIO(text or "")) if row]


def _blank(value: str) -> str:
    return "" if value.strip() in ("-0-", "") else value.strip()


def from_ofac(primary: str, aliases: str, list_label: str) -> list[Entry]:
    """Entries from one of Treasury's own files (SDN or consolidated non-SDN)
    and its alias file. Columns: ent_num, name, type, program, ... remarks."""
    other: dict[str, list[str]] = {}
    for row in _ofac_rows(aliases):
        if len(row) >= 4 and _blank(row[3]):
            other.setdefault(row[0], []).append(row[3])
    out = []
    for row in _ofac_rows(primary):
        if len(row) < 4:
            continue
        kind = _blank(row[2]).lower()
        if kind in ("individual", "vessel", "aircraft"):
            continue
        out.append(Entry(
            entry_id=row[0], name=row[1], list_name=list_label, source=list_label,
            programs=_blank(row[3]), other_names=other.get(row[0], []),
            remarks=_blank(row[11]) if len(row) > 11 else "",
            info_url=OFAC_DETAILS + row[0]))
    return out


class ScreeningLists:
    """The lists, read once per process, and matching against them."""
    name = "screening_lists"

    def __init__(self, http) -> None:
        self.http = http
        self._index: Index | None = None
        self.coverage = ""          # what was read, or why not, for the run's notes
        self.complete = False       # every list, not Treasury's alone

    def _get(self, url: str) -> str:
        resp = self.http.get(url, cache_ttl=DAY, timeout=60, max_retries=2)
        return resp.get("text") or "" if resp.get("status") == 200 else ""

    def index(self) -> Index:
        if self._index is not None:
            return self._index
        entries: list[Entry] = []
        text = self._get(CSL_URL)
        if text:
            entries = from_csl(text)
        if entries:
            self.complete = True
            self.coverage = ""
        else:
            for primary, aliases, label in (("sdn", "alt", "Treasury SDN list"),
                                            ("cons_prim", "cons_alt",
                                             "Treasury non-SDN sanctions lists")):
                body = self._get(OFAC_FILES[primary])
                if body:
                    entries += from_ofac(body, self._get(OFAC_FILES[aliases]), label)
            self.coverage = (
                "The Consolidated Screening List (trade.gov) could not be downloaded, so "
                "contractors were checked against Treasury's sanctions lists only — not "
                "the Commerce Entity List, Military End User, Unverified or Denied "
                "Persons lists, or State's ITAR debarments."
                if entries else
                "No U.S. screening list could be downloaded (trade.gov or Treasury), so "
                "no contractor was checked against the sanctions and export lists.")
        log.info("screening lists: %d entries%s", len(entries),
                 "" if self.complete else " (Treasury only)")
        self._index = Index(entries)
        return self._index

    @property
    def available(self) -> bool:
        return bool(self.index().entries)

    def screen(self, name: str, whose: str = "contractor") -> list[Document]:
        """Documents for the list entries this name matches, strongest first.
        `whose`: whose name it is — "contractor", "parent", or a holder."""
        if len(key_of(name).full) < 3:
            return []
        out = []
        for entry, matched, kind in self.index().lookup(name)[:5]:
            out.append(self._document(entry, matched, kind, name, whose))
        return out

    @staticmethod
    def _document(e: Entry, matched: str, kind: str, name: str, whose: str) -> Document:
        how = {"exact": "the same name", "core": "the same distinctive name",
               "variant": "a close variant of the name"}[kind]
        lines = [
            f"{e.list_name}: {e.name}" + (f" (programs: {e.programs})" if e.programs else ""),
            f"Matched {whose} name '{name}' to '{matched}' — {how}."
            + ("" if matched == e.name else f" '{matched}' is another name for {e.name}."),
        ]
        if e.addresses:
            lines.append(f"Listed addresses: {e.addresses[:400]}")
        if e.listed_on or e.notice:
            lines.append("Listed" + (f" on {e.listed_on}" if e.listed_on else "")
                         + (f" ({e.notice})" if e.notice else "") + ".")
        if e.remarks:
            lines.append(f"Remarks: {e.remarks[:300]}")
        lines.append("Name match only — confirm against the entry's identifiers and "
                     "addresses before acting.")
        where = countries(e.addresses)
        return Document(
            source="screening_lists", key=f"list:{e.entry_id}",
            title=f"{e.list_name}: {e.name}",
            url=e.info_url or e.list_url or CSL_PAGE, text="\n".join(lines),
            doc_type="sanctions_match",
            meta={"match": kind, "list": e.list_name, "list_source": e.source,
                  "programs": e.programs, "entry": e.name, "matched_name": matched,
                  "screened_name": name, "whose": whose, "list_url": e.list_url,
                  # Where the listed company is, and whether this name reads as
                  # its U.S. arm ("Hikvision USA"): a non-exact match to a
                  # company listed only abroad is recorded, not flagged.
                  "listed_countries": readable_countries(where),
                  "listed_abroad": bool(where) and not (set(where) & US),
                  "us_arm": bool(set(tokens(name)) & US_ARM_WORDS)})
