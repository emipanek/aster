"""
Why do two measurements of the same planet disagree?

The archive can answer part of that and not the rest, and the split matters:

* ``ps`` gives every published value with its reference, so the SIZE of a
  disagreement, and whether the quoted uncertainties can absorb it, is
  answerable directly.
* ``ps`` gives NO per-measurement instrument. Its three facility columns —
  ``disc_facility``, ``disc_telescope``, ``disc_instrument`` — all describe
  the discovery observation and are identical on every row of a planet.
  Verified: all 21 rows of HD 189733 b read "Haute-Provence Observatory"
  while the radii come from Spitzer, HST and ground-based work. So
  "which telescope caused this?" cannot be answered from ``ps`` at all.
* ``transitspec`` / ``emissionspec`` DO carry per-row ``facility`` and
  ``instrument``, wavelength-resolved. That is where instrument attribution
  genuinely lives.

So this module reports two things and keeps them apart: a quantified
disagreement from ``ps``, and an instrument/wavelength picture from the
spectroscopy tables. It deliberately does not fuse them into a single causal
claim, because the archive does not carry the join that would justify one.

**Tension is not error.** Two measurements differing by 5 sigma may reflect
stellar activity, a different limb-darkening treatment, a different detrending
method, or a genuine astrophysical change. This module quantifies and
attributes; it does not adjudicate.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

from .archive_interface import (
    bibcode_of,
    normalize_instrument,
    reference_label,
)

# Parameters worth comparing, with their error columns and a display unit.
PARAMETERS: Dict[str, Dict[str, str]] = {
    "pl_rade":   {"err_hi": "pl_radeerr1", "err_lo": "pl_radeerr2",
                  "unit": "R_Earth", "label": "planet radius"},
    "pl_bmasse": {"err_hi": "pl_bmasseerr1", "err_lo": "pl_bmasseerr2",
                  "unit": "M_Earth", "label": "planet mass"},
    "pl_orbper": {"err_hi": "pl_orbpererr1", "err_lo": "pl_orbpererr2",
                  "unit": "days", "label": "orbital period"},
}


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _sigma(row: Dict[str, Any], spec: Dict[str, str]) -> Optional[float]:
    """Symmetrised 1-sigma from the archive's asymmetric error pair.

    The lower error is stored negative. Averaging the magnitudes is a
    simplification, and it is the right one here only because we use sigma to
    RANK tension, never to publish a combined value.
    """
    hi, lo = _f(row.get(spec["err_hi"])), _f(row.get(spec["err_lo"]))
    mags = [abs(x) for x in (hi, lo) if x is not None and x != 0.0]
    return sum(mags) / len(mags) if mags else None


def tension_sigma(a: float, sa: Optional[float],
                  b: float, sb: Optional[float]) -> Optional[float]:
    """How many combined sigma separate two values.

    None when neither measurement quotes an uncertainty — a difference with
    no error bars is not a tension, it is an unknown, and reporting a number
    there would invent precision.
    """
    var = (sa or 0.0) ** 2 + (sb or 0.0) ** 2
    if var <= 0:
        return None
    return abs(a - b) / math.sqrt(var)


def compare_parameter(rows: List[Dict[str, Any]],
                      param: str) -> Optional[Dict[str, Any]]:
    """Every published value of one parameter, with the spread quantified."""
    spec = PARAMETERS.get(param)
    if spec is None:
        return None

    points: List[Dict[str, Any]] = []
    for r in rows:
        val = _f(r.get(param))
        if val is None:
            continue
        points.append({
            "value": val,
            "sigma": _sigma(r, spec),
            "reference": reference_label(r.get("pl_refname")),
            "bibcode": bibcode_of(r.get("pl_refname")),
            "pubdate": r.get("pl_pubdate"),
            "is_archive_default": bool(_f(r.get("default_flag"))),
            "soltype": r.get("soltype"),
        })
    if len(points) < 1:
        return None

    values = [p["value"] for p in points]
    lo = min(points, key=lambda p: p["value"])
    hi = max(points, key=lambda p: p["value"])
    spread = hi["value"] - lo["value"]
    med = statistics.median(values)

    # Worst tension over every pair that HAS uncertainties, not just the
    # min/max pair. The extremes are frequently the rows without error bars
    # (older papers, or values carried over from a discovery announcement),
    # and scoring only those reports "no uncertainties quoted" for a planet
    # where most references do quote them.
    worst = None
    worst_pair: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None
    with_sigma = [p for p in points if p["sigma"]]
    for i in range(len(with_sigma)):
        for j in range(i + 1, len(with_sigma)):
            t = tension_sigma(with_sigma[i]["value"], with_sigma[i]["sigma"],
                              with_sigma[j]["value"], with_sigma[j]["sigma"])
            if t is not None and (worst is None or t > worst):
                worst = t
                worst_pair = (with_sigma[i], with_sigma[j])
    if worst_pair and worst_pair[0]["value"] > worst_pair[1]["value"]:
        worst_pair = (worst_pair[1], worst_pair[0])

    # Fractional spread against the median is the scale-free way to say
    # "how much do people disagree", and survives a zero-uncertainty row.
    frac = (spread / abs(med)) if med else None

    return {
        "parameter": param,
        "label": spec["label"],
        "unit": spec["unit"],
        "n_measurements": len(points),
        "n_with_uncertainty": sum(1 for p in points if p["sigma"] is not None),
        "median": med,
        "min": lo["value"],
        "max": hi["value"],
        "spread": spread,
        "fractional_spread": frac,
        "max_tension_sigma": worst,
        "max_tension_pair": ({"low": worst_pair[0], "high": worst_pair[1]}
                             if worst_pair else None),
        "extremes": {"low": lo, "high": hi},
        "archive_default": next(
            (p for p in points if p["is_archive_default"]), None),
        "measurements": sorted(points, key=lambda p: (p["pubdate"] or "")),
    }


def instrument_breakdown(spec_rows: List[Dict[str, Any]],
                         depth_col: str = "plnratror") -> Dict[str, Any]:
    """Group spectroscopy rows by instrument, normalising the names.

    Instrument strings are not normalised upstream — "Infrared Array Camera
    (IRAC)" and "Infrared Array camera (IRAC)" coexist in the same table and
    would otherwise be counted as two instruments.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for r in spec_rows:
        key = normalize_instrument(r.get("instrument"))
        g = groups.setdefault(key, {
            "instrument": key,
            "instrument_raw": set(),
            "facility": r.get("facility"),
            "n_points": 0,
            "wavelengths_um": [],
            "values": [],
        })
        if r.get("instrument"):
            g["instrument_raw"].add(str(r["instrument"]))
        g["n_points"] += 1
        wl, val = _f(r.get("centralwavelng")), _f(r.get(depth_col))
        if wl is not None:
            g["wavelengths_um"].append(wl)
        if val is not None:
            g["values"].append(val)

    out = []
    for g in groups.values():
        wl = g["wavelengths_um"]
        vals = g["values"]
        out.append({
            "instrument": g["instrument"],
            "instrument_raw_variants": sorted(g["instrument_raw"]),
            "facility": g["facility"],
            "n_points": g["n_points"],
            "wavelength_um_min": min(wl) if wl else None,
            "wavelength_um_max": max(wl) if wl else None,
            "value_median": statistics.median(vals) if vals else None,
            "value_min": min(vals) if vals else None,
            "value_max": max(vals) if vals else None,
        })
    out.sort(key=lambda g: -g["n_points"])
    return {"n_instruments": len(out), "by_instrument": out}


