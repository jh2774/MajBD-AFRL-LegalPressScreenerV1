"""How people type the names of agencies, and how the records spell them.

Awards record an agency the long way — "Department of the Navy", "Defense
Advanced Research Projects Agency" — and nobody types that. They type NAVSEA,
or DARPA, or DoD. A search that only matches the recorded spelling answers
"nothing found" to the most natural thing a reader can enter, which is
indistinguishable from the agency having no awards.

The mapping runs both ways on purpose: typing an abbreviation finds the long
name, and typing part of the long name still works, because an alias is
matched as a substring like any other term.

Not exhaustive, and not meant to be. It covers the defence organisations this
tool screens most and the civilian departments that turn up alongside them.
Adding one is a line, and an unknown abbreviation degrades to an ordinary
substring search rather than to an error.
"""
from __future__ import annotations

# Alias -> the spellings that should match it. Lower case throughout; matching
# is substring, so "navy" reaches "Department of the Navy".
AGENCY_ALIASES: dict[str, tuple[str, ...]] = {
    # --- departments -------------------------------------------------------
    "dod": ("department of defense",),
    "dept of defense": ("department of defense",),
    "usaf": ("department of the air force", "air force"),
    "af": ("air force",),
    "usn": ("department of the navy", "navy"),
    "navy": ("department of the navy",),
    "usa": ("department of the army", "army"),
    "army": ("department of the army",),
    "usmc": ("marine corps",),
    "uscg": ("coast guard",),
    "ussf": ("space force",),
    # --- defence agencies and commands -------------------------------------
    "darpa": ("defense advanced research projects agency",),
    "dla": ("defense logistics agency",),
    "disa": ("defense information systems agency",),
    "dtra": ("defense threat reduction agency",),
    "mda": ("missile defense agency",),
    "dha": ("defense health agency",),
    "dcsa": ("defense counterintelligence and security agency",),
    "nga": ("national geospatial-intelligence agency",),
    "nsa": ("national security agency",),
    "nro": ("national reconnaissance office",),
    "socom": ("special operations command",),
    "ussocom": ("special operations command",),
    "transcom": ("transportation command",),
    "centcom": ("central command",),
    "navsea": ("naval sea systems command",),
    "navair": ("naval air systems command",),
    "navwar": ("naval information warfare systems command",),
    "spawar": ("naval information warfare systems command",),
    "navfac": ("naval facilities engineering",),
    "onr": ("office of naval research",),
    "afrl": ("air force research laboratory",),
    "aflcmc": ("air force life cycle management center",),
    "arl": ("army research laboratory",),
    "acc": ("army contracting command",),
    "usace": ("corps of engineers",),
    "ami": ("army materiel command",),
    # --- civilian departments ----------------------------------------------
    "dhs": ("department of homeland security",),
    "doe": ("department of energy",),
    "dot": ("department of transportation",),
    "hhs": ("department of health and human services",),
    "doj": ("department of justice",),
    "dos": ("department of state",),
    "state": ("department of state",),
    "usda": ("department of agriculture",),
    "doc": ("department of commerce",),
    "doi": ("department of the interior",),
    "dol": ("department of labor",),
    "hud": ("department of housing and urban development",),
    "va": ("department of veterans affairs", "veterans affairs"),
    "treasury": ("department of the treasury",),
    "ed": ("department of education",),
    # --- civilian agencies --------------------------------------------------
    "nasa": ("national aeronautics and space administration",),
    "nsf": ("national science foundation",),
    "nih": ("national institutes of health",),
    "cdc": ("centers for disease control",),
    "fda": ("food and drug administration",),
    "cms": ("centers for medicare",),
    "epa": ("environmental protection agency",),
    "gsa": ("general services administration",),
    "nrc": ("nuclear regulatory commission",),
    "noaa": ("national oceanic and atmospheric administration",),
    "faa": ("federal aviation administration",),
    "tsa": ("transportation security administration",),
    "fema": ("federal emergency management agency",),
    "cbp": ("customs and border protection",),
    "ice": ("immigration and customs enforcement",),
    "nist": ("national institute of standards and technology",),
    "sba": ("small business administration",),
    "ssa": ("social security administration",),
}


def expand_agency(query: str) -> list[str]:
    """Every spelling worth matching for what was typed.

    The query itself always comes first, so an unknown abbreviation behaves
    exactly as it did before this module existed.
    """
    q = " ".join((query or "").lower().split())
    if not q:
        return []
    out = [q]
    for spelling in AGENCY_ALIASES.get(q, ()):
        if spelling not in out:
            out.append(spelling)
    # A typed long name should also be reachable from its abbreviation's other
    # spellings — "air force" finding "Department of the Air Force".
    for alias, spellings in AGENCY_ALIASES.items():
        if alias == q:
            continue
        for spelling in spellings:
            if q in spelling and spelling not in out:
                out.append(spelling)
    return out


# Characters that separate one word from the next. Deliberately short: these
# are the ways a person writes a list of names, and nothing else.
_SEPARATORS = ",;|/\t\r\n "
# Trimmed from each end of a word. `%` and `_` are absent on purpose: they are
# SQL wildcards, and a search for one must stay a search for that character.
_EDGE_PUNCTUATION = ".:!?()[]{}\"'`"


def tokens(query: str) -> list[str]:
    """Words to match independently, so word order does not matter.

    "doe jane" and "jane doe" are the same person, and a contracting officer is
    recorded one way while being spoken of the other.

    Only whitespace and list punctuation separate words. Everything else stays
    inside the token, which matters more than it looks: splitting on all
    punctuation would make "SPACE_SYSTEMS" into two words that also match
    "SPACEXSYSTEMS", and in a tool whose job is attribution, matching the
    wrong company is the expensive failure. A `%` or `_` typed in a search
    stays a literal character and is escaped downstream.
    """
    text = (query or "").lower()
    for sep in _SEPARATORS[:-1]:
        text = text.replace(sep, " ")
    # Punctuation at the edge of a word is noise — a middle initial typed
    # "R." has to find a record that stores it as "R". Inside a word it is
    # part of the name and stays, which is what keeps "space_systems" one
    # token rather than two that also match "spacexsystems".
    return [t for t in (w.strip(_EDGE_PUNCTUATION) for w in text.split(" ")) if t]
