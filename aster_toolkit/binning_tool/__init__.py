"""
binning_tool — spectrum binning package for ASTER.

Provides flux-conserving spectrum rebinning onto custom or instrument
wavelength grids.
"""

from .bin_spectrum_tool import BinSpectrum
from .spectrum_binner import (
    bin_spectrum,
    bin_to_resolving_power,
    bin_to_n_bins,
    load_spectrum,
    save_binned_spectrum,
    generate_wavelength_grid,
)
from .instrument_grids import (
    INSTRUMENT_MODES,
    get_instrument_grid,
    list_instruments,
)

__all__ = [
    'BinSpectrum',
    'bin_spectrum',
    'bin_to_resolving_power',
    'bin_to_n_bins',
    'load_spectrum',
    'save_binned_spectrum',
    'generate_wavelength_grid',
    'INSTRUMENT_MODES',
    'get_instrument_grid',
    'list_instruments',
]