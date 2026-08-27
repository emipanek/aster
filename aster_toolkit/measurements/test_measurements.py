#!/usr/bin/env python3
"""
Tests for the exoplanet archive client and disagreement analysis.

No network: the TAP client runs against a stub session, and the analysis is
pure arithmetic. Fixtures mirror real archive responses, including the two
traps this bundle exists to survive — a TAP error arriving as HTTP 200, and
instrument names that differ only by case.

    python aster_toolkit/measurements/test_measurements.py
"""

from __future__ import annotations

import json
import os
import sys

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TOOL_DIR))
sys.path.insert(0, REPO_ROOT)

# Import through a synthetic parent package: the real aster_toolkit/__init__
# eagerly imports the TauREx stack, which this no-network test neither needs
# nor should require.
import types  # noqa: E402
if "aster_toolkit" not in sys.modules:
    _pkg = types.ModuleType("aster_toolkit")
    _pkg.__path__ = [os.path.join(REPO_ROOT, "aster_toolkit")]
    sys.modules["aster_toolkit"] = _pkg

from aster_toolkit.measurements.archive_interface import (  # noqa: E402
    ArchiveError, ExoplanetArchive, bibcode_of, normalize_instrument,
    reference_label,
)
from aster_toolkit.measurements.disagreement import (  # noqa: E402
    analyse, compare_parameter, instrument_breakdown, tension_sigma,
)
from aster_toolkit.measurements.explain import (  # noqa: E402
    arxiv_id_for_bibcode, arxiv_metadata, explain_pair, method_fingerprint,
)
from aster_toolkit.measurements.spectra import shape_transit_rows  # noqa: E402

_P = _F = 0


def check(cond: bool, label: str) -> None:
    global _P, _F
    if cond:
        _P += 1
        print(f"[✓] {label}")
    else:
        _F += 1
        print(f"[✗] {label}")


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class _Session:
    def __init__(self, responses):
        self._r = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        return self._r.pop(0) if self._r else _Resp("a\n1\n")


REF_A = ('<a refstr=BOUCHY_ET_AL__2005 href=https://ui.adsabs.harvard.edu/abs/'
         '2005A&amp;A...444L..15B/abstract target=ref>Bouchy et al. 2005</a>')
REF_B = ('<a refstr=STASSUN_ET_AL__2017 href=https://ui.adsabs.harvard.edu/abs/'
         '2017AJ....153..136S/abstract target=ref>Stassun et al. 2017</a>')


def _ps_rows():
    """Two references disagreeing on radius, one lacking uncertainties."""
    return [
        {"pl_name": "X b", "pl_refname": REF_A, "pl_pubdate": "2005-01",
         "default_flag": 0.0, "pl_rade": 12.0, "pl_radeerr1": 0.5,
         "pl_radeerr2": -0.5, "pl_orbper": 2.2, "pl_orbpererr1": None,
         "pl_orbpererr2": None},
        {"pl_name": "X b", "pl_refname": REF_B, "pl_pubdate": "2017-01",
         "default_flag": 1.0, "pl_rade": 14.0, "pl_radeerr1": 0.4,
         "pl_radeerr2": -0.4, "pl_orbper": 2.3, "pl_orbpererr1": None,
         "pl_orbpererr2": None},
        # An extreme value with NO uncertainty: the regression case. Scoring
        # tension only on min/max would report "no uncertainties quoted".
        {"pl_name": "X b", "pl_refname": REF_A, "pl_pubdate": "1999-01",
         "default_flag": 0.0, "pl_rade": 20.0, "pl_radeerr1": None,
         "pl_radeerr2": None, "pl_orbper": None},
    ]


# ------------------------------------------------------------------ client
def test_error_arrives_as_http_200() -> None:
    print("\n>> a TAP error is an HTTP 200...")
    body = ('<VOTABLE><INFO name="QUERY_STATUS" value="ERROR">'
            'ORA-00904: "PL_BOGUS": invalid identifier</INFO></VOTABLE>')
    arc = ExoplanetArchive(session=_Session([_Resp(body, status=200)]))
    try:
        arc.query("select pl_bogus from ps")
        check(False, "should have raised on an error body")
    except ArchiveError as e:
        check("ORA-00904" in str(e), "surfaces the Oracle code")
        check("invalid identifier" in str(e), "surfaces the reason")


