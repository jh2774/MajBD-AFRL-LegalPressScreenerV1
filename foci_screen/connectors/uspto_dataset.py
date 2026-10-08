"""Recorded patent security interests, from the USPTO Patent Assignment Dataset.

Recorded `SECURITY INTEREST` conveyances are the strongest IP-collateral
evidence there is, and the live sources for them are closed to this tool
without a key: the assignment search moved to assignmentcenter.uspto.gov,
which refuses automated clients, and data.uspto.gov sits behind a bot
challenge. Neither is worked around.

The USPTO also publishes the same records as a research dataset — 10.5
million recorded transactions since 1970, released by its Office of the Chief
Economist for exactly this kind of use:

    https://www.uspto.gov/ip-policy/economic-research/research-datasets/patent-assignment-dataset

Its download links hand a person a signed URL to open in a browser; a script
gets the bot challenge instead. So, like the saved-alerts folder, this stops
at files a person has downloaded. `import_dataset` reads them once into a
small index of security interests and releases; `PatentAssignmentDataset`
answers from that index during a screen.

What it cannot do is be current. The dataset is a snapshot, refreshed about
once a year, so a lien recorded after its last date is not seen — and a
screen says so rather than reading as "no liens".
"""
from __future__ import annotations

import csv
import io
import logging
import re
import sqlite3
import zipfile
from array import array
from bisect import bisect_left
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path

from ..models import Document
from .sec_edgar import normalise_holder

log = logging.getLogger("foci.uspto_dataset")

# Conveyance and correspondent fields run long; the csv module's default limit
# of 128 KB per field is below the longest of them.
csv.field_size_limit(2**31 - 1)

DATASET_URL = ("https://www.uspto.gov/ip-policy/economic-research/research-datasets/"
               "patent-assignment-dataset")

# The dataset's own classification of each record (`assignment_conveyance`).
# Only these two are kept: a lien, and the lender giving it back.
KEPT = {"security": "security", "release": "release"}

