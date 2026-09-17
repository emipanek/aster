"""
Exoplanet tools: check a paper's claimed measurements against the archive.

  ResolvePlanetNameTool       paper spelling -> archive pl_name
  PublishedMeasurementsTool   every published value, with its reference
  MeasurementDisagreementTool quantify the spread and attribute instruments
  ExplainDisagreementTool     WHY the worst-tension pair differs (from papers)
  TransmissionSpectrumTool    points from the legacy transitspec table
  AtmosphericSpectraTool      spectra files from the Atmospheric Spectroscopy
                              table, no wget script, DownloadDataset-ready
  ExoplanetArchiveQueryTool   raw ADQL, for anything the above does not cover

The intended chain is: pull a claimed value out of a paper, resolve the
planet name, then ask what else has been
published for it and whether the claim sits inside or outside that spread.

None of these tools decide who is right. They quantify, attribute, and hand
back the references.

Naming: orchestral shows the model a tool named after its class (``ResolvePlanetNameTool``
-> ``resolveplanetname``) and described by its docstring, so hints below refer to
tools by those derived names.
"""

from __future__ import annotations

import json
from typing import List, Optional

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField

from .archive_interface import ArchiveError, ExoplanetArchive
from .disagreement import PARAMETERS, analyse, compare_parameter
from .explain import explain_pair
from .spectra import (parse_ipac_table, shape_spectrum_file, shape_transit_rows,
                      wget_script)

SCHEMA_VERSION = "exoplanet-1.0"


def _err(msg: str, **extra) -> str:
    return json.dumps({"status": "error", "error": msg,
                       "schema_version": SCHEMA_VERSION, **extra}, indent=2)


def _ok(**payload) -> str:
    return json.dumps({"status": "ok", "schema_version": SCHEMA_VERSION,
                       **payload}, indent=2, default=str)


def _int(v) -> Optional[int]:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


class ResolvePlanetNameTool(BaseTool):
    """Map a planet name as written in a paper onto the archive's spelling.

    Papers write "K2-18b" where the archive writes "K2-18 b", and an exact
    match silently returns nothing. Always resolve before querying, and treat
    multiple candidates as a question for the user rather than picking one.
    """

    planet_name: str = RuntimeField(description="Planet name as written, e.g. 'K2-18b'")
    limit: Optional[int] = RuntimeField(default=10, description="Max candidates")

    def _run(self) -> str:
        try:
            names = ExoplanetArchive().resolve_planet(
                self.planet_name, limit=self.limit or 10)
        except ArchiveError as e:
            return _err(str(e))
        return _ok(query=self.planet_name, n_candidates=len(names),
                   candidates=names,
                   note=("No match. Try a shorter fragment of the host name."
                         if not names else
                         "Exactly one candidate is a confident match; several "
                         "means you should choose before continuing."))


class PublishedMeasurementsTool(BaseTool):
    """Every published parameter set for one planet, one row per reference.

    Queries the ``ps`` table, not ``pscomppars``. ``pscomppars`` holds one
    composite row per planet and has already discarded the disagreement this
    tool exists to show.
    """

    planet_name: str = RuntimeField(description="Archive pl_name, e.g. 'HD 189733 b'")

    def _run(self) -> str:
        try:
            rows = ExoplanetArchive().published_values(self.planet_name)
        except ArchiveError as e:
            return _err(str(e))
        if not rows:
            return _err(f"no rows in ps for '{self.planet_name}'",
                        hint="Resolve the name first with resolveplanetname.")
        return _ok(planet=self.planet_name, n_references=len(rows),
                   measurements=rows)


