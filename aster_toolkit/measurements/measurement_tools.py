"""
Exoplanet tools: check a paper's claimed measurements against the archive.

  ResolvePlanetNameTool     paper spelling -> archive pl_name
  PublishedMeasurementsTool every published value, with its reference
  MeasurementDisagreementTool  quantify the spread and attribute instruments
  ExoplanetArchiveQueryTool raw ADQL, for anything the above does not cover

The intended chain is: pull a claimed value out of a paper, resolve the
planet name, then ask what else has been
published for it and whether the claim sits inside or outside that spread.

None of these tools decide who is right. They quantify, attribute, and hand
back the references.
"""

from __future__ import annotations

import json
from typing import List, Optional

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField

from .archive_interface import ArchiveError, ExoplanetArchive
from .disagreement import PARAMETERS, analyse, compare_parameter
from .explain import explain_pair
from .spectra import shape_transit_rows

SCHEMA_VERSION = "exoplanet-1.0"


def _err(msg: str, **extra) -> str:
    return json.dumps({"status": "error", "error": msg,
                       "schema_version": SCHEMA_VERSION, **extra}, indent=2)


def _ok(**payload) -> str:
    return json.dumps({"status": "ok", "schema_version": SCHEMA_VERSION,
                       **payload}, indent=2, default=str)


class ResolvePlanetNameTool(BaseTool):
    """Map a planet name as written in a paper onto the archive's spelling.

    Papers write "K2-18b" where the archive writes "K2-18 b", and an exact
    match silently returns nothing. Always resolve before querying, and treat
    multiple candidates as a question for the user rather than picking one.
    """

    name: str = "resolve_planet_name"
    description: str = (
        "Resolve a planet name as written in a paper to the NASA Exoplanet "
        "Archive's canonical pl_name. Returns candidates, not a single answer."
    )

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

    name: str = "published_measurements"
    description: str = (
        "Get every published measurement set for a planet from the NASA "
        "Exoplanet Archive ps table (one row per published reference), with "
        "radius, mass, period, uncertainties and the source reference."
    )

    planet_name: str = RuntimeField(description="Archive pl_name, e.g. 'HD 189733 b'")

    def _run(self) -> str:
        try:
            rows = ExoplanetArchive().published_values(self.planet_name)
        except ArchiveError as e:
            return _err(str(e))
        if not rows:
            return _err(f"no rows in ps for '{self.planet_name}'",
                        hint="Resolve the name first with resolve_planet_name.")
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

    name: str = "measurement_disagreement"
    description: str = (
        "Explain how published measurements of a planet disagree: the spread "
        "of each parameter across references, whether uncertainties absorb "
        "it, and an instrument breakdown from the spectroscopy tables. For "
        "WHY a specific pair differs, follow up with "
        "explain_measurement_disagreement."
    )

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
                        hint="Resolve the name first with resolve_planet_name.")

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

    ``measurement_disagreement`` finds and quantifies a disagreement; this
    tool follows the two references of the worst-tension pair to their
    papers (bibcode -> ADS link gateway -> arXiv abstract, keyless) and
    reports what kind of analysis each side says it did, and how the two
    differ. When the abstracts describe the same kind of analysis, it says
    so and hands back the arXiv ids to read instead of guessing.
    """

    name: str = "explain_measurement_disagreement"
    description: str = (
        "Explain WHY two published values of a planet parameter disagree: "
        "follow each reference to its paper and compare the methods the "
        "papers themselves describe. Keyless; abstract-level."
    )

    planet_name: str = RuntimeField(description="Archive pl_name, e.g. 'HD 189733 b'")
    parameter: Optional[str] = RuntimeField(
        default=None,
        description=("ps column to explain, e.g. 'pl_orbper'. Omit to pick "
                     "the parameter with the worst tension automatically."))

    def _run(self) -> str:
        try:
            rows = ExoplanetArchive().published_values(self.planet_name)
        except ArchiveError as e:
            return _err(str(e))
        if not rows:
            return _err(f"no rows in ps for '{self.planet_name}'",
                        hint="Resolve the name first with resolve_planet_name.")

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
                hint="published_measurements shows what the rows carry.")
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

    Reads ``transitspec`` over keyless TAP — no Firefly clicking, no wget
    lists — and returns wavelength-ordered points with the transit depth
    already propagated from the quoted Rp/R* ratio, plus per-row facility,
    instrument and reference. The per-instrument summary says who observed
    this planet over which wavelength range before you read a single point.

    The points feed a TauREx observed spectrum directly: wavelength_um,
    depth ( = (Rp/R*)^2 ), depth_err, bin_um.
    """

    name: str = "transmission_spectrum"
    description: str = (
        "Fetch the published transmission spectrum of a planet from the "
        "NASA Exoplanet Archive transitspec table: wavelength-resolved "
        "transit depths with per-point instrument, facility and reference. "
        "Keyless; ready for a TauREx observed-spectrum file."
    )

    planet_name: str = RuntimeField(description="Archive pl_name, e.g. 'HD 189733 b'")
    instrument: Optional[str] = RuntimeField(
        default=None,
        description="Only points from this instrument (case-insensitive), e.g. 'IRAC'")
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
                hint=("Resolve the name first with resolve_planet_name. The "
                      "spectroscopy tables also lag the literature — a "
                      "recently observed planet may simply not be in them."))

        shaped = shape_transit_rows(rows, instrument=self.instrument)
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


class ExoplanetArchiveQueryTool(BaseTool):
    """Run raw ADQL against the archive, for questions the other tools miss.

    Useful tables: ``ps`` (one row per planet per reference), ``pscomppars``
    (one composite row per planet), ``transitspec`` and ``emissionspec``
    (wavelength-resolved, with per-row facility and instrument).

    Note that a malformed query comes back as HTTP 200 with an Oracle error
    in the body; that is detected and surfaced as an error here.
    """

    name: str = "exoplanet_archive_query"
    description: str = (
        "Run an ADQL query against the NASA Exoplanet Archive TAP service. "
        "Tables include ps, pscomppars, transitspec, emissionspec."
    )

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
