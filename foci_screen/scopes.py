"""Whole offices and agencies: every contractor under one, watched at once.

A screen of an agency takes its largest awards and screens a handful of their
contractors. Someone at AFWERX wants every company AFWERX contracts with, and
AFWERX is not an agency in USAspending's sense at all: it is a contracting
office inside AFRL, which is itself inside the Department of the Air Force,
the lowest level USAspending can filter on. FPDS, the federal procurement
record, can be searched by contracting office, so a scope is one of:

  * ``offices:FA2280+FA8649`` — named contracting offices (FPDS);
  * ``office-name:AFRL`` — every office whose FPDS name contains a word, which
    is how "every AFRL office" is said without listing them;
  * ``agency:Department of Defense|Department of the Air Force`` — an
    awarding agency, with an optional sub-agency (USAspending).

A watch on a scope collects its awards month by month, keeps every contractor
in it, and screens them in turn, a few each run (`pipeline.Screener`). A
portfolio of a scope follows it: whoever the scope's awards name is in it.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass

OFFICE_CODE = re.compile(r"^[A-Z0-9]{6}$")


@dataclass(frozen=True)
class Scope:
    kind: str                  # offices | office-name | agency
    values: tuple[str, ...]    # office codes; one name word; (agency, sub-agency)
    label: str = ""
    about: str = ""

    @property
    def id(self) -> str:
        if self.kind == "offices":
            return "offices:" + "+".join(sorted(self.values))
        if self.kind == "office-name":
            return f"office-name:{self.values[0]}"
        return "agency:" + "|".join(v for v in self.values if v)

    @property
    def source(self) -> str:
        """Where its awards come from: fpds or usaspending."""
        return "usaspending" if self.kind == "agency" else "fpds"

    def title(self) -> str:
        if self.label:
            return self.label
        if self.kind == "offices":
            return "Contracting office " + ", ".join(self.values)
        if self.kind == "office-name":
            return f"Every {self.values[0]} contracting office"
        return self.values[-1] if len(self.values) > 1 and self.values[1] else self.values[0]

    # -------------------------------------------------------------- FPDS
    def fpds_queries(self) -> list[str]:
        if self.kind == "offices":
            return [f'CONTRACTING_OFFICE_ID:"{code}"' for code in sorted(self.values)]
        if self.kind == "office-name":
            return [f'CONTRACTING_OFFICE_NAME:"{self.values[0]}"']
        return []

    # ------------------------------------------------------------- SQL
    def sql(self, alias: str = "") -> tuple[str, list]:
        """A WHERE clause matching this scope's rows in `contracts`."""
        p = f"{alias}." if alias else ""
        if self.kind == "offices":
            marks = ",".join("?" for _ in self.values)
            return f"{p}office_id IN ({marks})", list(self.values)
        if self.kind == "office-name":
            return f"UPPER({p}office_name) LIKE ?", [f"%{self.values[0].upper()}%"]
        agency, sub = (self.values + ("",))[:2]
        clause, params = f"LOWER({p}agency)=?", [agency.lower()]
        if sub:
            clause += f" AND LOWER({p}sub_agency)=?"
            params.append(sub.lower())
        return clause, params

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.title(),
                "about": self.about, "source": self.source}


# Named scopes people ask for by name. Each was checked against FPDS: FA8649
# is "USAF SBIR STTR CNTRCTNG AFRL" and FA2280 "AFRL/RGKA" (RG is AFWERX's
# office symbol); "AFRL" in an office's FPDS name covers AFRL's offices at
# Wright-Patterson, Kirtland, Rome, Eglin and elsewhere.
PRESETS: dict[str, Scope] = {
    "afwerx": Scope(
        "offices", ("FA2280", "FA8649"), "AFWERX",
        "AFWERX's contracting: the Air Force SBIR/STTR office (FA8649) and AFRL/RGKA "
        "(FA2280). Small-business research awards and other transactions."),
    "afrl": Scope(
        "office-name", ("AFRL",), "Air Force Research Laboratory (AFRL)",
        "Every contracting office whose federal record names it AFRL, AFWERX's "
        "included."),
}
# Other ways the presets are typed.
PRESET_ALIASES = {
    "afwerx": "afwerx", "af werx": "afwerx", "afrl/rg": "afwerx", "afventures": "afwerx",
    "afrl": "afrl", "air force research laboratory": "afrl",
    "air force research lab": "afrl", "air force research": "afrl",
}


class ScopeError(ValueError):
    """A scope that cannot be read, with a reason a person can act on."""


def parse(text: str) -> Scope:
    """A scope from its id, a preset's name, or a bare office code."""
    raw = (text or "").strip()
    low = raw.lower()
    if low in PRESET_ALIASES:
        return PRESETS[PRESET_ALIASES[low]]
    kind, _, rest = raw.partition(":")
    kind = kind.strip().lower()
    if kind == "offices":
        codes = tuple(sorted({c.strip().upper() for c in rest.split("+") if c.strip()}))
        if not codes or not all(OFFICE_CODE.match(c) for c in codes):
            raise ScopeError("An office is its six-character code, such as FA8649.")
        preset = next((p for p in PRESETS.values()
                       if p.kind == "offices" and p.values == codes), None)
        return preset or Scope("offices", codes)
    if kind == "office-name":
        word = rest.strip().upper()
        if len(word) < 3:
            raise ScopeError("Name at least three letters of the office.")
        preset = next((p for p in PRESETS.values()
                       if p.kind == "office-name" and p.values == (word,)), None)
        return preset or Scope("office-name", (word,))
    if kind == "agency":
        agency, _, sub = rest.partition("|")
        if len(agency.strip()) < 3:
            raise ScopeError("Name the agency, as USAspending spells it.")
        return Scope("agency", (agency.strip(), sub.strip()))
    if OFFICE_CODE.match(raw.upper()) and any(ch.isdigit() for ch in raw):
        return Scope("offices", (raw.upper(),))
    raise ScopeError("Not a scope this site knows: name an office code such as FA8649, "
                     "AFWERX, AFRL, or an agency.")


def suggest(q: str) -> list[Scope]:
    """Scopes for what is being typed into the search box."""
    low = (q or "").strip().lower()
    if len(low) < 2:
        return []
    found: list[Scope] = []
    for alias, name in PRESET_ALIASES.items():
        if (alias.startswith(low) or low.startswith(alias)) and PRESETS[name] not in found:
            found.append(PRESETS[name])
    if OFFICE_CODE.match(low.upper()) and any(ch.isdigit() for ch in low):
        code = Scope("offices", (low.upper(),))
        if not any(code.values == s.values for s in found):
            found.append(code)
    return found


def month_range(month: str) -> tuple[str, str]:
    """'2026-04' -> ('2026/04/01', '2026/04/30'), as FPDS writes dates."""
    year, mon = (int(x) for x in month.split("-"))
    last = calendar.monthrange(year, mon)[1]
    return f"{year}/{mon:02d}/01", f"{year}/{mon:02d}/{last:02d}"


def months_back(today, n: int) -> list[str]:
    """The last `n` months, this one first: '2026-11', '2026-10', ..."""
    out, year, mon = [], today.year, today.month
    for _ in range(max(1, n)):
        out.append(f"{year}-{mon:02d}")
        mon -= 1
        if mon == 0:
            year, mon = year - 1, 12
    return out
