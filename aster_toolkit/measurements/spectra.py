"""
Shape the archive's transmission-spectroscopy rows into a usable spectrum.

``transitspec`` is the one archive table with per-row facility and
instrument. Its rows arrive as strings, wavelength-ordered but mixed across
instruments and papers, quoting the radius ratio Rp/R* rather than the
transit depth most retrieval codes want. This module turns those rows into:

* per-point records with the depth PROPAGATED, not just the ratio —
  depth = (Rp/R*)^2, sigma_depth = 2 * (Rp/R*) * sigma_ratio — because a
  retrieval fed ratios as depths is wrong by a factor of the ratio itself;
* a per-instrument summary, so "which instruments observed this planet, over
  which wavelength ranges" is one look, not a scan of 500 rows.

Pure functions, no network: the TAP fetch lives in ``archive_interface``,
and everything here is testable against canned rows.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .archive_interface import bibcode_of, normalize_instrument, reference_label


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


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
        ratio = _f(r.get("plnratror"))
        if wl is None:
            continue
        # err1 is the upper error, err2 the lower (negative by convention);
        # symmetrize because that is what a retrieval's likelihood takes.
        errs = [abs(e) for e in (_f(r.get("plnratrorerr1")),
                                 _f(r.get("plnratrorerr2"))) if e is not None]
        ratio_err = sum(errs) / len(errs) if errs else None
        depth = ratio * ratio if ratio is not None else None
        depth_err = (2 * abs(ratio) * ratio_err
                     if ratio is not None and ratio_err is not None else None)
        points.append({
            "wavelength_um": wl,
            "bin_um": _f(r.get("bandwidth")),
            "rp_rs": ratio,
            "rp_rs_err": ratio_err,
            "depth": depth,
            "depth_err": depth_err,
            "facility": r.get("facility"),
            "instrument": inst,
            "reference": reference_label(r.get("plntranreflink")),
            "bibcode": bibcode_of(r.get("plntranreflink")),
        })

    by_inst: Dict[str, Dict[str, Any]] = {}
    for p in points:
        key = p["instrument"] or "unknown"
        s = by_inst.setdefault(key, {
            "n_points": 0, "wavelength_min_um": p["wavelength_um"],
            "wavelength_max_um": p["wavelength_um"], "references": set()})
        s["n_points"] += 1
        s["wavelength_min_um"] = min(s["wavelength_min_um"], p["wavelength_um"])
        s["wavelength_max_um"] = max(s["wavelength_max_um"], p["wavelength_um"])
        if p["reference"]:
            s["references"].add(p["reference"])
    for s in by_inst.values():
        s["references"] = sorted(s["references"])

    return {
        "n_points": len(points),
        "wavelength_min_um": min((p["wavelength_um"] for p in points),
                                 default=None),
        "wavelength_max_um": max((p["wavelength_um"] for p in points),
                                 default=None),
        "instruments": dict(sorted(by_inst.items())),
        "points": points,
    }
