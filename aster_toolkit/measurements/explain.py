"""
Explain WHY two published measurements disagree — as far as the papers say.

``disagreement.py`` quantifies a disagreement and attributes each value to a
reference; it deliberately stops there, because the archive stores no
per-measurement method. But every ``ps`` row does carry one handle to the
paper itself: a bibcode. This module follows it, keyless end to end:

    bibcode --ADS link gateway (public 302)-->  arXiv id
            --arXiv export API (Atom feed)-->   title, date, abstract
            --method fingerprint (taxonomy)-->  what kind of measurement

and then states the DIFFERENCE between the two sides' fingerprints.

Abstract-level on purpose. Abstracts of measurement papers state data and
technique ("we monitor ephemerides with ground and space photometry",
"empirical radii from Gaia parallaxes"), which is exactly the level at which
two disagreeing values can be told apart. What the abstract cannot settle —
two papers with the SAME fingerprint — is said out loud in the narrative,
with the arXiv ids to go read.

Same contract as the rest of the bundle: the module explains what differs in
approach; it never adjudicates which value is right. And it degrades
honestly — a bibcode with no arXiv eprint (the gateway answers 404) yields
``available: false`` with a reason, never a guess.
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import requests

ADS_GATEWAY = "https://ui.adsabs.harvard.edu/link_gateway/{bibcode}/EPRINT_HTML"
ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_ARXIV_ABS = re.compile(r"arxiv\.org/abs/([^\s?#]+)", re.I)

TIMEOUT_S = 30
_UA = {"User-Agent": "aster-measurements/1.0"}


# --------------------------------------------------------- method taxonomy
# One family per DISTINCT way of producing a planet parameter. Keywords are
# matched against a lowercased abstract; multi-word phrases dodge the worst
# substring traps, and the deliberately short ones (" rv ", " hst ") are
# padded so they only match as words.
METHOD_TAXONOMY: Dict[str, tuple] = {
    "transit timing / ephemeris monitoring": (
        "ephemeris", "ephemerides", "transit timing", "mid-time",
        "mid-transit", "timing variation", "exoclock"),
    "radial velocity": (
        "radial velocity", "radial-velocity", " rv ", "doppler",
        "spectrograph"),
    "empirical / global re-analysis": (
        "empirical", "homogeneous", "global fit", "joint fit", "reanalysis",
        "re-analysis", "catalog", "catalogue", "compilation",
        "self-consistent"),
    "astrometry / parallax": ("parallax", "astrometr"),
    "transit photometry": (
        "photometr", "transit depth", "light curve", "lightcurve",
        "light-curve", "transit observation"),
    "atmospheric spectroscopy": (
        "transmission spectr", "emission spectr", "secondary eclipse"),
}

FACILITIES = (
    "TESS", "Kepler", "K2", "CHEOPS", "CoRoT", "Gaia", "Spitzer", "Hubble",
    "HST", "JWST", "HARPS", "HIRES", "SOPHIE", "ESPRESSO", "ExoClock", "ETD",
    "Ariel",
)


def method_fingerprint(text: str) -> Dict[str, Any]:
    """What kind of measurement an abstract describes, deterministically."""
    hay = f" {' '.join((text or '').lower().split())} "
    methods = [fam for fam, keys in METHOD_TAXONOMY.items()
               if any(k in hay for k in keys)]
    facilities = sorted({f for f in FACILITIES
                         if f" {f.lower()} " in hay
                         or f" {f.lower()}," in hay
                         or f" {f.lower()}." in hay})
    m = re.search(r"(\d{1,2})\s*(?:years|yr)", hay)
    return {
        "methods": methods,
        "facilities": facilities,
        "baseline_years": int(m.group(1)) if m else None,
    }


# ------------------------------------------------------------ paper lookup
def arxiv_id_for_bibcode(bibcode: str,
                         session: Optional[Any] = None) -> Optional[str]:
    """bibcode -> arXiv id via ADS's public link gateway, or None.

    The gateway is a keyless redirect service: 302 with an arxiv.org
    Location when the paper has an eprint, 404 when it does not. Bibcodes
    contain ``&`` (A&A) so the path segment must be percent-encoded.
    """
    ses = session or requests
    url = ADS_GATEWAY.format(bibcode=urllib.parse.quote(bibcode, safe=""))
    resp = ses.get(url, allow_redirects=False, timeout=TIMEOUT_S,
                   headers=_UA)
    if resp.status_code not in (301, 302, 303, 307, 308):
        return None
    m = _ARXIV_ABS.search(resp.headers.get("Location", ""))
    return m.group(1) if m else None


def arxiv_metadata(arxiv_id: str,
                   session: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """title / date / abstract from arXiv's keyless export API."""
    ses = session or requests
    resp = ses.get(ARXIV_API, params={"id_list": arxiv_id},
                   timeout=TIMEOUT_S, headers=_UA)
    if resp.status_code != 200:
        return None
    try:
        entry = ET.fromstring(resp.text).find("a:entry", _ATOM)
    except ET.ParseError:
        return None
    if entry is None:
        return None
    title = " ".join((entry.findtext("a:title", "", _ATOM) or "").split())
    abstract = " ".join((entry.findtext("a:summary", "", _ATOM) or "").split())
    if not abstract:
        return None
    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "published": (entry.findtext("a:published", "", _ATOM) or "")[:10],
        "abstract": abstract,
    }


