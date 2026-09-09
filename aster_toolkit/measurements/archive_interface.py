"""
NASA Exoplanet Archive TAP interface.

Free and keyless: https://exoplanetarchive.ipac.caltech.edu/TAP, ADQL over
sync or async, several output formats.

Two things about this service decide the whole design above it.

**Which table you query decides whether the question is answerable at all.**
``pscomppars`` holds one row per planet — a curated composite "best value"
that is explicitly not self-consistent across columns. ``ps`` holds one row
per planet *per published reference*. Asking why two measurements of a planet
disagree is structurally impossible against ``pscomppars``; it has already
thrown the disagreement away. Verified: HD 189733 b has 21 rows in ``ps`` and
1 in ``pscomppars``.

**A TAP error is an HTTP 200.** The service answers malformed ADQL with status
200 and a VOTABLE body carrying a raw Oracle ``ORA-xxxxx`` code. Checking the
status code alone silently accepts failure as an empty result, so the body is
inspected here instead.
"""

from __future__ import annotations

import csv
import io
import re
import threading
import time
from typing import Any, Dict, List, Optional

import requests

ARCHIVE_HOST = "https://exoplanetarchive.ipac.caltech.edu"
TAP_SYNC = ARCHIVE_HOST + "/TAP/sync"
# The Firefly app in front of the Atmospheric Spectroscopy table. Loading it
# once mints the session workspace under which spectrum files are served.
FIREFLY_ATMOSPHERES = ARCHIVE_HOST + "/cgi-bin/atmospheres/nph-firefly?atmospheres"
SPECTRA_DATA_SUBPATH = "/atmospheres/tab1/data/"

# No documented rate limit, but hammering a public archive on someone else's
# behalf is rude and gets tools blocked. One request per second, plus backoff.
_MIN_REQUEST_INTERVAL = 1.0
_MAX_RETRIES = 3

# The archive answers errors with HTTP 200, so detect them in the body.
_ERROR_MARKERS = ("ORA-", "<INFO name=\"QUERY_STATUS\" value=\"ERROR\"",
                  "Error in", "ERROR<")


class ArchiveError(RuntimeError):
    """The archive refused the query, or answered with an error body."""


def _looks_like_error(text: str) -> bool:
    head = text[:4000]
    return any(m in head for m in _ERROR_MARKERS)