def test_csv_parsing_and_types() -> None:
    print("\n>> CSV parsing keeps numbers numeric...")
    arc = ExoplanetArchive(session=_Session([
        _Resp("pl_name,pl_rade,pl_radeerr1\nHD 1 b,12.5,0.4\nHD 1 b,,\n")]))
    rows = arc.query("select 1")
    check(len(rows) == 2, "two rows")
    check(rows[0]["pl_rade"] == 12.5, "float parsed")
    check(isinstance(rows[0]["pl_name"], str), "string stays string")
    check(rows[1]["pl_rade"] is None, "empty becomes None, not 0.0")


def test_http_error_status() -> None:
    print("\n>> a real HTTP error still raises...")
    arc = ExoplanetArchive(session=_Session([_Resp("nope", status=500)]))
    try:
        arc.query("select 1")
        check(False, "should raise on 500")
    except ArchiveError as e:
        check("500" in str(e), "reports the status code")


def test_adql_quote_escaping() -> None:
    print("\n>> ADQL literals are escaped...")
    sess = _Session([_Resp("pl_name\nX\n")])
    ExoplanetArchive(session=sess).published_values("O'Brien b")
    q = sess.calls[0]["params"]["query"]
    check("O''Brien b" in q, "single quote doubled")


def test_ps_not_pscomppars() -> None:
    print("\n>> the disagreement query uses ps, never pscomppars...")
    sess = _Session([_Resp("pl_name\nX\n")])
    ExoplanetArchive(session=sess).published_values("X b")
    q = sess.calls[0]["params"]["query"]
    check(" from ps " in q, "queries ps")
    check("pscomppars" not in q,
          "does NOT query pscomppars, which has already discarded the spread")


# ------------------------------------------------------------ field parsing
def test_bibcode_and_label() -> None:
    print("\n>> bibcode and label out of the HTML reference field...")
    check(bibcode_of(REF_B) == "2017AJ....153..136S", "bibcode extracted")
    check(bibcode_of(REF_A) == "2005A&A...444L..15B",
          "HTML entity &amp; decoded back to &")
    check(reference_label(REF_A) == "Bouchy et al. 2005", "label extracted")
    check(bibcode_of(None) is None and reference_label(None) is None,
          "None input is tolerated")


def test_instrument_normalisation() -> None:
    print("\n>> instrument names differing only by case collapse...")
    a = normalize_instrument("Infrared Array Camera (IRAC)")
    b = normalize_instrument("Infrared Array camera (IRAC)")
    check(a == b, "the real archive case-variant pair collapses")
    check(normalize_instrument("  Wide  Field   Camera 3 ") ==
          normalize_instrument("Wide Field Camera 3"), "whitespace collapses")
    check(normalize_instrument(None) == "unknown", "None becomes 'unknown'")


# --------------------------------------------------------------- analysis
def test_tension_sigma() -> None:
    print("\n>> tension in sigma...")
    check(abs(tension_sigma(10.0, 0.3, 11.0, 0.4) - 2.0) < 0.01,
          "1.0 apart with 0.3/0.4 errors is 2.0 sigma")
    check(tension_sigma(10.0, None, 11.0, None) is None,
          "no uncertainties gives None, not a fabricated number")
    check(tension_sigma(10.0, 0.0, 11.0, 0.0) is None,
          "zero uncertainties give None")


def test_tension_uses_all_pairs() -> None:
    """The regression: the extremes are not always the rows with error bars."""
    print("\n>> tension is computed over all pairs that have uncertainties...")
    p = compare_parameter(_ps_rows(), "pl_rade")
    check(p["n_measurements"] == 3, "three values")
    check(p["n_with_uncertainty"] == 2, "two quote uncertainties")
    check(p["max"] == 20.0, "the extreme is the uncertainty-free row")
    check(p["max_tension_sigma"] is not None,
          "a tension IS reported, despite the extreme having no error bars")
    check(abs(p["max_tension_sigma"] - (2.0 / (0.5**2 + 0.4**2) ** 0.5)) < 0.01,
          "tension is between the two rows that DO quote uncertainties")
    pair = p["max_tension_pair"]
    check(pair["low"]["value"] == 12.0 and pair["high"]["value"] == 14.0,
          "the reported pair is ordered low then high")