def describe_side(point: Dict[str, Any],
                  session: Optional[Any] = None) -> Dict[str, Any]:
    """Everything explainable about one measurement: value + its paper."""
    out: Dict[str, Any] = {
        "reference": point.get("reference"),
        "bibcode": point.get("bibcode"),
        "value": point.get("value"),
        "sigma": point.get("sigma"),
        "pubdate": point.get("pubdate"),
        "available": False,
    }
    bibcode = point.get("bibcode")
    if not bibcode:
        out["reason"] = "row carries no bibcode; nothing to follow"
        return out
    try:
        arxiv_id = arxiv_id_for_bibcode(bibcode, session)
    except requests.RequestException as e:
        out["reason"] = f"ADS link gateway unreachable: {e}"
        return out
    if not arxiv_id:
        out["reason"] = ("no arXiv eprint registered for this bibcode; the "
                         "paper may be paywalled — read it via "
                         f"https://ui.adsabs.harvard.edu/abs/{bibcode}")
        return out
    try:
        meta = arxiv_metadata(arxiv_id, session)
    except requests.RequestException as e:
        out["reason"] = f"arXiv export API unreachable: {e}"
        return out
    if not meta:
        out["reason"] = f"arXiv returned no usable entry for {arxiv_id}"
        return out
    out.update(meta)
    out["available"] = True
    out["fingerprint"] = method_fingerprint(
        f"{meta['title']} {meta['abstract']}")
    return out


# --------------------------------------------------------------- the story
def _difference(low: Dict[str, Any], high: Dict[str, Any]) -> Dict[str, Any]:
    fl = (low.get("fingerprint") or {})
    fh = (high.get("fingerprint") or {})
    ml, mh = set(fl.get("methods") or []), set(fh.get("methods") or [])
    dl, dh = sorted(ml - mh), sorted(mh - ml)
    shared = sorted(ml & mh)

    def who(side: Dict[str, Any]) -> str:
        return side.get("reference") or side.get("bibcode") or "one side"

    if not (low.get("available") and high.get("available")):
        missing = [who(s) for s in (low, high) if not s.get("available")]
        narrative = (
            "Only a partial explanation is possible: no readable paper for "
            + " and ".join(missing) + ". The reason for the disagreement "
            "lives in the papers, so follow the reason link(s) above.")
    elif dl or dh:
        bits = []
        if dl:
            bits.append(f"{who(low)} additionally describes: {', '.join(dl)}")
        if dh:
            bits.append(f"{who(high)} additionally describes: {', '.join(dh)}")
        narrative = (
            "The two values come from different kinds of analysis. "
            + "; ".join(bits) + "."
            + (f" Both describe: {', '.join(shared)}." if shared else "")
            + " When the method families differ, the disagreement usually "
            "traces to the data and technique each paper chose, not to an "
            "error either made.")
    else:
        narrative = (
            "The abstracts describe the same kind of analysis"
            + (f" ({', '.join(shared)})" if shared else "")
            + ", so the difference is in details the abstracts do not "
            "carry — data span, detrending, limb darkening, stellar "
            "activity treatment. Read the methods sections: arXiv "
            f"{low.get('arxiv_id')} and {high.get('arxiv_id')}.")

    return {
        "methods_low_only": dl,
        "methods_high_only": dh,
        "methods_shared": shared,
        "facilities_low": fl.get("facilities") or [],
        "facilities_high": fh.get("facilities") or [],
        "narrative": narrative,
    }


def explain_pair(low: Dict[str, Any], high: Dict[str, Any],
                 session: Optional[Any] = None) -> Dict[str, Any]:
    """The full explanation for one contested pair from compare_parameter."""
    lo = describe_side(low, session)
    hi = describe_side(high, session)
    return {
        "low": lo,
        "high": hi,
        "difference": _difference(lo, hi),
        "caveat": (
            "Method attribution comes from the papers' own titles and "
            "abstracts, keyless and deterministic. It explains what differs "
            "in approach; it does not decide which value is right."),
    }