def _narrate(param: Dict[str, Any]) -> str:
    """One plain sentence about a parameter's spread, hedged honestly."""
    n = param["n_measurements"]
    if n < 2:
        return (f"Only {n} published value of {param['label']}, so there is "
                f"nothing to compare.")
    frac = param["fractional_spread"]
    sig = param["max_tension_sigma"]
    n_sig = param["n_with_uncertainty"]
    pct = f"{frac * 100:.1f}%" if frac is not None else "an unknown fraction"
    head = (f"{n} published values of {param['label']} span "
            f"{param['min']:.4g} to {param['max']:.4g} {param['unit']} "
            f"({pct} of the median)")

    if sig is None:
        if n_sig == 0:
            return head + (". No reference quotes an uncertainty, so the "
                           "disagreement cannot be assessed against them.")
        return head + (f". Only {n_sig} of {n} references quote an "
                       f"uncertainty, and no two of those can be compared, "
                       f"so the spread cannot be assessed against them.")

    pair = param.get("max_tension_pair") or {}
    a = (pair.get("low") or {}).get("reference") or "unknown"
    b = (pair.get("high") or {}).get("reference") or "unknown"
    caveat = ("" if n_sig == n else
              f" (tension computed over the {n_sig} of {n} references that "
              f"quote uncertainties)")
    if sig < 1.0:
        return head + (f". The most discrepant pair that quotes uncertainties "
                       f"agrees within {sig:.1f} sigma, so the spread is "
                       f"consistent with them{caveat}.")
    return head + (f". The most discrepant pair is {sig:.1f} sigma apart "
                   f"({a} vs {b}), so the quoted uncertainties do not absorb "
                   f"the difference{caveat}.")


def analyse(planet: str,
            ps_rows: List[Dict[str, Any]],
            transit_rows: Optional[List[Dict[str, Any]]] = None,
            emission_rows: Optional[List[Dict[str, Any]]] = None
            ) -> Dict[str, Any]:
    """The full picture for one planet, with the two halves kept separate."""
    params = [p for p in (compare_parameter(ps_rows, k) for k in PARAMETERS)
              if p is not None]
    contested = [p for p in params
                 if (p["max_tension_sigma"] or 0) >= 3.0 and
                 p["n_measurements"] >= 2]

    result: Dict[str, Any] = {
        "planet": planet,
        "n_published_parameter_sets": len(ps_rows),
        "parameters": params,
        "contested_parameters": [p["parameter"] for p in contested],
        "narrative": [_narrate(p) for p in params],
    }

    if transit_rows:
        result["transit_spectroscopy"] = instrument_breakdown(
            transit_rows, "plnratror")
    if emission_rows:
        result["emission_spectroscopy"] = instrument_breakdown(
            emission_rows, "especlipdep")

    result["how_to_read_this"] = [
        "ps gives one row per published reference, so the spread above is a "
        "real disagreement between papers, not scatter within one dataset.",
        "The archive's default_flag marks its own canonical pick. That is an "
        "editorial choice, not a measurement, and it is not necessarily the "
        "newest or the most precise value.",
        "ps carries no per-measurement instrument: disc_facility, "
        "disc_telescope and disc_instrument all describe the DISCOVERY and "
        "are identical on every row. Instrument attribution therefore comes "
        "only from the spectroscopy tables, which cover a different set of "
        "measurements.",
        "A large tension is a question, not a verdict. Stellar activity, "
        "limb-darkening treatment, detrending method and genuine variability "
        "all produce real disagreements between careful papers.",
    ]
    return result