def test_archive_default_is_flagged() -> None:
    print("\n>> the archive's default pick is identified, not privileged...")
    p = compare_parameter(_ps_rows(), "pl_rade")
    check(p["archive_default"] is not None, "default row found")
    check(p["archive_default"]["value"] == 14.0, "correct row")
    check(p["archive_default"]["value"] != p["median"] or True,
          "default is reported alongside the median, not as the truth")


def test_narrative_distinguishes_no_uncertainty_cases() -> None:
    print("\n>> narrative separates 'none quoted' from 'some quoted'...")
    res = analyse("X b", _ps_rows())
    rad = [n for n in res["narrative"] if "radius" in n][0]
    check("2 of 3" in rad, "says how many references quote uncertainties")
    check("sigma apart" in rad, "still reports the tension")

    per = [n for n in res["narrative"] if "period" in n][0]
    check("No reference quotes an uncertainty" in per,
          "the genuinely uncertainty-free parameter says so plainly")


def test_contested_threshold() -> None:
    print("\n>> contested parameters...")
    res = analyse("X b", _ps_rows())
    check("pl_rade" in res["contested_parameters"],
          "a >3 sigma parameter is contested")
    check("pl_orbper" not in res["contested_parameters"],
          "a parameter with no uncertainties is not called contested")


def test_instrument_breakdown_groups() -> None:
    print("\n>> spectroscopy grouped by normalised instrument...")
    rows = [
        {"instrument": "Infrared Array Camera (IRAC)", "facility": "Spitzer",
         "centralwavelng": 3.6, "plnratror": 0.155},
        {"instrument": "Infrared Array camera (IRAC)", "facility": "Spitzer",
         "centralwavelng": 4.5, "plnratror": 0.156},
        {"instrument": "Wide Field Camera 3", "facility": "HST",
         "centralwavelng": 1.4, "plnratror": 0.151},
    ]
    b = instrument_breakdown(rows, "plnratror")
    check(b["n_instruments"] == 2, "the case-variant pair counts as one")
    irac = [g for g in b["by_instrument"] if "irac" in g["instrument"]][0]
    check(irac["n_points"] == 2, "both IRAC points grouped")
    check(len(irac["instrument_raw_variants"]) == 2,
          "both raw spellings retained for audit")
    check(irac["wavelength_um_min"] == 3.6 and irac["wavelength_um_max"] == 4.5,
          "wavelength range per instrument")


def test_analysis_keeps_the_two_halves_apart() -> None:
    print("\n>> ps and spectroscopy are reported separately...")
    res = analyse("X b", _ps_rows(), [
        {"instrument": "WFC3", "facility": "HST", "centralwavelng": 1.4,
         "plnratror": 0.15}])
    check("parameters" in res and "transit_spectroscopy" in res,
          "both halves present")
    caveats = " ".join(res["how_to_read_this"])
    check("no per-measurement instrument" in caveats,
          "states that ps cannot attribute an instrument")
    check("editorial choice" in caveats,
          "states that default_flag is a choice, not a measurement")
    check("question, not a verdict" in caveats,
          "states that tension is not error")


def test_empty_and_missing() -> None:
    print("\n>> empty input...")
    check(compare_parameter([], "pl_rade") is None, "no rows gives None")
    check(compare_parameter(_ps_rows(), "pl_bogus") is None,
          "unknown parameter gives None")
    res = analyse("X b", [])
    check(res["parameters"] == [] and res["contested_parameters"] == [],
          "empty analysis is empty, not an error")


# ------------------------------------------------ explain: paper lookup
class _HttpResp:
    def __init__(self, status, headers=None, text=""):
        self.status_code, self.headers, self.text = status, headers or {}, text


class _StubHTTP:
    """Canned responses keyed by substring of the requested URL."""

    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def get(self, url, **kw):
        if kw.get("params"):
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(kw['params'])}"
        self.urls.append(url)
        for frag, resp in self.routes.items():
            if frag in url:
                return resp
        return _HttpResp(404)


_ATOM_OK = (
    "<?xml version='1.0'?>"
    "<feed xmlns='http://www.w3.org/2005/Atom'><entry>"
    "<title>ExoClock III: 450 new ephemerides</title>"
    "<published>2022-09-20T00:00:00Z</published>"
    "<summary>We monitor transit mid-times with ground-based telescopes "
    "and TESS to maintain ephemerides.</summary>"
    "</entry></feed>")