TABLES = ("assignment_conveyance", "assignment", "assignor", "assignee", "documentid")

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    rf_id      INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,          -- security | release
    reel       TEXT, frame TEXT,
    convey     TEXT,                   -- conveyance text as recorded
    recorded   TEXT                    -- ISO date recorded with the USPTO
);
CREATE TABLE IF NOT EXISTS parties (
    rf_id INTEGER NOT NULL,
    role  TEXT NOT NULL,               -- assignor | assignee
    name  TEXT NOT NULL,
    norm  TEXT NOT NULL,
    city  TEXT, state TEXT, country TEXT
);
CREATE TABLE IF NOT EXISTS properties (
    rf_id INTEGER NOT NULL,
    patent TEXT NOT NULL               -- grant number, else application number
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_parties_norm ON parties(norm, role);
CREATE INDEX IF NOT EXISTS idx_parties_rf ON parties(rf_id);
CREATE INDEX IF NOT EXISTS idx_props_rf ON properties(rf_id);
CREATE INDEX IF NOT EXISTS idx_props_patent ON properties(patent);
"""

_STATA_EPOCH = date(1960, 1, 1)
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


class DatasetError(Exception):
    """The files given are not the Patent Assignment Dataset, or are incomplete."""


# ------------------------------------------------------------------ import

def import_dataset(source: str | Path, index_path: str | Path,
                   progress: Callable[[str], None] = print) -> dict:
    """Build the index from downloaded dataset files.

    `source` may be the combined `csv.zip`, a folder of the per-table
    `*.csv.zip` downloads, or a folder of extracted `.csv` files. Everything is
    streamed: the full set is about 1.8 GB compressed, and nothing larger than
    the list of kept record ids is held in memory.
    """
    if not Path(source).exists():
        raise DatasetError(f"{source} does not exist. Download the CSV set from "
                           f"{DATASET_URL} and give its path.")
    opener = _TableOpener(Path(source))
    missing = [t for t in TABLES if not opener.has(t)]
    if missing:
        raise DatasetError(
            f"Missing table(s): {', '.join(missing)}. Download the full CSV set "
            f"(csv.zip) from {DATASET_URL}")

    index_path = Path(index_path)
    tmp = index_path.with_suffix(index_path.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    # The index is rebuilt whole on every import and renamed into place only
    # once complete, so a crash costs a rerun, never a corrupt index. That
    # makes journaling and fsync pure cost here; with them on, the import
    # spent most of its time waiting on the disk.
    db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;"
                     " PRAGMA temp_store=MEMORY; PRAGMA cache_size=-65536;")
    db.executescript(SCHEMA)
    try:
        progress("Reading conveyance types...")
        # Two sorted arrays of record ids rather than a dict: about 2.5 million
        # records are kept, which is ~20 MB as arrays and ~250 MB as a dict.
        security, release = array("q"), array("q")
        col, rows = opener.table("assignment_conveyance")
        for row in rows:
            kind = KEPT.get(col(row, "convey_ty").lower())
            rf = _int(col(row, "rf_id"))
            if kind and rf is not None:
                (security if kind == "security" else release).append(rf)
        security, release = _sorted(security), _sorted(release)
        if not (security or release):
            raise DatasetError("No security or release records found in "
                               "assignment_conveyance — is this the right file?")
        progress(f"  {len(security):,} security interest(s), {len(release):,} release(s)")

        def kind_of(rf: int | None) -> str:
            if rf is None:
                return ""
            i = bisect_left(security, rf)
            if i < len(security) and security[i] == rf:
                return "security"
            i = bisect_left(release, rf)
            if i < len(release) and release[i] == rf:
                return "release"
            return ""

        progress("Reading assignments...")
        latest = ""
        batch: list[tuple] = []
        col, rows = opener.table("assignment")
        for row in rows:
            rf = _int(col(row, "rf_id"))
            kind = kind_of(rf)
            if not kind or col(row, "purge_in") in ("1", "1.0"):
                continue
            recorded = parse_date(col(row, "record_dt"))
            latest = max(latest, recorded)
            batch.append((rf, kind, col(row, "reel_no"), col(row, "frame_no"),
                          col(row, "convey_text"), recorded))
            if len(batch) >= 50_000:
                db.executemany("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)", batch)
                batch.clear()
        db.executemany("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)", batch)

        progress("Reading assignors and assignees...")
        for table, role, name_col, place in (
                ("assignor", "assignor", "or_name", ()),
                ("assignee", "assignee", "ee_name", ("ee_city", "ee_state", "ee_country"))):
            batch = []
            col, rows = opener.table(table)
            for row in rows:
                rf = _int(col(row, "rf_id"))
                name = col(row, name_col)
                if not name or not kind_of(rf):
                    continue
                city, state, country = ([col(row, c) for c in place] if place
                                        else ("", "", ""))
                batch.append((rf, role, name, normalise_holder(name), city, state, country))
                if len(batch) >= 50_000:
                    db.executemany("INSERT INTO parties VALUES (?,?,?,?,?,?,?)", batch)
                    batch.clear()
            db.executemany("INSERT INTO parties VALUES (?,?,?,?,?,?,?)", batch)

        progress("Reading the patents each record covers...")
        batch = []
        col, rows = opener.table("documentid")
        for row in rows:
            rf = _int(col(row, "rf_id"))
            if not kind_of(rf):
                continue
            patent = col(row, "grant_doc_num") or col(row, "appno_doc_num")
            if patent:
                batch.append((rf, patent))
            if len(batch) >= 100_000:
                db.executemany("INSERT INTO properties VALUES (?,?)", batch)
                batch.clear()
        db.executemany("INSERT INTO properties VALUES (?,?)", batch)

        progress("Indexing...")
        db.executescript(INDEXES)
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("records", "parties", "properties")}
        db.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
            ("latest_recorded", latest), ("source", str(source)),
            ("imported_on", date.today().isoformat())])
        db.commit()
    except BaseException:
        db.close()
        tmp.unlink(missing_ok=True)
        raise
    db.close()
    tmp.replace(index_path)
    return {**counts, "latest_recorded": latest}


class _TableOpener:
    """Finds each table among the shapes the dataset is downloaded in."""

    def __init__(self, source: Path) -> None:
        self.source = source
        self.members: dict[str, tuple[Path, str | None]] = {}
        candidates = [source] if source.is_file() else sorted(source.iterdir())
        for path in candidates:
            if path.suffix.lower() == ".zip":
                with zipfile.ZipFile(path) as z:
                    for name in z.namelist():
                        self._add(_table_of(name), path, name)
            else:
                self._add(_table_of(path.name), path, None)

    def _add(self, table: str, path: Path, member: str | None) -> None:
        if table in TABLES and table not in self.members:
            self.members[table] = (path, member)

    def has(self, table: str) -> bool:
        return table in self.members

    def table(self, table: str) -> tuple[Callable[[list, str], str], Iterator[list]]:
        """A column reader and the table's rows, as lists.

        Lists rather than a dict per row: the largest tables run to tens of
        millions of rows, and building a dict for each was a large share of
        the import's time.
        """
        with ExitStack() as stack:
            header = next(self._reader(table, stack), [])
        index = {h.strip().strip('"').lower(): i for i, h in enumerate(header)}

        def col(row: list, name: str) -> str:
            i = index.get(name)
            # A short row (a trailing empty field the export dropped) reads
            # its missing columns as empty rather than failing.
            return row[i].strip() if i is not None and i < len(row) else ""

        def rows() -> Iterator[list]:
            with ExitStack() as stack:
                reader = self._reader(table, stack)
                next(reader, None)
                yield from reader

        return col, rows()

    def _reader(self, table: str, stack: ExitStack):
        path, member = self.members[table]
        if member is None:
            handle = stack.enter_context(open(path, "rb"))
        else:
            archive = stack.enter_context(zipfile.ZipFile(path))
            handle = stack.enter_context(archive.open(member))
        text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace", newline="")
        return csv.reader(text)


def _table_of(filename: str) -> str:
    """'2023/assignment_conveyance.csv' -> 'assignment_conveyance'."""
    base = Path(filename).name.lower()
    return base.split(".", 1)[0] if base.endswith((".csv", ".csv.zip")) else ""


def _sorted(arr: array) -> array:
    """Sorted, without a list copy when the file was already in order (it is)."""
    if all(arr[i] <= arr[i + 1] for i in range(len(arr) - 1)):
        return arr
    return array("q", sorted(arr))


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(float(str(value).strip()))   # "101.0", as some exports write ids
    except (TypeError, ValueError):
        return None


def parse_date(value) -> str:
    """The recorded date as ISO, from any form the export has used.

    Stata stores dates as days since 1960-01-01, and a CSV export carries
    either that number or Stata's display form ("12mar2015"); other releases
    have used ISO or YYYYMMDD. An unreadable date is "" rather than a guess.
    """
    s = str(value or "").strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", s):
        return s[:10]
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    m = re.fullmatch(r"(\d{1,2})([a-z]{3})(\d{4})", s.lower())
    if m and m.group(2) in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1))).isoformat()
        except ValueError:
            return ""
    days = _int(s)
    if days is not None and -40_000 < days < 40_000:
        return (_STATA_EPOCH + timedelta(days=days)).isoformat()
    return ""


# ------------------------------------------------------------------ lookup

class PatentAssignmentDataset:
    """Security interests a contractor granted over its patents, from the index."""

    name = "uspto_dataset"

    def __init__(self, index_path: str = "") -> None:
        self.index_path = index_path
        self._db: sqlite3.Connection | None = None

    @property
    def available(self) -> bool:
        return bool(self.index_path) and Path(self.index_path).is_file()

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            self._db = sqlite3.connect(f"file:{self.index_path}?mode=ro", uri=True,
                                       check_same_thread=False)
            self._db.row_factory = sqlite3.Row
        return self._db

    def latest_recorded(self) -> str:
        row = self._conn().execute(
            "SELECT value FROM meta WHERE key='latest_recorded'").fetchone()
        return row["value"] if row else ""

    def security_interests(self, company: str, limit: int = 25) -> list[Document]:
        """Liens the company granted, newest first, with released ones dropped.

        The company is matched as the assignor — the party pledging its patents
        — by exact name once legal suffixes are set aside, as IAPD holders are.
        A lien whose every patent the lender has since released (a release
        recorded later, giving the patent back to this company) is no longer an
        encumbrance and is not returned; a partly released one says how much.
        """
        norm = normalise_holder(company)
        if not norm or not self.available:
            return []
        db = self._conn()
        records = db.execute(
            "SELECT DISTINCT r.* FROM records r JOIN parties p ON p.rf_id = r.rf_id"
            " WHERE p.norm = ? AND p.role = 'assignor' AND r.kind = 'security'"
            " ORDER BY r.recorded DESC, r.rf_id DESC LIMIT ?",
            (norm, limit * 4)).fetchall()
        docs: list[Document] = []
        for r in records:
            patents = db.execute("SELECT COUNT(DISTINCT patent) AS n FROM properties"
                                 " WHERE rf_id=?", (r["rf_id"],)).fetchone()["n"]
            released = self._released(r["rf_id"], r["recorded"], norm)
            if patents and released >= patents:
                continue
            docs.append(self._to_doc(r, patents, released))
            if len(docs) >= limit:
                break
        return docs

    def _released(self, rf_id: int, recorded: str, debtor: str) -> int:
        """How many of this lien's patents a later release gave back to the debtor."""
        return self._conn().execute(
            "SELECT COUNT(DISTINCT p.patent) AS n FROM properties p"
            " JOIN properties q ON q.patent = p.patent"
            " JOIN records rel ON rel.rf_id = q.rf_id AND rel.kind = 'release'"
            " JOIN parties back ON back.rf_id = rel.rf_id AND back.role = 'assignee'"
            " WHERE p.rf_id = ? AND rel.recorded >= ? AND back.norm = ?",
            (rf_id, recorded or "", debtor)).fetchone()["n"]

    def _to_doc(self, r: sqlite3.Row, patents: int, released: int) -> Document:
        db = self._conn()
        parties = db.execute("SELECT * FROM parties WHERE rf_id=?", (r["rf_id"],)).fetchall()
        assignors = [p["name"] for p in parties if p["role"] == "assignor"]
        lenders = [p for p in parties if p["role"] == "assignee"]
        lender = lenders[0]["name"] if lenders else ""
        address = (" ".join(filter(None, (lenders[0]["city"], lenders[0]["state"],
                                          lenders[0]["country"])))
                   if lenders else "")
        reel, frame = r["reel"] or "", r["frame"] or ""
        outstanding = patents - released
        released_note = (f"\nReleased since: {released} of {patents} (a later release "
                         f"recorded the lender returning them)" if released else "")
        return Document(
            source="uspto", key=f"uspto:{reel}-{frame}",
            title=f"{r['convey']} {'; '.join(assignors)} -> {lender}",
            # The reel/frame is how the record is cited and looked up by hand
            # in the USPTO's Assignment Center.
            url="https://assignmentcenter.uspto.gov/search/patent",
            text=(f"USPTO assignment reel/frame {reel}/{frame} recorded {r['recorded']}.\n"
                  f"Conveyance: {r['convey']}\nAssignor: {'; '.join(assignors)}\n"
                  f"Assignee: {lender}\nAssignee address: {address}\n"
                  f"Properties: {patents}{released_note}\n"
                  f"Source: USPTO Patent Assignment Dataset (research release)."),
            published=r["recorded"] or "", doc_type="patent_assignment",
            meta={"conveyance": r["convey"] or "", "conveyance_type": "security",
                  "assignee": lender, "assignor": "; ".join(assignors),
                  "assignee_address": address, "patent_count": outstanding,
                  "patents_recorded": patents, "patents_released": released,
                  "reel_frame": f"{reel}/{frame}", "from_dataset": True})
