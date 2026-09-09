"""
Shape the archive's spectroscopy data into usable spectra.

Two archive sources carry transmission spectra, and they differ in shape:

* ``transitspec`` rows (TAP): one row per point, mixed across instruments
  and papers, quoting EITHER a radius ratio Rp/R* OR a transit depth in
  percent — 63% of the table's rows carry a depth and no ratio. A retrieval
  wants the depth as a fraction, so the depth is PROPAGATED here:
  depth = (Rp/R*)^2, sigma_depth = 2 (Rp/R*) sigma_ratio when only the ratio
  is quoted; depth = quoted / 100 when the paper quoted a depth.
* ``spectra`` files (Atmospheric Spectroscopy table): one IPAC table file
  per published spectrum, fetched by :meth:`ExoplanetArchive.spectrum_file`.
  :func:`parse_ipac_table` reads the fixed-width format the way astropy
  does (the archive's own ``DownloadDataset`` uses astropy), so both paths
  agree on every value.

Pure functions, no network: the fetches live in ``archive_interface`` and
everything here is testable against canned rows and canned file text.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .archive_interface import bibcode_of, normalize_instrument, reference_label


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def _sym_err(e1: Any, e2: Any) -> Optional[float]:
    """Symmetrize an (upper, lower) error pair as the mean of magnitudes.

    err1 is the upper error, err2 the lower (negative by convention); a
    retrieval's likelihood takes one sigma, so the mean of magnitudes it is.
    """
    errs = [abs(e) for e in (_f(e1), _f(e2)) if e is not None]
    return sum(errs) / len(errs) if errs else None


def _depth_and_ratio(ratio: Optional[float], ratio_err: Optional[float],
                     depth_pct: Optional[float], depth_pct_err: Optional[float]
                     ) -> Dict[str, Any]:
    """Depth (fraction) and Rp/R*, whichever the row quotes, the other derived.

    Feeding a retrieval a ratio as a depth is wrong by a factor of the
    ratio itself, and feeding it a percent as a fraction is wrong by 100.
    Both conversions happen exactly once, here.
    """
    if depth_pct is not None:
        depth = depth_pct / 100.0
        depth_err = depth_pct_err / 100.0 if depth_pct_err is not None else None
        if ratio is None and depth > 0:
            ratio = depth ** 0.5
            ratio_err = (depth_err / (2 * ratio)
                         if depth_err is not None else None)
        return {"depth": depth, "depth_err": depth_err, "rp_rs": ratio,
                "rp_rs_err": ratio_err, "depth_source": "quoted"}
    if ratio is not None:
        return {"depth": ratio * ratio,
                "depth_err": (2 * abs(ratio) * ratio_err
                              if ratio_err is not None else None),
                "rp_rs": ratio, "rp_rs_err": ratio_err,
                "depth_source": "from_ratio"}
    return {"depth": None, "depth_err": None, "rp_rs": None,
            "rp_rs_err": None, "depth_source": None}


# ------------------------------------------------------------ transitspec
def shape_transit_rows(rows: List[Dict[str, Any]],
                       instrument: Optional[str] = None) -> Dict[str, Any]:
    """Archive ``transitspec`` rows -> spectrum points + instrument summary.

    ``instrument`` filters case-insensitively through
    ``normalize_instrument`` — the table spells IRAC three different ways.
    """
    want = normalize_instrument(instrument) if instrument else None

    points: List[Dict[str, Any]] = []
    for r in rows:
        inst = normalize_instrument(r.get("instrument"))
        if want and inst != want:
            continue
        wl = _f(r.get("centralwavelng"))
        if wl is None:
            continue
        point = {"wavelength_um": wl, "bin_um": _f(r.get("bandwidth"))}
        point.update(_depth_and_ratio(
            _f(r.get("plnratror")),
            _sym_err(r.get("plnratrorerr1"), r.get("plnratrorerr2")),
            _f(r.get("plntransdep")),
            _sym_err(r.get("plntransdeperr1"), r.get("plntransdeperr2"))))
        point.update({
            "facility": r.get("facility"),
            "instrument": inst,
            "reference": reference_label(r.get("plntranreflink")),
            "bibcode": bibcode_of(r.get("plntranreflink")),
        })
        points.append(point)

    return {
        "n_points": len(points),
        "wavelength_min_um": min((p["wavelength_um"] for p in points),
                                 default=None),
        "wavelength_max_um": max((p["wavelength_um"] for p in points),
                                 default=None),
        "instruments": _instrument_summary(points),
        "points": points,
    }


def _instrument_summary(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_inst: Dict[str, Dict[str, Any]] = {}
    for p in points:
        key = p.get("instrument") or "unknown"
        s = by_inst.setdefault(key, {
            "n_points": 0, "wavelength_min_um": p["wavelength_um"],
            "wavelength_max_um": p["wavelength_um"], "references": set()})
        s["n_points"] += 1
        s["wavelength_min_um"] = min(s["wavelength_min_um"], p["wavelength_um"])
        s["wavelength_max_um"] = max(s["wavelength_max_um"], p["wavelength_um"])
        if p.get("reference"):
            s["references"].add(p["reference"])
    for s in by_inst.values():
        s["references"] = sorted(s["references"])
    return dict(sorted(by_inst.items()))


# ------------------------------------------------------- IPAC table files
def parse_ipac_table(text: str) -> Dict[str, Any]:
    """Parse an IPAC-format table (the archive's spectrum file format).

    Returns ``{"keywords": {...}, "columns": [...], "units": [...],
    "rows": [{col: value}]}``. Column boundaries come from the ``|``
    positions of the first header line, and each value is read from the
    character after its left bar up to its right bar — exactly astropy's
    ``format="ipac"`` behaviour, so a value with spaces in it ("Sing et al.
    2016") stays whole. ``null`` and blanks become ``None``; numerals
    become floats.
    """
    keywords: Dict[str, str] = {}
    header: List[str] = []
    data: List[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("\\"):
            m = re.match(r"\\\s*([A-Za-z_][\w]*)\s*=\s*(.*)$", line)
            if m:
                keywords[m.group(1)] = m.group(2).strip().strip("'\"")
        elif line.startswith("|"):
            header.append(line)
        else:
            data.append(line)
    if not header:
        raise ValueError("not an IPAC table: no '|' header line")

    bars = [i for i, ch in enumerate(header[0]) if ch == "|"]
    spans: List[Tuple[int, Optional[int]]] = [
        (bars[i] + 1, bars[i + 1]) for i in range(len(bars) - 1)]
    if len(bars) == 1 or header[0].rstrip()[-1] != "|":
        spans.append((bars[-1] + 1, None))

    def cells(line: str) -> List[str]:
        return [line[a:b].strip() if b is not None else line[a:].strip()
                for a, b in spans]

    columns = cells(header[0])
    units = cells(header[2]) if len(header) > 2 else [""] * len(columns)
    null_tokens = set(cells(header[3])) if len(header) > 3 else set()
    null_tokens |= {"", "null"}

    rows: List[Dict[str, Any]] = []
    for line in data:
        vals = cells(line)
        row: Dict[str, Any] = {}
        for col, v in zip(columns, vals):
            if v in null_tokens:
                row[col] = None
            elif re.fullmatch(r"[-+]?\d*\.?\d+([eE][-+]?\d+)?", v):
                row[col] = float(v)
            else:
                row[col] = v
        rows.append(row)
    return {"keywords": keywords, "columns": columns, "units": units,
            "rows": rows}


def shape_spectrum_file(meta: Dict[str, Any], table: Dict[str, Any],
                        max_points: Optional[int] = None) -> Dict[str, Any]:
    """One archive spectrum file -> points in the same shape as transitspec.

    ``meta`` is the ``spectra`` table row the file belongs to; ``table`` is
    :func:`parse_ipac_table` output. Transmission files carry the depth in
    percent (``PL_TRANDEP``) and often a radius ratio; eclipse files carry
    ``ESPECLIPDEP`` (percent) and a brightness temperature; direct-imaging
    files carry flux densities. Whatever the file has is passed through
    under stable names, with percent turned into fractions once.
    """
    kind = str(meta.get("spec_type") or table["keywords"].get("SPEC_TYPE") or "").lower()
    points: List[Dict[str, Any]] = []
    for r in table["rows"]:
        wl = _f(r.get("CENTRALWAVELNG"))
        if wl is None:
            continue
        p: Dict[str, Any] = {"wavelength_um": wl, "bin_um": _f(r.get("BANDWIDTH"))}
        if kind.startswith("trans"):
            p.update(_depth_and_ratio(
                _f(r.get("PL_RATROR")),
                _sym_err(r.get("PL_RATRORERR1"), r.get("PL_RATRORERR2")),
                _f(r.get("PL_TRANDEP")),
                _sym_err(r.get("PL_TRANDEPERR1"), r.get("PL_TRANDEPERR2"))))
            p["depth_provenance"] = r.get("PL_TRANDEP_AUTHORS")
            p["ratio_provenance"] = r.get("PL_RATROR_AUTHORS")
        elif kind.startswith("ecl"):
            dep = _f(r.get("ESPECLIPDEP"))
            err = _sym_err(r.get("ESPECLIPDEPERR1"), r.get("ESPECLIPDEPERR2"))
            p.update({
                "eclipse_depth": dep / 100.0 if dep is not None else None,
                "eclipse_depth_err": err / 100.0 if err is not None else None,
                "brightness_temp_k": _f(r.get("ESPBRITEMP")),
                "brightness_temp_err_k": _sym_err(r.get("ESPBRITEMPERR1"),
                                                  r.get("ESPBRITEMPERR2")),
            })
        else:
            p.update({
                "flam_w_m2_um": _f(r.get("FLAM")),
                "flam_err": _sym_err(r.get("FLAMERR1"), r.get("FLAMERR2")),
                "fnu_jy": _f(r.get("FNU")),
                "fnu_err": _sym_err(r.get("FNUERR1"), r.get("FNUERR2")),
            })
        points.append(p)

    n_all = len(points)
    truncated = bool(max_points) and n_all > int(max_points)
    if truncated:
        points = points[:int(max_points)]
    return {
        "n_points": n_all,
        "truncated": truncated,
        "wavelength_min_um": min((p["wavelength_um"] for p in points), default=None),
        "wavelength_max_um": max((p["wavelength_um"] for p in points), default=None),
        "keywords": table["keywords"],
        "points": points,
    }


def wget_script(entries: Sequence[Tuple[str, str]], comment: str = "") -> str:
    """Render ``wget -O <file> <url>`` lines, one per spectrum.

    This is byte-for-byte what the archive's Firefly page offers behind
    "Download All Checked Spectra", minus the metadata manifest line, so
    ``downloaddataset(wget_text=...)`` (DownloadDataset) consumes it unchanged.
    """
    lines = [f"# {comment}"] if comment else []
    lines += [f"wget -O {name} {url}" for name, url in entries]
    return "\n".join(lines) + "\n"