def _oracle_reason(text: str) -> str:
    m = re.search(r"(ORA-\d+:[^<\n]*)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r'value="ERROR"[^>]*>\s*([^<]{0,300})', text)
    return (m.group(1).strip() if m else text[:200]).strip()


class ExoplanetArchive:
    """Minimal ADQL client for the NASA Exoplanet Archive.

    Exposes the queries the measurement-disagreement workflow needs rather
    than being a general TAP library.
    """

    def __init__(self, timeout: int = 120,
                 session: Optional[requests.Session] = None) -> None:
        self._timeout = timeout
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._last = 0.0
        self._workspace: Optional[str] = None

    # ------------------------------------------------------------- plumbing
    def _throttle(self) -> None:
        with self._lock:
            gap = time.time() - self._last
            if gap < _MIN_REQUEST_INTERVAL:
                time.sleep(_MIN_REQUEST_INTERVAL - gap)
            self._last = time.time()

    def query(self, adql: str) -> List[Dict[str, Any]]:
        """Run ADQL and return rows as dicts. Raises ArchiveError on failure."""
        last_err: Optional[str] = None
        for attempt in range(_MAX_RETRIES):
            self._throttle()
            try:
                r = self._session.get(
                    TAP_SYNC, params={"query": adql, "format": "csv"},
                    timeout=self._timeout)
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = f"{type(e).__name__}"
                time.sleep(2.0 * (attempt + 1))
                continue
            except requests.RequestException as e:
                raise ArchiveError(f"request failed: {type(e).__name__}: {e}") from e

            if r.status_code >= 400:
                raise ArchiveError(f"archive returned {r.status_code}: "
                                   f"{r.text[:200]}")
            # HTTP 200 does not mean success here.
            if _looks_like_error(r.text):
                raise ArchiveError(f"ADQL rejected: {_oracle_reason(r.text)}")
            return self._parse_csv(r.text)

        raise ArchiveError(f"archive unreachable after {_MAX_RETRIES} attempts "
                           f"({last_err})")

    @staticmethod
    def _parse_csv(text: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for raw in csv.DictReader(io.StringIO(text)):
            row: Dict[str, Any] = {}
            for k, v in raw.items():
                if k is None:
                    continue
                if v is None or v == "":
                    row[k] = None
                    continue
                try:                       # keep numbers numeric for stats
                    row[k] = float(v) if re.fullmatch(
                        r"[-+]?\d*\.?\d+([eE][-+]?\d+)?", v.strip()) else v
                except ValueError:
                    row[k] = v
            rows.append(row)
        return rows

    # -------------------------------------------------------------- queries
    def published_values(self, planet: str,
                         columns: Optional[List[str]] = None
                         ) -> List[Dict[str, Any]]:
        """Every published parameter set for one planet, from ``ps``.

        One row per reference. ``default_flag=1`` marks the archive's own
        canonical pick, which is a *choice*, not a measurement.
        """
        cols = columns or [
            "pl_name", "hostname", "pl_refname", "pl_pubdate", "default_flag",
            "soltype", "discoverymethod",
            "pl_rade", "pl_radeerr1", "pl_radeerr2",
            "pl_bmasse", "pl_bmasseerr1", "pl_bmasseerr2", "pl_bmassprov",
            "pl_orbper", "pl_orbpererr1", "pl_orbpererr2",
            "pl_eqt", "pl_dens",
            "disc_facility", "disc_telescope", "disc_instrument",
        ]
        return self.query(
            f"select {','.join(cols)} from ps "
            f"where pl_name='{_esc(planet)}' order by pl_pubdate desc")

    def transit_spectrum(self, planet: str) -> List[Dict[str, Any]]:
        """Wavelength-resolved transit depths WITH per-row instrument.

        ``ps`` cannot attribute a measurement to an instrument — its three
        facility columns all describe the discovery and are constant across a
        planet's rows. This table is where per-instrument attribution
        actually lives.
        """
        return self.query(
            "select plntname,centralwavelng,bandwidth,plnratror,"
            "plnratrorerr1,plnratrorerr2,plntransdep,plntransdeperr1,"
            "plntransdeperr2,facility,instrument,plntranreflink "
            f"from transitspec where plntname='{_esc(planet)}' "
            "order by centralwavelng")

    def emission_spectrum(self, planet: str) -> List[Dict[str, Any]]:
        """Eclipse depths and brightness temperatures, with instrument."""
        return self.query(
            "select plntname,centralwavelng,bandwidth,especlipdep,"
            "especlipdeperr1,espbritemp,facility,instrument,plntreflink "
            f"from emissionspec where plntname='{_esc(planet)}' "
            "order by centralwavelng")

    # ----------------------------------------------- atmospheric spectroscopy
    def spectra_index(self, planet: str,
                      spec_type: Optional[str] = "Transmission"
                      ) -> List[Dict[str, Any]]:
        """Metadata rows from ``spectra``, the Atmospheric Spectroscopy table.

        This is the newer, file-backed table: as of 2026-09 it lists 940
        transmission spectra for 203 planets, against 104 planets in
        ``transitspec``, and it is where JWST-era spectra land. TAP exposes
        only the metadata; the points live in one IPAC table file per
        spectrum, addressed by ``spec_path``. ``spec_type`` is
        'Transmission', 'Eclipse', 'Direct Imaging', or 'any'.
        """
        where = f"pl_name='{_esc(planet)}'"
        if spec_type and spec_type.strip().lower() != "any":
            where += f" and spec_type='{_esc(spec_type.strip().title())}'"
        return self.query(
            "select pl_name,spec_type,authors,bibcode,num_datapoints,"
            "instrument,facility,minwavelng,maxwavelng,mintranmid,maxtranmid,"
            f"note,spec_path from spectra where {where} "
            "order by bibcode,spec_path")

    def mint_spectra_workspace(self) -> str:
        """Obtain the session path under which spectrum files are served.

        The archive offers spectrum files only through a wget script from
        its Firefly page. That script is nothing but ``host + workspace +
        /atmospheres/tab1/data/ + spec_path`` per file, where the workspace
        (``/workspace/TMP_xxxx``) is minted on every page load. One GET of
        the page, no cookies and no JavaScript, yields a workspace — so the
        script, and the clicking that produces it, are unnecessary.
        """
        self._throttle()
        try:
            r = self._session.get(FIREFLY_ATMOSPHERES, timeout=self._timeout)
        except requests.RequestException as e:
            raise ArchiveError(
                f"atmospheres app unreachable: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise ArchiveError(f"atmospheres app returned {r.status_code}")
        ws = workspace_from_page(r.text)
        if not ws:
            raise ArchiveError(
                "atmospheres app page carried no workspace path; the "
                "archive's Firefly layout may have changed")
        self._workspace = ws
        return ws

    def spectrum_file_url(self, spec_path: str) -> str:
        """Direct URL of one spectrum file, minting a workspace if needed."""
        ws = self._workspace or self.mint_spectra_workspace()
        return f"{ARCHIVE_HOST}{ws}{SPECTRA_DATA_SUBPATH}{spec_path}"

    def spectrum_file(self, spec_path: str) -> str:
        """The IPAC table text of one spectrum.

        A workspace can expire; a 404 re-mints one and retries once.
        """
        for attempt in (0, 1):
            url = self.spectrum_file_url(spec_path)
            self._throttle()
            try:
                r = self._session.get(url, timeout=self._timeout)
            except (requests.Timeout, requests.ConnectionError) as e:
                raise ArchiveError(
                    f"spectrum file unreachable: {type(e).__name__}") from e
            if r.status_code == 404 and attempt == 0:
                self._workspace = None
                continue
            if r.status_code >= 400:
                raise ArchiveError(f"spectrum file {spec_path}: HTTP {r.status_code}")
            if not r.text.lstrip().startswith(("\\", "|")):
                raise ArchiveError(
                    f"spectrum file {spec_path}: body is not an IPAC table")
            return r.text
        raise ArchiveError(f"spectrum file {spec_path}: not found")

    def count(self, table: str, planet: str) -> int:
        col = "plntname" if table in ("transitspec", "emissionspec") else "pl_name"
        rows = self.query(
            f"select count(*) as n from {table} where {col}='{_esc(planet)}'")
        return int(rows[0]["n"]) if rows else 0

    def resolve_planet(self, name: str, limit: int = 10) -> List[str]:
        """Planet names in ``ps`` matching loosely, for name reconciliation.

        Papers write "K2-18b" where the archive writes "K2-18 b". Exact
        matching therefore silently returns nothing, which is worse than
        returning candidates.
        """
        like = _esc(re.sub(r"\s+", "", name)).replace("-", "%")
        rows = self.query(
            "select distinct pl_name from ps where "
            f"replace(pl_name,' ','') like '%{like}%'")
        return [r["pl_name"] for r in rows][:limit]


def workspace_from_page(html: str) -> Optional[str]:
    """The ``/workspace/TMP_...`` path from the Firefly page's init call.

    The page's ``<body onload="FF_InitPage('/work/TMP_x/...', '/workspace/TMP_x', ...)">``
    is the only place the workspace appears; the second argument is it.
    """
    m = re.search(r"FF_InitPage\s*\(\s*'[^']*'\s*,\s*'(/workspace/TMP_[^']+)'", html)
    return m.group(1) if m else None


def _esc(value: str) -> str:
    """Escape a single-quoted ADQL literal.

    The archive takes ADQL straight to Oracle, so an unescaped quote is not
    merely a syntax error — keep the doubling explicit and obvious.
    """
    return (value or "").replace("'", "''")


# Instrument strings are not normalised upstream: "Infrared Array Camera
# (IRAC)" and "Infrared Array camera (IRAC)" are distinct values in the same
# table and would be counted as two instruments. Verified live on
# HD 189733 b.
def normalize_instrument(name: Optional[str]) -> str:
    if not name:
        return "unknown"
    s = re.sub(r"\s+", " ", str(name)).strip().lower()
    s = re.sub(r"\s*\(([^)]+)\)\s*$", r" (\1)", s)
    return s


def bibcode_of(refname: Optional[str]) -> Optional[str]:
    """Pull a bibcode out of the archive's HTML reference field.

    ``pl_refname`` arrives as an anchor tag, e.g.
    ``<a refstr=... href=...abs/2019A&A...625A.136S/abstract ...>Stassun 2019</a>``
    There is no clean bibcode column anywhere in ``ps``, so this is the only
    handle for linking a row to its paper — and it breaks if the archive
    changes its HTML.
    """
    if not refname:
        return None
    m = re.search(r"abs/([^/\s\"'>]+)/abstract", str(refname))
    if m:
        return m.group(1).replace("&amp;", "&")
    m = re.search(r"(\d{4}[A-Za-z&.]{5}[\w.&]{4}[\w.]\w?)", str(refname))
    return m.group(1).replace("&amp;", "&") if m else None


def reference_label(refname: Optional[str]) -> Optional[str]:
    """The human-readable author/year inside the anchor text."""
    if not refname:
        return None
    m = re.search(r">([^<]{2,60})</a>", str(refname))
    return m.group(1).strip() if m else None