class MeasurementDisagreementTool(BaseTool):
    """Why do published measurements of this planet differ?

    Quantifies the spread of each parameter across all published references
    and says whether the quoted uncertainties absorb it. Where transit or
    emission spectroscopy exists, it also breaks those down by instrument.

    Two halves are reported separately on purpose. ``ps`` carries no
    per-measurement instrument — its facility columns describe the discovery
    and are identical on every row — so instrument attribution comes only
    from the spectroscopy tables, which cover a different set of
    measurements. Fusing them would assert a join the archive does not have.
    """

    planet_name: str = RuntimeField(description="Archive pl_name, e.g. 'HD 189733 b'")
    include_spectroscopy: Optional[bool] = RuntimeField(
        default=True, description="Also query transitspec and emissionspec")

    def _run(self) -> str:
        arc = ExoplanetArchive()
        try:
            ps_rows = arc.published_values(self.planet_name)
        except ArchiveError as e:
            return _err(str(e))
        if not ps_rows:
            return _err(f"no rows in ps for '{self.planet_name}'",
                        hint="Resolve the name first with resolveplanetname.")

        transit = emission = None
        warnings: List[str] = []
        if self.include_spectroscopy:
            for label, fn in (("transitspec", arc.transit_spectrum),
                              ("emissionspec", arc.emission_spectrum)):
                try:
                    rows = fn(self.planet_name)
                except ArchiveError as e:
                    warnings.append(f"{label} query failed: {e}")
                    continue
                if label == "transitspec":
                    transit = rows
                else:
                    emission = rows
            if not transit and not emission:
                warnings.append(
                    "No spectroscopy rows, so no instrument attribution is "
                    "possible for this planet. These tables lag the current "
                    "literature and are notably thin for recent instruments.")

        out = analyse(self.planet_name, ps_rows, transit, emission)
        if warnings:
            out["warnings"] = warnings
        return _ok(**out)


class ExplainDisagreementTool(BaseTool):
    """WHY do the two most discrepant published values differ?

    ``measurementdisagreement`` finds and quantifies a disagreement; this
    tool follows the two references of the worst-tension pair to their
    papers (bibcode -> ADS link gateway -> arXiv abstract, keyless) and
    reports what kind of analysis each side says it did, and how the two
    differ. When the abstracts describe the same kind of analysis, it says
    so and hands back the arXiv ids to read instead of guessing.
    """

    planet_name: str = RuntimeField(description="Archive pl_name, e.g. 'HD 189733 b'")
    parameter: str = RuntimeField(
        default="",
        description=("ps column to explain, e.g. 'pl_orbper'. Empty string "
                     "picks the parameter with the worst tension."))

    def _run(self) -> str:
        try:
            rows = ExoplanetArchive().published_values(self.planet_name)
        except ArchiveError as e:
            return _err(str(e))
        if not rows:
            return _err(f"no rows in ps for '{self.planet_name}'",
                        hint="Resolve the name first with resolveplanetname.")

        if self.parameter:
            if self.parameter not in PARAMETERS:
                return _err(f"unknown parameter '{self.parameter}'",
                            known=sorted(PARAMETERS))
            candidates = [compare_parameter(rows, self.parameter)]
        else:
            candidates = [compare_parameter(rows, p) for p in PARAMETERS]

        contested = [c for c in candidates
                     if c and c.get("max_tension_pair")]
        if not contested:
            return _err(
                "no parameter has two values with quoted uncertainties, so "
                "there is no tension pair to explain",
                hint="publishedmeasurements shows what the rows carry.")
        worst = max(contested, key=lambda c: c["max_tension_sigma"] or 0)
        pair = worst["max_tension_pair"]

        explanation = explain_pair(pair["low"], pair["high"])
        return _ok(
            planet=self.planet_name,
            parameter=worst["parameter"],
            parameter_label=worst["label"],
            unit=worst["unit"],
            tension_sigma=worst["max_tension_sigma"],
            explanation=explanation,
        )


