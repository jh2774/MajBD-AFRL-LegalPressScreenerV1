"""SAM.gov exclusions — parties barred from federal contracts, from the public extract.

The entity API is closed without a key, and its pages are a script shell over
that API. Exclusions are different: SAM.gov publishes every active exclusion
as a daily public file, listed and served by its own file service with no key
and no login, under a `robots.txt` that permits it. This reads that file.

    https://sam.gov/data-services/Exclusions/Public%20V2?privacy=Public

The extract (about 12 MB zipped, 169,000 records in October 2026) carries each
excluded party's UEI and CAGE code, so a contractor is matched on the
identifiers a screen already has, and only failing those on its exact name. It
is indexed once a day into a small SQLite file beside the HTTP cache, so a
screen looks records up rather than holding the list in memory.

SAM.gov's one stated condition is that users read the exclusion record
completely, so the evidence carries every field of the record, not a summary.
"""
from __future__ import annotations

import csv
import io
import logging
import sqlite3
import time
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import quote

from ..models import Document
from .sec_edgar import normalise_holder

log = logging.getLogger("foci.sam_exclusions")

LIST_URL = ("https://sam.gov/api/prod/fileextractservices/v1/api/listfiles"
            "?domain=Exclusions%2FPublic%20V2&privacy=Public")
DOWNLOAD_URL = ("https://sam.gov/api/prod/fileextractservices/v1/api/download/"
                "Exclusions/Public%20V2/{name}?privacy=Public")
PAGE_URL = "https://sam.gov/data-services/Exclusions/Public%20V2?privacy=Public"

# A person, not a business. Matched only by UEI or CAGE, never by name: a
# contractor sharing a name with an excluded individual says nothing.
INDIVIDUAL = "Individual"

REFRESH_SECONDS = 24 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS exclusions (
    sam_number TEXT, classification TEXT, name TEXT, norm TEXT,
    uei TEXT, cage TEXT, record TEXT
);
CREATE INDEX IF NOT EXISTS idx_excl_uei ON exclusions(uei);
CREATE INDEX IF NOT EXISTS idx_excl_cage ON exclusions(cage);
CREATE INDEX IF NOT EXISTS idx_excl_norm ON exclusions(norm);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class SAMExclusionsConnector:
    name = "sam_exclusions"

    def __init__(self, http, cache_dir: str = ".cache") -> None:
        self.http = http
        self.index_path = Path(cache_dir) / "sam_exclusions.db"
        self._db: sqlite3.Connection | None = None
        self.file_name = ""
        self.unavailable_reason = ""

    # ---------------------------------------------------------------- index
    def _ensure_index(self) -> sqlite3.Connection | None:
        """The day's index, building it when it is missing or a day old.

        A failed refresh keeps the previous index rather than none: yesterday's
        exclusions are a far better screen than no exclusions, and the run
        notes say which file was read.
        """
        if self._db is not None:
            return self._db
        fresh = (self.index_path.is_file()
                 and time.time() - self.index_path.stat().st_mtime < REFRESH_SECONDS)
        if not fresh:
            try:
                self._build()
            except Exception as exc:          # a source outage must not sink the run
                log.warning("SAM exclusions refresh failed: %s", exc)
                self.unavailable_reason = str(exc)
        if not self.index_path.is_file():
            return None
        self._db = sqlite3.connect(f"file:{self.index_path}?mode=ro", uri=True,
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        row = self._db.execute("SELECT value FROM meta WHERE key='file'").fetchone()
        self.file_name = row["value"] if row else ""
        return self._db

    def _build(self) -> None:
        listing = self.http.get(LIST_URL).get("json") or {}
        files = ((listing.get("_embedded") or {}).get("customS3ObjectSummaryList") or [])
        names = [f.get("displayKey") or "" for f in files
                 if (f.get("displayKey") or "").upper().endswith(".ZIP")]
        if not names:
            raise RuntimeError("SAM.gov listed no exclusions extract")
        # Names end in a two-digit year and the day of the year
        # (…_V2_26282.ZIP); as text that compares correctly, across years too.
        newest = max(names, key=_julian_key)
        status, body = self.http.get_bytes(DOWNLOAD_URL.format(name=quote(newest)),
                                           timeout=180, max_bytes=60_000_000)
        if status != 200 or body[:2] != b"PK":
            raise RuntimeError(f"SAM.gov returned {status} for {newest}")
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".partial")
        tmp.unlink(missing_ok=True)
        count = build_index(body, tmp, newest)
        tmp.replace(self.index_path)
        log.info("indexed %d SAM exclusions from %s", count, newest)

    # ---------------------------------------------------------------- match
    def screen(self, name: str, uei: str = "", cage: str = "",
               parent_name: str = "", parent_uei: str = "") -> list[Document]:
        """Exclusions of the contractor, then of its parent, by the best evidence.

        A UEI or CAGE match identifies the excluded party as this contractor.
        A name match does not, and is labelled as one; it is tried only for
        firms and special entities, and only when no identifier matched.
        """
        db = self._ensure_index()
        if db is None:
            return []
        out: list[Document] = []
        seen: set[str] = set()

        def add(rows, basis: str, whose: str) -> None:
            for r in rows:
                if r["sam_number"] in seen:
                    continue
                seen.add(r["sam_number"])
                out.append(self._to_doc(r, basis, whose))

        for value, column in ((uei, "uei"), (cage, "cage")):
            if value:
                add(db.execute(f"SELECT * FROM exclusions WHERE {column}=?",
                               (value.strip().upper(),)).fetchall(), column, "contractor")
        if not out and name:
            norm = normalise_holder(name)
            if norm:
                add(db.execute("SELECT * FROM exclusions WHERE norm=? AND classification!=?",
                               (norm, INDIVIDUAL)).fetchall(), "name", "contractor")
        if parent_uei and parent_uei.strip().upper() != (uei or "").strip().upper():
            add(db.execute("SELECT * FROM exclusions WHERE uei=?",
                           (parent_uei.strip().upper(),)).fetchall(), "uei", "parent")
        return out[:10]

    def _to_doc(self, row: sqlite3.Row, basis: str, whose: str) -> Document:
        record = dict(_decode(row["record"]))
        text = "\n".join(f"{k}: {v}" for k, v in record.items() if v)
        return Document(
            source="sam_exclusions",
            key=f"samexcl:{row['sam_number'] or row['uei'] or row['norm']}",
            title=f"SAM exclusion — {row['name']}",
            url=PAGE_URL,
            text=(f"SAM.gov exclusion record ({self.file_name or 'public extract'}):\n"
                  f"{text}"),
            published=record.get("Active Date", ""),
            doc_type="exclusion",
            meta={"match_basis": basis, "whose": whose,
                  "classification": row["classification"], "name": row["name"],
                  "exclusion_type": record.get("Exclusion Type", ""),
                  "excluding_agency": record.get("Excluding Agency", ""),
                  "program": record.get("Exclusion Program", ""),
                  "active_date": record.get("Active Date", ""),
                  "termination_date": record.get("Termination Date", ""),
                  "file": self.file_name})