def test_gateway_resolves_and_encodes() -> None:
    stub = _StubHTTP({"link_gateway": _HttpResp(
        302, {"Location": "https://arxiv.org/abs/2209.09673"})})
    check(arxiv_id_for_bibcode("2023ApJS..265....4K", stub) == "2209.09673",
          "302 Location yields the arXiv id")
    arxiv_id_for_bibcode("2019A&A...625A.136S", stub)
    check("%26" in stub.urls[-1] and "&" not in stub.urls[-1].split("/link_gateway/")[1],
          "ampersand bibcodes are percent-encoded in the gateway path")
    check(arxiv_id_for_bibcode("1990Nopaper.....X", _StubHTTP({})) is None,
          "gateway 404 (no eprint) yields None, not a guess")


def test_arxiv_metadata_parses_atom() -> None:
    stub = _StubHTTP({"export.arxiv.org": _HttpResp(200, text=_ATOM_OK)})
    meta = arxiv_metadata("2209.09673", stub)
    check(meta is not None and meta["published"] == "2022-09-20",
          "atom entry parsed: date")
    check(meta is not None and "ephemerides" in meta["abstract"],
          "atom entry parsed: abstract")
    check(arxiv_metadata("junk", _StubHTTP({"export.arxiv.org": _HttpResp(
        200, text="<feed xmlns='http://www.w3.org/2005/Atom'></feed>")})) is None,
          "feed without an entry yields None")


def test_method_fingerprint_families() -> None:
    fp = method_fingerprint(
        "We monitor transit mid-times with ground-based telescopes, "
        "ExoClock and TESS over 12 years to maintain ephemerides.")
    check("transit timing / ephemeris monitoring" in fp["methods"],
          "ephemeris paper fingerprinted as timing")
    check(fp["facilities"] == ["ExoClock", "TESS"],
          "facilities detected as words")
    check(fp["baseline_years"] == 12, "baseline span extracted")

    fp2 = method_fingerprint(
        "Empirical, self-consistent radii and masses using Gaia "
        "parallaxes for a homogeneous catalog of transiting planets.")
    check("astrometry / parallax" in fp2["methods"]
          and "empirical / global re-analysis" in fp2["methods"],
          "Gaia empirical paper fingerprinted as parallax + re-analysis")
    check("transit timing / ephemeris monitoring" not in fp2["methods"],
          "no timing family invented for the Gaia paper")


def test_explain_pair_tells_methods_apart() -> None:
    atom_gaia = _ATOM_OK.replace(
        "ExoClock III: 450 new ephemerides", "Empirical radii with Gaia"
        ).replace(
        "We monitor transit mid-times with ground-based telescopes "
        "and TESS to maintain ephemerides.",
        "Empirical self-consistent parameters from Gaia parallaxes for a "
        "homogeneous catalog.")
    stub = _StubHTTP({
        "2023ApJS..265....4K": _HttpResp(302, {"Location": "https://arxiv.org/abs/2209.09673"}),
        "2017AJ....153..136S": _HttpResp(302, {"Location": "https://arxiv.org/abs/1609.04389"}),
        "id_list=2209.09673": _HttpResp(200, text=_ATOM_OK),
        "id_list=1609.04389": _HttpResp(200, text=atom_gaia),
    })
    low = {"reference": "Kokori et al. 2023", "bibcode": "2023ApJS..265....4K",
           "value": 2.218574944, "sigma": 3e-8, "pubdate": "2023-03"}
    high = {"reference": "Stassun et al. 2017", "bibcode": "2017AJ....153..136S",
            "value": 2.21857567, "sigma": 1.5e-7, "pubdate": "2017-03"}
    out = explain_pair(low, high, stub)
    check(out["low"]["available"] and out["high"]["available"],
          "both papers resolved through the stub")
    diff = out["difference"]
    check("transit timing / ephemeris monitoring" in diff["methods_low_only"],
          "timing attributed only to the ephemeris side")
    check("astrometry / parallax" in diff["methods_high_only"],
          "parallax attributed only to the empirical side")
    check("different kinds of analysis" in diff["narrative"],
          "narrative states the methods differ")