class TransmissionSpectrumTool(BaseTool):
    """Fetch a planet's published transmission spectrum from the archive.

    Reads the ``transitspec`` table over keyless TAP and returns
    wavelength-ordered points with the transit depth as a fraction — taken
    from the quoted depth when the paper quoted one (63% of rows), else
    propagated from the quoted Rp/R* — plus per-row facility, instrument
    and reference. The per-instrument summary says who observed this planet
    over which wavelength range before you read a single point.

    ``transitspec`` is the older, point-per-row table (104 planets). The
    file-backed Atmospheric Spectroscopy table covers 203 planets and is
    where JWST spectra land; ``atmosphericspectra`` reads that one.

    The points feed a TauREx observed spectrum directly: wavelength_um,
    depth, depth_err, bin_um.
    """

    planet_name: str = RuntimeField(description="Archive pl_name, e.g. 'HD 189733 b'")
    instrument: str = RuntimeField(
        default="",
        description=("Only points from this instrument (case-insensitive), "
                     "e.g. 'IRAC'. Empty string means all instruments."))
    max_points: Optional[int] = RuntimeField(
        default=500, description="Cap on returned points")

    def _run(self) -> str:
        try:
            rows = ExoplanetArchive().transit_spectrum(self.planet_name)
        except ArchiveError as e:
            return _err(str(e))
        if not rows:
            return _err(
                f"no transitspec rows for '{self.planet_name}'",
                hint=("Resolve the name first with resolveplanetname, then "
                      "try atmosphericspectra: the Atmospheric Spectroscopy "
                      "table covers 99 planets this table does not."))

        shaped = shape_transit_rows(rows, instrument=self.instrument or None)
        if not shaped["n_points"]:
            return _err(
                f"transitspec has rows for '{self.planet_name}' but none "
                f"match instrument '{self.instrument}'",
                instruments_present=sorted(
                    shape_transit_rows(rows)["instruments"]))
        cap = max(1, int(self.max_points or 500))
        truncated = shaped["n_points"] > cap
        shaped["points"] = shaped["points"][:cap]
        return _ok(planet=self.planet_name, truncated=truncated, **shaped)


