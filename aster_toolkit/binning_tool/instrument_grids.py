
import numpy as np

__all__ = [
    'INSTRUMENT_MODES',
    'get_instrument_grid',
    'list_instruments',
]


# Instrument modes

# Each entry:  key → {telescope, instrument, mode, wl_min, wl_max, R, description}
INSTRUMENT_MODES = {
    # ---- JWST ----
    'jwst_nirspec_prism': {
        'telescope': 'JWST',
        'instrument': 'NIRSpec',
        'mode': 'PRISM',
        'wl_min': 0.6,
        'wl_max': 5.3,
        'R': 100,
        'description': 'JWST NIRSpec PRISM — low-resolution, broad coverage (0.6–5.3 μm, R~100)',
    },
    'jwst_nirspec_g140m': {
        'telescope': 'JWST',
        'instrument': 'NIRSpec',
        'mode': 'G140M',
        'wl_min': 0.7,
        'wl_max': 1.27,
        'R': 1000,
        'description': 'JWST NIRSpec G140M — medium-resolution (0.7–1.27 μm, R~1000)',
    },
    'jwst_nirspec_g235m': {
        'telescope': 'JWST',
        'instrument': 'NIRSpec',
        'mode': 'G235M',
        'wl_min': 1.66,
        'wl_max': 3.17,
        'R': 1000,
        'description': 'JWST NIRSpec G235M — medium-resolution (1.66–3.17 μm, R~1000)',
    },
    'jwst_nirspec_g395m': {
        'telescope': 'JWST',
        'instrument': 'NIRSpec',
        'mode': 'G395M',
        'wl_min': 2.87,
        'wl_max': 5.27,
        'R': 1000,
        'description': 'JWST NIRSpec G395M — medium-resolution (2.87–5.27 μm, R~1000)',
    },
    'jwst_nirspec_g140h': {
        'telescope': 'JWST',
        'instrument': 'NIRSpec',
        'mode': 'G140H',
        'wl_min': 0.7,
        'wl_max': 1.27,
        'R': 2700,
        'description': 'JWST NIRSpec G140H — high-resolution (0.7–1.27 μm, R~2700)',
    },
    'jwst_nirspec_g235h': {
        'telescope': 'JWST',
        'instrument': 'NIRSpec',
        'mode': 'G235H',
        'wl_min': 1.66,
        'wl_max': 3.17,
        'R': 2700,
        'description': 'JWST NIRSpec G235H — high-resolution (1.66–3.17 μm, R~2700)',
    },
    'jwst_nirspec_g395h': {
        'telescope': 'JWST',
        'instrument': 'NIRSpec',
        'mode': 'G395H',
        'wl_min': 2.87,
        'wl_max': 5.27,
        'R': 2700,
        'description': 'JWST NIRSpec G395H — high-resolution (2.87–5.27 μm, R~2700)',
    },
    'jwst_niriss_soss': {
        'telescope': 'JWST',
        'instrument': 'NIRISS',
        'mode': 'SOSS',
        'wl_min': 0.6,
        'wl_max': 2.8,
        'R': 700,
        'description': 'JWST NIRISS SOSS — single-object slitless spectroscopy (0.6–2.8 μm, R~700)',
    },
    'jwst_nircam_grism': {
        'telescope': 'JWST',
        'instrument': 'NIRCam',
        'mode': 'Grism (F322W2/F444W)',
        'wl_min': 2.4,
        'wl_max': 5.0,
        'R': 1600,
        'description': 'JWST NIRCam Grism — long-wavelength grism time-series (2.4–5.0 μm, R~1600)',
    },
    'jwst_miri_lrs': {
        'telescope': 'JWST',
        'instrument': 'MIRI',
        'mode': 'LRS',
        'wl_min': 5.0,
        'wl_max': 12.0,
        'R': 100,
        'description': 'JWST MIRI LRS — low-resolution spectroscopy (5–12 μm, R~100)',
    },
    # ---- HST ----
    'hst_wfc3_g141': {
        'telescope': 'HST',
        'instrument': 'WFC3',
        'mode': 'G141',
        'wl_min': 1.1,
        'wl_max': 1.7,
        'R': 130,
        'description': 'HST WFC3 G141 — near-IR grism, water feature band (1.1–1.7 μm, R~130)',
    },
    'hst_wfc3_g102': {
        'telescope': 'HST',
        'instrument': 'WFC3',
        'mode': 'G102',
        'wl_min': 0.8,
        'wl_max': 1.15,
        'R': 210,
        'description': 'HST WFC3 G102 — optical/near-IR grism (0.8–1.15 μm, R~210)',
    },
    'hst_stis_g430l': {
        'telescope': 'HST',
        'instrument': 'STIS',
        'mode': 'G430L',
        'wl_min': 0.29,
        'wl_max': 0.57,
        'R': 530,
        'description': 'HST STIS G430L — optical, Rayleigh/haze diagnostic (0.29–0.57 μm, R~530)',
    },
    'hst_stis_g750l': {
        'telescope': 'HST',
        'instrument': 'STIS',
        'mode': 'G750L',
        'wl_min': 0.52,
        'wl_max': 1.03,
        'R': 530,
        'description': 'HST STIS G750L — red optical, alkali metals (0.52–1.03 μm, R~530)',
    },
    # ---- Ariel ----
    'ariel_airs_ch0': {
        'telescope': 'Ariel',
        'instrument': 'AIRS',
        'mode': 'CH0',
        'wl_min': 1.95,
        'wl_max': 3.9,
        'R': 100,
        'description': 'Ariel AIRS Channel 0 — near-IR spectrometer (1.95–3.9 μm, R~100)',
    },
    'ariel_airs_ch1': {
        'telescope': 'Ariel',
        'instrument': 'AIRS',
        'mode': 'CH1',
        'wl_min': 3.9,
        'wl_max': 7.8,
        'R': 30,
        'description': 'Ariel AIRS Channel 1 — mid-IR spectrometer (3.9–7.8 μm, R~30)',
    },
    'ariel_nirspec': {
        'telescope': 'Ariel',
        'instrument': 'FGS-NIRSpec',
        'mode': 'NIR',
        'wl_min': 1.25,
        'wl_max': 1.95,
        'R': 20,
        'description': 'Ariel FGS NIR Spectrometer — low-resolution near-IR (1.25–1.95 μm, R~20)',
    },
    # ---- ELT / VLT ----
    'elt_harmoni': {
        'telescope': 'ELT',
        'instrument': 'HARMONI',
        'mode': 'H+K',
        'wl_min': 1.45,
        'wl_max': 2.45,
        'R': 3500,
        'description': 'ELT HARMONI H+K — high-resolution IFS (1.45–2.45 μm, R~3500)',
    },
    'elt_crires_k': {
        'telescope': 'ELT/VLT',
        'instrument': 'CRIRES+',
        'mode': 'K-band',
        'wl_min': 1.9,
        'wl_max': 2.5,
        'R': 100000,
        'description': 'VLT CRIRES+ K-band — ultra-high-resolution echelle (1.9–2.5 μm, R~100000)',
    },
}


