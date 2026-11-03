"""Who an email is to, by name, so it can open with "Hello Jordan,".

First names rather than "Mr" or "Ms": a title has to be guessed from a name,
and the guess is wrong for enough people that an email opening with the wrong
one does more harm than one opening with their first name. A notice to a
contracting officer keeps its formal "Dear Jordan Smith," (`render.py`).

Where the name comes from, best first:

  * the person's account, where they gave one;
  * a contracting officer's name, from the awards that name them;
  * the address itself, when it reads as first.last@ — the usual pattern at
    agencies and contractors alike;
  * otherwise no name: "Hello," is better than a wrong one.
"""
from __future__ import annotations

import re

# Mailbox words that are not anybody's first name: contracts.team@ is a desk,
# and "Hello Contracts," reads as a broken tool.
ROLE_WORDS = frozenset("""
    admin administrator alerts accounts billing business care careers compliance
    contact contracts contracting desk dev do enquiries finance foci general help
    hello hr info inquiries it legal mail marketing media news no noreply
    notifications office ops operations orders payments pmo procurement purchasing
    reply sales security service services staff support team test the users web
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*[A-Za-z]$")


def _cap(word: str) -> str:
    """JORDAN -> Jordan; ANNE-MARIE -> Anne-Marie; o'brien -> O'Brien."""
    return "-".join("'".join(p[:1].upper() + p[1:].lower() for p in part.split("'"))
                    for part in word.split("-"))


def first_name(full_name: str) -> str:
    """The first name in a full name, as a greeting would use it.

    "Jordan A. Smith" -> "Jordan"; "SMITH, JORDAN A" -> "Jordan" (the way
    award records write it). "" when there is no usable word.
    """
    text = (full_name or "").strip()
    if "," in text:                       # "Last, First Middle"
        text = text.split(",", 1)[1]
    words = text.split()
    # Only the first word: "J. Smith" has no first name to use, and "Smith"
    # would be the wrong one.
    word = words[0].strip(".") if words else ""
    return _cap(word) if len(word) >= 2 and _WORD.match(word) else ""


def first_name_from_address(address: str) -> str:
    """"Jordan" from jordan.smith@ or jordan_smith@; "" from anything less
    certain — jsmith@, info@, contracts.team@, j.smith@."""
    local = (address or "").split("@", 1)[0]
    parts = [p for p in re.split(r"[._]+", local) if p]
    if len(parts) < 2:
        return ""
    if any(re.sub(r"\d+$", "", p).lower() in ROLE_WORDS for p in parts):
        return ""
    first = re.sub(r"\d+$", "", parts[0])
    if len(first) < 2 or not _WORD.match(first):
        return ""
    return _cap(first)


def first_name_for(store, address: str) -> str:
    """The best first name this site has for `address` (see the module note)."""
    known = ""
    if store is not None:
        try:
            known = store.known_name(address)
        except Exception:                 # noqa: BLE001 - a greeting is optional
            known = ""
    return first_name(known) or first_name_from_address(address)


def greeting(name: str) -> str:
    return f"Hello {name}," if name else "Hello,"


def greet(text: str, html: str | None, name: str) -> tuple[str, str | None]:
    """The email with its greeting on top, in both versions."""
    line = greeting(name)
    text = f"{line}\n\n{text}"
    if html:
        from html import escape
        para = f"<p>{escape(line)}</p>"
        # Inside the outer <div> that carries the email's font, where there is one.
        if html.startswith("<div") and ">" in html:
            cut = html.index(">") + 1
            html = html[:cut] + para + html[cut:]
        else:
            html = para + html
    return text, html
