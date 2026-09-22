"""SEC EDGAR — the richest keyless source for both risk families.

Three things are pulled:
  1. company_tickers.json  -> name/ticker -> CIK resolution
  2. submissions/CIK*.json -> recent filings, with the 8-K item codes that map
     onto our risk families (5.01 change in control, 1.01 material agreement,
     2.03 direct financial obligation)
  3. full-text search       -> exhibits containing "intellectual property
     security agreement" and similar, which is where IP collateralisation is
     actually documented

EDGAR requires a User-Agent with a contact address; see config.user_agent.
"""
from __future__ import annotations

import logging
import re

from ..models import Document

log = logging.getLogger("foci.edgar")

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
FTS = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data"

# 8-K items that carry FOCI / IP-encumbrance meaning.
ITEM_MEANING = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate a Financial Obligation",
    "3.02": "Unregistered Sales of Equity Securities",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure/Election of Directors or Officers",
}
INTERESTING_FORMS = {"8-K", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "D", "D/A",
                     "10-K", "10-Q", "25", "SC 14D9", "DEFM14A", "S-4"}

IP_COLLATERAL_QUERIES = [
    '"intellectual property security agreement"',
    '"patent security agreement"',
    '"collateral assignment of patents"',
]

_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"[ \t\r\f\v]+")


