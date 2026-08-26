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
from .measurement_tools import (
    ExoplanetArchiveQueryTool,
    MeasurementDisagreementTool,
    PublishedMeasurementsTool,
    ResolvePlanetNameTool,
)

__all__ = [
    "ResolvePlanetNameTool",
    "PublishedMeasurementsTool",
    "MeasurementDisagreementTool",
    "ExoplanetArchiveQueryTool",
    "ExoplanetArchive",
    "ArchiveError",
    "analyse",
    "compare_parameter",
    "tension_sigma",
    "bibcode_of",
    "reference_label",
    "normalize_instrument",
]