def _julian_key(name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    return stem.rsplit("_", 1)[-1]


def build_index(zip_bytes: bytes, path: Path, file_name: str) -> int:
    """Index the extract: one row per record, keyed on UEI, CAGE and name."""
    db = sqlite3.connect(path)
    db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
    count = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        member = next(n for n in z.namelist() if n.upper().endswith(".CSV"))
        with z.open(member) as handle:
            reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8",
                                                 errors="replace", newline=""))
            header = [h.strip() for h in next(reader, [])]
            batch = []
            for values in reader:
                rec = {h: (values[i].strip() if i < len(values) else "")
                       for i, h in enumerate(header)}
                classification = rec.get("Classification", "")
                uei, cage = rec.get("Unique Entity ID", "").upper(), rec.get("CAGE", "").upper()
                # An individual is matched only on an identifier, so one with
                # neither can never match. Most of the file is such records;
                # leaving them out keeps the index small.
                if classification == INDIVIDUAL and not uei and not cage:
                    continue
                # The file lists active exclusions, but it is a day old when
                # read: one whose end date has passed is not one any more.
                ends = rec.get("Termination Date", "")
                if len(ends) == 10 and ends[4] == "-" and ends < date.today().isoformat():
                    continue
                name = rec.get("Name") or " ".join(
                    filter(None, (rec.get("First"), rec.get("Middle"), rec.get("Last"))))
                norm = "" if classification == INDIVIDUAL else normalise_holder(name)
                batch.append((rec.get("SAM Number", ""), classification, name, norm,
                              uei, cage, _encode(rec)))
                count += 1
                if len(batch) >= 20_000:
                    db.executemany("INSERT INTO exclusions VALUES (?,?,?,?,?,?,?)", batch)
                    batch.clear()
            db.executemany("INSERT INTO exclusions VALUES (?,?,?,?,?,?,?)", batch)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('file', ?)", (file_name,))
    db.commit()
    db.close()
    return count


def _encode(rec: dict) -> str:
    """The record as tab-separated pairs: compact, and kept whole as SAM.gov asks."""
    return "\t".join(f"{k}\x1f{v}" for k, v in rec.items() if v)


def _decode(raw: str):
    for pair in (raw or "").split("\t"):
        if "\x1f" in pair:
            yield pair.split("\x1f", 1)
