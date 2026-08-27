"""
Exoplanet tools: check a paper's claims against the NASA Exoplanet Archive.

Pull a claimed measurement out of a paper, then ask the archive what else
has been published for that planet and whether the claim sits inside or
outside the published spread.

The archive is free and keyless (TAP/ADQL). One design fact governs
everything here: ``ps`` holds one row per planet PER PUBLISHED REFERENCE,
while ``pscomppars`` holds one composite row per planet. Only ``ps`` can
answer why two papers disagree.
"""

from .archive_interface import (
    ArchiveError,
    ExoplanetArchive,
    bibcode_of,
    normalize_instrument,
    reference_label,
)
from .disagreement import analyse, compare_parameter, tension_sigma
from .explain import explain_pair, method_fingerprint
from .spectra import shape_transit_rows
from .measurement_tools import (
    ExoplanetArchiveQueryTool,
    ExplainDisagreementTool,
    MeasurementDisagreementTool,
    PublishedMeasurementsTool,
    ResolvePlanetNameTool,
    TransmissionSpectrumTool,
)

__all__ = [
    "ResolvePlanetNameTool",
    "PublishedMeasurementsTool",
    "MeasurementDisagreementTool",
    "ExplainDisagreementTool",
    "TransmissionSpectrumTool",
    "ExoplanetArchiveQueryTool",
    "ExoplanetArchive",
    "ArchiveError",
    "analyse",
    "compare_parameter",
    "tension_sigma",
    "explain_pair",
    "method_fingerprint",
    "shape_transit_rows",
    "bibcode_of",
    "reference_label",
    "normalize_instrument",
]