def html_to_text(html: str, limit: int = 60000) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        for bad in soup(["script", "style", "noscript"]):
            bad.decompose()
        text = soup.get_text("\n")
    except Exception:
        text = _TAG_RX.sub(" ", html)
    text = _WS_RX.sub(" ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)[:limit]


class EdgarConnector:
    name = "sec_edgar"

    def __init__(self, http) -> None:
        self.http = http
        self._tickers: dict | None = None

    # ------------------------------------------------------------ resolution
    def _load_tickers(self) -> dict:
        if self._tickers is None:
            resp = self.http.get(TICKERS_URL)
            self._tickers = resp.get("json") or {}
        return self._tickers

    def resolve_cik(self, company_name: str) -> tuple[str, str]:
        """Best-effort name -> (CIK10, matched title). Conservative on purpose:
        a wrong CIK produces confident nonsense."""
        data = self._load_tickers()
        if not data:
            return "", ""
        target = _normalise(company_name)
        if not target:
            return "", ""
        best: tuple[float, str, str] = (0.0, "", "")
        for row in data.values():
            title = row.get("title") or ""
            score = _similarity(target, _normalise(title))
            if score > best[0]:
                best = (score, str(row.get("cik_str") or "").zfill(10), title)
        if best[0] >= 0.86:
            return best[1], best[2]
        return "", ""

    # -------------------------------------------------------------- filings
    def recent_filings(self, cik: str, limit: int = 25) -> list[Document]:
        if not cik:
            return []
        resp = self.http.get(SUBMISSIONS.format(cik=cik))
        data = resp.get("json") or {}
        recent = ((data.get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        docs: list[Document] = []
        company = data.get("name") or ""
        for i, form in enumerate(forms):
            if len(docs) >= limit:
                break
            if form not in INTERESTING_FORMS:
                continue
            accession = (recent.get("accessionNumber") or [""] * len(forms))[i]
            filed = (recent.get("filingDate") or [""] * len(forms))[i]
            items = (recent.get("items") or [""] * len(forms))[i]
            primary = (recent.get("primaryDocument") or [""] * len(forms))[i]
            acc_plain = accession.replace("-", "")
            url = f"{ARCHIVE}/{int(cik)}/{acc_plain}/{primary}" if primary else \
                  f"{ARCHIVE}/{int(cik)}/{acc_plain}"
            codes = [c.strip() for c in (items or "").split(",") if c.strip()]
            # Item *codes* only, never their English labels. The labels contain
            # words the prose rules look for ("Receivership", "Changes in
            # Control"), so including them would make the tool match text it
            # wrote itself. The codes are carried in meta and handled by a
            # dedicated structured rule instead.
            docs.append(Document(
                source="sec_edgar", key=f"{cik}:{accession}",
                title=f"{company} {form} filed {filed}", url=url,
                text=(f"Form {form} filed {filed} by {company}."
                      + (f" Reported items: {', '.join(codes)}." if codes else "")),
                published=filed, doc_type=form,
                meta={"cik": cik, "accession": accession, "items": items,
                      "item_codes": codes, "form": form, "company": company}))
        return docs

    def fetch_filing_text(self, doc: Document) -> Document:
        """Pull the actual document body so rules see the language, not the label."""
        if not doc.url:
            return doc
        resp = self.http.get(doc.url)
        if resp.get("status") == 200 and resp.get("text"):
            body = html_to_text(resp["text"])
            if body:
                doc.text = f"{doc.text}\n\n{body}"
        return doc

    # ------------------------------------------------------- full-text search
    def full_text_search(self, company_name: str, cik: str = "",
                         queries: list[str] | None = None, forms: str = "",
                         limit: int = 5) -> list[Document]:
        """Find filings by THIS entity whose text contains IP-collateral language.

        Two non-obvious requirements, both learned the hard way:

        1. The search MUST be constrained by CIK. EDGAR's `q` parameter does not
           AND a company name with a phrase — querying
           '"patent security agreement" "Lockheed Martin"' returns other
           registrants' exhibits, which would then be attributed to Lockheed.
           Without a confident CIK we return nothing rather than guess.

        2. The returned Document's `text` must be the *filed exhibit*, never a
           summary we compose. A summary that names the phrase we searched for
           will match the very rule that looks for that phrase, and the tool
           will confidently flag its own query string as evidence.
        """
        if not cik:
            return []
        out: list[Document] = []
        seen: set[str] = set()
        for q in (queries or IP_COLLATERAL_QUERIES):
            params = {"q": q, "ciks": cik}
            if forms:
                params["forms"] = forms
            resp = self.http.get(FTS, params=params)
            data = resp.get("json") or {}
            hits = ((data.get("hits") or {}).get("hits") or [])
            for h in hits[:limit]:
                src = h.get("_source") or {}
                doc_id = h.get("_id") or ""
                if not doc_id or doc_id in seen:
                    continue
                # Belt and braces: confirm the hit really is this registrant.
                hit_ciks = {str(c).zfill(10) for c in (src.get("ciks") or [])}
                if cik.zfill(10) not in hit_ciks:
                    continue
                seen.add(doc_id)
                accession, _, filename = doc_id.partition(":")
                url = f"{ARCHIVE}/{int(cik)}/{accession.replace('-', '')}/{filename}"
                body = self._fetch_body(url)
                if not body:
                    # No verifiable source text -> no document. Reporting the
                    # hit without its text would mean flagging on the strength
                    # of our own query.
                    continue
                names = ", ".join(src.get("display_names") or [])
                out.append(Document(
                    source="sec_edgar", key=f"fts:{doc_id}",
                    title=f"{names} {src.get('form', '')} {src.get('file_type', '')}".strip(),
                    url=url, text=body,
                    published=src.get("file_date") or "",
                    doc_type=src.get("form") or "",
                    meta={"matched_query": q, "cik": cik, "accession": accession,
                          "file_type": src.get("file_type", ""),
                          "verified_registrant": True}))
        return out

    def _fetch_body(self, url: str) -> str:
        resp = self.http.get(url)
        if resp.get("status") != 200 or not resp.get("text"):
            return ""
        return html_to_text(resp["text"])


def _normalise(name: str) -> str:
    name = name.upper()
    for suffix in (" CORPORATION", " CORP", " INCORPORATED", " INC", " COMPANY",
                   " CO", " LLC", " L.L.C.", " LTD", " LIMITED", " PLC", " HOLDINGS",
                   " GROUP", " TECHNOLOGIES", " TECHNOLOGY", " SYSTEMS", " LP",
                   " L.P.", " THE"):
        name = name.replace(suffix, " ")
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", name).split())


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    # Require the distinctive first token to agree; "GENERAL DYNAMICS" and
    # "GENERAL MILLS" should not merge.
    head_bonus = 0.25 if a.split()[0] == b.split()[0] else 0.0
    return min(1.0, jaccard + head_bonus)