class AtmosphericSpectraTool(BaseTool):
    """Fetch published spectra from the archive's Atmospheric Spectroscopy table.

    This is the file-backed table where JWST-era spectra land: 940
    transmission spectra for 203 planets as of 2026-09, against 104 planets
    in ``transitspec``. The archive hands out its files only through a wget
    script that a person generates by filtering and clicking in the Firefly
    page. That script is just ``host + session workspace + spec_path`` per
    file, and the workspace is minted by loading the page once — so this
    tool builds the same script from a planet name, with no browser, and
    can parse the files straight into points.

    Chain with ``downloaddataset`` (DownloadDataset): pass this result's ``wget_script`` as
    its ``wget_text`` and it produces TauREx-ready ``spectrum.dat`` files
    organised by planet, exactly as if the script had come from the site.
    """

    planet_name: str = RuntimeField(description="Archive pl_name, e.g. 'WASP-39 b'")
    spec_type: str = RuntimeField(
        default="Transmission",
        description="'Transmission' (default), 'Eclipse', 'Direct Imaging' or 'any'")
    bibcode: str = RuntimeField(
        default="",
        description=("Only spectra from this reference (ADS bibcode; substring "
                     "ok). Empty string means all references."))
    instrument: str = RuntimeField(
        default="",
        description=("Only spectra whose instrument contains this text "
                     "(case-insensitive), e.g. 'NIRSpec' or 'G395H'. Empty "
                     "string means all instruments."))
    include_points: bool = RuntimeField(
        default=False,
        description="Also fetch and parse each spectrum's points (one request per spectrum)")
    max_spectra: int = RuntimeField(
        default=20, description="Cap on spectra returned (and files fetched)")
    max_points: int = RuntimeField(
        default=500, description="Cap on points per spectrum when include_points")

    def _run(self) -> str:
        arc = ExoplanetArchive()
        kind = (self.spec_type or "Transmission").strip()
        try:
            index = arc.spectra_index(self.planet_name, kind)
        except ArchiveError as e:
            return _err(str(e))
        if not index:
            return _err(
                f"no {kind} spectra for '{self.planet_name}' in the "
                "Atmospheric Spectroscopy table",
                hint=("Resolve the name first with resolveplanetname, or "
                      "try spec_type='any'. transmissionspectrum reads the "
                      "older transitspec table, which covers some planets "
                      "this one does not."))

        want_bib = (self.bibcode or "").strip().lower()
        want_inst = (self.instrument or "").strip().lower()
        matched = [
            r for r in index
            if (not want_bib or want_bib in str(r.get("bibcode") or "").lower())
            and (not want_inst or want_inst in str(r.get("instrument") or "").lower())]
        if not matched:
            return _err(
                f"{len(index)} {kind} spectra for '{self.planet_name}', "
                "none matching the bibcode/instrument filter",
                available=[{"bibcode": r.get("bibcode"),
                            "instrument": r.get("instrument")} for r in index])

        cap = max(1, int(self.max_spectra or 20))
        selected, skipped = matched[:cap], matched[cap:]
        try:
            workspace = arc.mint_spectra_workspace()
        except ArchiveError as e:
            return _err(str(e))

        spectra: List[dict] = []
        warnings: List[str] = []
        for r in selected:
            spec_path = str(r["spec_path"])
            entry = {
                "bibcode": r.get("bibcode"),
                "reference": r.get("authors"),
                "spec_type": r.get("spec_type"),
                "instrument": r.get("instrument"),
                "facility": r.get("facility"),
                "n_points": _int(r.get("num_datapoints")),
                "wavelength_min_um": r.get("minwavelng"),
                "wavelength_max_um": r.get("maxwavelng"),
                "obs_date_min_bjd": r.get("mintranmid"),
                "obs_date_max_bjd": r.get("maxtranmid"),
                "note": r.get("note"),
                "file": spec_path.rsplit("/", 1)[-1],
                "url": arc.spectrum_file_url(spec_path),
            }
            if self.include_points:
                try:
                    table = parse_ipac_table(arc.spectrum_file(spec_path))
                    entry.update(shape_spectrum_file(
                        r, table, max_points=int(self.max_points or 500)))
                except (ArchiveError, ValueError) as e:
                    warnings.append(f"{entry['file']}: {e}")
            spectra.append(entry)

        script = wget_script(
            [(s["file"], s["url"]) for s in spectra],
            comment=(f"NASA Exoplanet Archive {kind} spectra for "
                     f"{self.planet_name}; generated by atmosphericspectra"))
        out = dict(
            planet=self.planet_name, spec_type=kind,
            n_available=len(index), n_matching=len(matched),
            n_returned=len(spectra), workspace=workspace,
            spectra=spectra, wget_script=script,
            next_step=("Pass wget_script to downloaddataset(wget_text=...) "
                       "for TauREx-ready spectrum.dat files. The URLs are "
                       "session-scoped: rerun this tool if they 404."))
        if skipped:
            out["skipped"] = [{"bibcode": r.get("bibcode"),
                               "instrument": r.get("instrument")} for r in skipped]
        if warnings:
            out["warnings"] = warnings
        return _ok(**out)


class ExoplanetArchiveQueryTool(BaseTool):
    """Run raw ADQL against the archive, for questions the other tools miss.

    Useful tables: ``ps`` (one row per planet per reference), ``pscomppars``
    (one composite row per planet), ``transitspec`` and ``emissionspec``
    (wavelength-resolved, with per-row facility and instrument).

    Note that a malformed query comes back as HTTP 200 with an Oracle error
    in the body; that is detected and surfaced as an error here.
    """

    adql: str = RuntimeField(
        description="ADQL, e.g. select pl_name,pl_rade from ps where hostname='TRAPPIST-1'")
    max_rows: Optional[int] = RuntimeField(default=200, description="Cap on returned rows")

    def _run(self) -> str:
        try:
            rows = ExoplanetArchive().query(self.adql)
        except ArchiveError as e:
            return _err(str(e), adql=self.adql)
        cap = max(1, int(self.max_rows or 200))
        return _ok(adql=self.adql, n_rows=len(rows),
                   truncated=len(rows) > cap, rows=rows[:cap])