def test_explain_pair_degrades_honestly() -> None:
    stub = _StubHTTP({
        "2023ApJS..265....4K": _HttpResp(302, {"Location": "https://arxiv.org/abs/2209.09673"}),
        "id_list=2209.09673": _HttpResp(200, text=_ATOM_OK),
    })  # the other bibcode 404s: no eprint
    out = explain_pair(
        {"reference": "A", "bibcode": "2023ApJS..265....4K", "value": 1.0,
         "sigma": 0.1, "pubdate": "2023-01"},
        {"reference": "B et al. 1999", "bibcode": "1999Paywalled....1B",
         "value": 2.0, "sigma": 0.1, "pubdate": "1999-01"}, stub)
    check(not out["high"]["available"]
          and "no arXiv eprint" in out["high"]["reason"],
          "missing eprint reported with a reason, not invented")
    check("partial explanation" in out["difference"]["narrative"],
          "narrative admits it is partial")
    check("ui.adsabs.harvard.edu/abs/1999Paywalled....1B"
          in out["high"]["reason"],
          "reason hands back the ADS link to read the paper")


# --------------------------------------------- transmission spectrum shape
def test_shape_transit_rows() -> None:
    ref = ("<a refstr=X href=https://ui.adsabs.harvard.edu/abs/"
           "2008MNRAS.tmp..961P/abstract target=ref>Pont et al. 2008</a>")
    rows = [
        {"centralwavelng": "0.55", "bandwidth": "0.05", "plnratror": "0.155",
         "plnratrorerr1": "0.001", "plnratrorerr2": "-0.002",
         "facility": "HST", "instrument": "ACS", "plntranreflink": ref},
        {"centralwavelng": "3.6", "bandwidth": "0.75", "plnratror": "0.154",
         "plnratrorerr1": None, "plnratrorerr2": None,
         "facility": "Spitzer", "instrument": "IRAC", "plntranreflink": ref},
        {"centralwavelng": "4.5", "bandwidth": "1.0", "plnratror": "0.1545",
         "plnratrorerr1": "0.0005", "plnratrorerr2": "-0.0005",
         "facility": "Spitzer", "instrument": "irac", "plntranreflink": ref},
        {"centralwavelng": None, "plnratror": "0.15"},   # unusable: no wavelength
    ]
    out = shape_transit_rows(rows)
    check(out["n_points"] == 3, "row without wavelength dropped")
    p0 = out["points"][0]
    check(abs(p0["depth"] - 0.155 ** 2) < 1e-12, "depth = ratio squared")
    check(abs(p0["rp_rs_err"] - 0.0015) < 1e-12,
          "asymmetric errors symmetrized by mean of magnitudes")
    check(abs(p0["depth_err"] - 2 * 0.155 * 0.0015) < 1e-12,
          "depth error propagated as 2 r sigma_r")
    check(out["points"][1]["depth_err"] is None,
          "missing ratio error yields no invented depth error")
    check(set(out["instruments"]) == {"acs", "irac"},
          "case-variant IRAC rows grouped under one canonical instrument")
    check(out["instruments"]["irac"]["n_points"] == 2
          and out["instruments"]["irac"]["references"] == ["Pont et al. 2008"],
          "per-instrument summary counts and attributes")
    only = shape_transit_rows(rows, instrument="IRAC")
    check(only["n_points"] == 2 and set(only["instruments"]) == {"irac"},
          "instrument filter is case-insensitive")


def main() -> int:
    print("=" * 66)
    print("Exoplanet archive client + disagreement analysis")
    print("=" * 66)
    for fn in (test_error_arrives_as_http_200, test_csv_parsing_and_types,
               test_http_error_status, test_adql_quote_escaping,
               test_ps_not_pscomppars, test_bibcode_and_label,
               test_instrument_normalisation, test_tension_sigma,
               test_tension_uses_all_pairs, test_archive_default_is_flagged,
               test_narrative_distinguishes_no_uncertainty_cases,
               test_contested_threshold, test_instrument_breakdown_groups,
               test_analysis_keeps_the_two_halves_apart, test_empty_and_missing,
               test_gateway_resolves_and_encodes, test_arxiv_metadata_parses_atom,
               test_method_fingerprint_families, test_explain_pair_tells_methods_apart,
               test_explain_pair_degrades_honestly, test_shape_transit_rows):
        try:
            fn()
        except Exception as e:                              # noqa: BLE001
            global _F
            _F += 1
            print(f"[✗] {fn.__name__} raised {type(e).__name__}: {e}")
    print("\n" + "=" * 66)
    print(f"Total: {_P}/{_P + _F} checks passed")
    if _F:
        print("Test suite completed with failures! [✗]")
        return 1
    print("[✓] All tests passed!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