def _generate_log_grid(wl_min: float, wl_max: float, R: float):
    """
    Generate a contiguous log-uniform wavelength grid at resolving power R.

    Bin edges are spaced geometrically: edge_{k+1} = edge_k * (1 + 1/R).
    The final edge is snapped to wl_max; a degenerate trailing sliver
    (relative width < 0.5 of the previous bin) is merged into the previous bin.

    Parameters
    ----------
    wl_min : float
        Minimum wavelength in μm.
    wl_max : float
        Maximum wavelength in μm.
    R : float
        Resolving power (λ/Δλ).

    Returns
    -------
    wl_centers : np.ndarray
        Bin centre wavelengths in μm.
    half_bin_widths : np.ndarray
        Half-bin widths in μm.
    """
    if wl_min <= 0 or wl_max <= wl_min:
        raise ValueError(f"Invalid wavelength range: {wl_min}–{wl_max}")
    if R <= 0:
        raise ValueError(f"Resolving power must be positive, got {R}")

    factor = 1.0 + 1.0 / R
    edges = [float(wl_min)]
    while edges[-1] * factor < wl_max:
        edges.append(edges[-1] * factor)
    edges.append(float(wl_max))

    # Merge a degenerate final sliver into the previous bin
    if len(edges) >= 3:
        last_width = edges[-1] - edges[-2]
        prev_width = edges[-2] - edges[-3]
        if last_width < 0.5 * prev_width:
            edges[-2] = edges[-1]
            edges.pop()

    edges = np.asarray(edges, dtype=float)
    centers = 0.5 * (edges[:-1] + edges[1:])
    half_bin_widths = 0.5 * (edges[1:] - edges[:-1])
    return centers, half_bin_widths


def get_instrument_grid(name: str):
    """
    Return the wavelength grid for a named instrument mode.

    Parameters
    ----------
    name : str
        Instrument mode key, e.g. ``'jwst_nirspec_prism'``.
        Case-insensitive.

    Returns
    -------
    wl_centers : np.ndarray
        Bin centre wavelengths in μm.
    half_bin_widths : np.ndarray
        Half-bin widths in μm.
    info : dict
        Metadata dict for this instrument mode.

    Raises
    ------
    KeyError
        If *name* is not a recognised instrument mode.
    """
    key = name.strip().lower()
    if key not in INSTRUMENT_MODES:
        available = ', '.join(sorted(INSTRUMENT_MODES.keys()))
        raise KeyError(
            f"Unknown instrument mode '{name}'. "
            f"Available modes: {available}"
        )

    info = INSTRUMENT_MODES[key]
    wl_centers, half_bin_widths = _generate_log_grid(
        info['wl_min'], info['wl_max'], info['R'],
    )
    return wl_centers, half_bin_widths, info


def list_instruments() -> list[dict]:
    """
    Return a list of all available instrument modes with metadata.

    Returns
    -------
    list[dict]
        Each dict has keys: key, telescope, instrument, mode, wl_min,
        wl_max, R, n_bins, description.
    """
    result = []
    for key, info in INSTRUMENT_MODES.items():
        wl, _ = _generate_log_grid(info['wl_min'], info['wl_max'], info['R'])
        entry = dict(info)
        entry['key'] = key
        entry['n_bins'] = len(wl)
        result.append(entry)
    return result