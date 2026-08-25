
import os
import numpy as np

# NumPy 2.x renamed np.trapz → np.trapezoid
_trapezoid = getattr(np, 'trapezoid', None) or np.trapz

# Minimum fractional coverage of a target bin that must overlap the input
# spectrum; bins below this threshold are dropped.
_MIN_COVERAGE = 0.5

__all__ = [
    'generate_wavelength_grid',
    'bin_spectrum',
    'bin_to_resolving_power',
    'bin_to_n_bins',
    'load_spectrum',
    'save_binned_spectrum',
    'compute_half_bin_widths',
]

# Wavelength grid generation
def generate_wavelength_grid(wl_min: float, wl_max: float, R: float):
    """
    Generate a log-uniform wavelength grid at resolving power *R*.

    Parameters
    ----------
    wl_min : float
        Minimum wavelength (μm).
    wl_max : float
        Maximum wavelength (μm).
    R : float
        Resolving power (λ / Δλ).

    Returns
    -------
    wl_centers : np.ndarray
        Bin-centre wavelengths (μm).
    half_bin_widths : np.ndarray
        Half-bin widths (μm).
    """
    from .instrument_grids import _generate_log_grid
    return _generate_log_grid(wl_min, wl_max, R)


def _generate_n_bins_grid(wl_min: float, wl_max: float, n_bins: int):
    """Generate *n_bins* equally spaced bins in log-wavelength space."""
    log_edges = np.linspace(np.log(wl_min), np.log(wl_max), n_bins + 1)
    edges = np.exp(log_edges)
    centers = 0.5 * (edges[:-1] + edges[1:])
    half_bin_widths = 0.5 * (edges[1:] - edges[:-1])
    return centers, half_bin_widths


def compute_half_bin_widths(wavelengths: np.ndarray) -> np.ndarray:
    """
    Compute half-bin widths from a wavelength array.

    Uses midpoints between adjacent wavelengths to define bin edges,
    with edge bins extended symmetrically.
    """
    wavelengths = np.asarray(wavelengths, dtype=float)
    if len(wavelengths) == 1:
        return np.array([0.1])

    midpoints = 0.5 * (wavelengths[:-1] + wavelengths[1:])
    left_edges = np.empty_like(wavelengths)
    right_edges = np.empty_like(wavelengths)

    left_edges[0] = wavelengths[0] - (midpoints[0] - wavelengths[0])
    left_edges[1:] = midpoints
    right_edges[-1] = wavelengths[-1] + (wavelengths[-1] - midpoints[-1])
    right_edges[:-1] = midpoints

    return 0.5 * (right_edges - left_edges)


# Flux-conserving rebinning using trapezoidal integration
def _bin_spectrum_custom(
    old_wl: np.ndarray,
    old_flux: np.ndarray,
    new_wl: np.ndarray,
    new_half_bin_widths: np.ndarray,
    old_errors: np.ndarray | None = None,
):
    """
    Flux-conserving rebinning using trapezoidal integration.

    For each new bin, integrate the old spectrum over the overlapping
    interval and divide by the covered width.  Error bars are propagated
    with weights consistent with the trapezoidal integral:
        sigma_bin = sqrt(sum(w_i^2 sigma_i^2)) / sum(w_i)

    Returns
    -------
    new_flux, new_errors, flags
        flags is a dict with keys ``partial``, ``empty``, ``dropped``
        (boolean arrays over the *input* new_wl length before dropping).
    """
    n = len(new_wl)
    new_flux = np.full(n, np.nan)
    new_errors_out = np.full(n, np.nan) if old_errors is not None else None
    flags = {
        'partial': np.zeros(n, dtype=bool),
        'empty': np.zeros(n, dtype=bool),
        'dropped': np.zeros(n, dtype=bool),
        'coverage': np.zeros(n, dtype=float),
    }

    input_min, input_max = float(old_wl[0]), float(old_wl[-1])

    for i in range(n):
        lo = new_wl[i] - new_half_bin_widths[i]
        hi = new_wl[i] + new_half_bin_widths[i]
        bin_width = hi - lo

        # Covered sub-interval (no extrapolation beyond input)
        cov_lo = max(lo, input_min)
        cov_hi = min(hi, input_max)
        covered = max(0.0, cov_hi - cov_lo)
        coverage = covered / bin_width if bin_width > 0 else 0.0
        flags['coverage'][i] = coverage

        if coverage < _MIN_COVERAGE:
            flags['dropped'][i] = True
            continue

        if coverage < 1.0 - 1e-12:
            flags['partial'][i] = True

        # Old pixels strictly inside the covered interval
        mask = (old_wl >= cov_lo) & (old_wl <= cov_hi)
        idx = np.where(mask)[0]

        if len(idx) == 0:
            # No old pixels in the covered range — interpolate at bin centre
            # (only valid if the centre itself is inside the input range)
            if cov_lo <= new_wl[i] <= cov_hi and input_min <= new_wl[i] <= input_max:
                new_flux[i] = np.interp(new_wl[i], old_wl, old_flux)
                if new_errors_out is not None:
                    new_errors_out[i] = np.interp(new_wl[i], old_wl, old_errors)
                flags['empty'][i] = True
            else:
                flags['dropped'][i] = True
            continue

        sub_wl = old_wl[idx].astype(float)
        sub_flux = old_flux[idx].astype(float)

        # Add interpolated values at covered edges for trapezoidal integral
        if sub_wl[0] > cov_lo:
            edge_flux = np.interp(cov_lo, old_wl, old_flux)
            sub_wl = np.concatenate([[cov_lo], sub_wl])
            sub_flux = np.concatenate([[edge_flux], sub_flux])
        if sub_wl[-1] < cov_hi:
            edge_flux = np.interp(cov_hi, old_wl, old_flux)
            sub_wl = np.concatenate([sub_wl, [cov_hi]])
            sub_flux = np.concatenate([sub_flux, [edge_flux]])

        integral = _trapezoid(sub_flux, sub_wl)
        new_flux[i] = integral / covered

        if new_errors_out is not None:
            # Trapezoidal node weights: half-width of adjacent segments
            seg = np.diff(sub_wl)
            weights = np.zeros(len(sub_wl))
            weights[0] = 0.5 * seg[0]
            weights[-1] = 0.5 * seg[-1]
            if len(sub_wl) > 2:
                weights[1:-1] = 0.5 * (seg[:-1] + seg[1:])

            # Map node errors: edge nodes use interpolated errors
            node_errors = np.empty(len(sub_wl))
            # Identify which nodes came from original pixels
            orig_start = 0
            if abs(sub_wl[0] - old_wl[idx[0]]) > 1e-15:
                node_errors[0] = np.interp(sub_wl[0], old_wl, old_errors)
                orig_start = 1
            orig_end = len(sub_wl)
            if abs(sub_wl[-1] - old_wl[idx[-1]]) > 1e-15:
                node_errors[-1] = np.interp(sub_wl[-1], old_wl, old_errors)
                orig_end = len(sub_wl) - 1
            node_errors[orig_start:orig_end] = old_errors[idx]

            w_sum = np.sum(weights)
            if w_sum > 0:
                new_errors_out[i] = np.sqrt(np.sum((weights * node_errors) ** 2)) / w_sum
            else:
                new_errors_out[i] = np.interp(new_wl[i], old_wl, old_errors)

    return new_flux, new_errors_out, flags


# Rebinning using the spectres package
def _bin_spectrum_spectres(
    old_wl: np.ndarray,
    old_flux: np.ndarray,
    new_wl: np.ndarray,
    new_half_bin_widths: np.ndarray,
    old_errors: np.ndarray | None = None,
):
    """
    Rebinning using the ``spectres`` package (flux-conserving).

    Parameters are identical to ``_bin_spectrum_custom``.
    """
    try:
        import spectres
    except ImportError:
        raise ImportError(
            "The 'spectres' package is required for method='spectres'. "
            "Install it with: pip install spectres"
        )

    if old_errors is not None:
        new_flux, new_errors_out = spectres.spectres(
            new_wl, old_wl, old_flux, spec_errs=old_errors,
        )
    else:
        new_flux = spectres.spectres(new_wl, old_wl, old_flux)
        new_errors_out = None

    n = len(new_wl)
    flags = {
        'partial': np.zeros(n, dtype=bool),
        'empty': np.isnan(new_flux) if hasattr(new_flux, '__len__') else np.zeros(n, dtype=bool),
        'dropped': np.zeros(n, dtype=bool),
        'coverage': np.ones(n, dtype=float),
    }
    return new_flux, new_errors_out, flags



def bin_spectrum(
    old_wl: np.ndarray,
    old_flux: np.ndarray,
    new_wl: np.ndarray,
    new_half_bin_widths: np.ndarray,
    old_errors: np.ndarray | None = None,
    method: str = 'spectres',
) -> dict:
    """
    Rebin a spectrum onto a new wavelength grid.

    Parameters
    ----------
    old_wl : np.ndarray
        Input wavelength array (μm, sorted ascending).
    old_flux : np.ndarray
        Input flux / transit-depth array.
    new_wl : np.ndarray
        Target bin centres (μm).
    new_half_bin_widths : np.ndarray
        Target half-bin widths (μm).
    old_errors : np.ndarray, optional
        Uncertainties on *old_flux*.
    method : str
        Binning backend: ``'spectres'`` (default) or ``'custom'``.

    Returns
    -------
    dict
        Keys: ``wl``, ``half_bin_width``, ``flux``, ``error`` (or None),
        ``n_partial``, ``n_empty``, ``n_dropped``, ``coverage``.
    """
    # Ensure sorted ascending
    sort_idx = np.argsort(old_wl)
    old_wl = np.asarray(old_wl, dtype=float)[sort_idx]
    old_flux = np.asarray(old_flux, dtype=float)[sort_idx]
    if old_errors is not None:
        old_errors = np.asarray(old_errors, dtype=float)[sort_idx]

    new_wl = np.asarray(new_wl, dtype=float)
    new_half_bin_widths = np.asarray(new_half_bin_widths, dtype=float)

    # Keep bins that have any potential overlap with the input range
    input_min, input_max = old_wl[0], old_wl[-1]
    overlap = (
        (new_wl + new_half_bin_widths > input_min) &
        (new_wl - new_half_bin_widths < input_max)
    )
    new_wl_clipped = new_wl[overlap]
    new_hbw_clipped = new_half_bin_widths[overlap]

    if len(new_wl_clipped) == 0:
        raise ValueError(
            f"No overlap between input spectrum ({input_min:.4f}–{input_max:.4f} μm) "
            f"and target grid ({new_wl[0]:.4f}–{new_wl[-1]:.4f} μm)."
        )
    if method == 'spectres':
        new_flux, new_errors, flags = _bin_spectrum_spectres(
            old_wl, old_flux, new_wl_clipped, new_hbw_clipped, old_errors,
        )
    elif method == 'custom':
        new_flux, new_errors, flags = _bin_spectrum_custom(
            old_wl, old_flux, new_wl_clipped, new_hbw_clipped, old_errors,
        )
    else:
        raise ValueError(
            f"Unknown method '{method}'. Use 'spectres' or 'custom'."
        )

    # Drop bins that failed coverage / empty-outside-range checks
    keep = ~flags['dropped']
    # Also drop any remaining NaN fluxes
    keep = keep & np.isfinite(new_flux)

    return {
        'wl': new_wl_clipped[keep],
        'half_bin_width': new_hbw_clipped[keep],
        'flux': new_flux[keep],
        'error': new_errors[keep] if new_errors is not None else None,
        'n_partial': int(np.sum(flags['partial'] & keep)),
        'n_empty': int(np.sum(flags['empty'] & keep)),
        'n_dropped': int(np.sum(~keep)),
        'coverage': flags['coverage'][keep],
    }


def bin_to_resolving_power(
    old_wl: np.ndarray,
    old_flux: np.ndarray,
    R: float,
    wl_min: float | None = None,
    wl_max: float | None = None,
    old_errors: np.ndarray | None = None,
    method: str = 'spectres',
) -> dict:
    """
    Bin a spectrum to a target resolving power R.

    Parameters
    ----------
    old_wl : np.ndarray
        Input wavelengths (μm).
    old_flux : np.ndarray
        Input flux / transit-depth.
    R : float
        Target resolving power.
    wl_min, wl_max : float, optional
        Wavelength range for new grid.  Defaults to input range.
    old_errors : np.ndarray, optional
        Input uncertainties.
    method : str
        ``'spectres'`` or ``'custom'``.

    Returns
    -------
    dict
        Binned spectrum (see ``bin_spectrum``).
    """
    if wl_min is None:
        wl_min = float(np.min(old_wl))
    if wl_max is None:
        wl_max = float(np.max(old_wl))

    new_wl, new_hbw = generate_wavelength_grid(wl_min, wl_max, R)
    return bin_spectrum(old_wl, old_flux, new_wl, new_hbw, old_errors, method)


def bin_to_n_bins(
    old_wl: np.ndarray,
    old_flux: np.ndarray,
    n_bins: int,
    wl_min: float | None = None,
    wl_max: float | None = None,
    old_errors: np.ndarray | None = None,
    method: str = 'spectres',
) -> dict:
    """
    Bin a spectrum into *n_bins* equally spaced (in log λ) bins.

    Parameters
    ----------
    old_wl : np.ndarray
        Input wavelengths (μm).
    old_flux : np.ndarray
        Input flux / transit-depth.
    n_bins : int
        Number of output bins.
    wl_min, wl_max : float, optional
        Wavelength range.  Defaults to input range.
    old_errors : np.ndarray, optional
        Input uncertainties.
    method : str
        ``'spectres'`` or ``'custom'``.

    Returns
    -------
    dict
        Binned spectrum (see ``bin_spectrum``).
    """
    if wl_min is None:
        wl_min = float(np.min(old_wl))
    if wl_max is None:
        wl_max = float(np.max(old_wl))

    new_wl, new_hbw = _generate_n_bins_grid(wl_min, wl_max, n_bins)
    return bin_spectrum(old_wl, old_flux, new_wl, new_hbw, old_errors, method)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def load_spectrum(file_path: str, base_directory: str = '') -> dict:
    """
    Load a spectrum from various formats, auto-detecting the type.

    Supported formats
    -----------------
    - **Exo_Skryer 4-column** (.dat/.txt): wavelength, half_bin_width, depth, error
    - **2-column** (.dat/.txt): wavelength, flux
    - **TauREx 3-column** (.dat/.txt): wavelength, depth, error
    - **TauREx 4-column** (.dat/.txt): wavelength, depth, error, bin_width
    - **.npy pair**: ``*_wavelength.npy`` + ``*_spectrum.npy``
      (also matches TauREx FM ``*_fm_wavelength.npy`` / ``*_fm_spectrum.npy``)

    Returns
    -------
    dict
        Keys: ``wl``, ``flux``, ``error``, ``half_bin_width``, ``format``,
        ``n_dropped_nan``, ``n_deduped``, ``was_sorted``.
    """
    full_path = os.path.join(base_directory, file_path) if base_directory else file_path

    # --- .npy pair ---
    if (
        full_path.endswith('_wavelength.npy')
        or full_path.endswith('_spectrum.npy')
        or full_path.endswith('.npy')
    ):
        result = _load_npy_pair(full_path)
        return _sanitize_spectrum(result)

    # --- text file ---
    data = np.genfromtxt(full_path, comments='#', dtype=float)
    if data.ndim == 1:
        data = data[None, :]

    ncols = data.shape[1]

    if ncols == 2:
        result = {
            'wl': data[:, 0],
            'flux': data[:, 1],
            'error': None,
            'half_bin_width': None,
            'format': '2-column (wavelength, flux)',
        }
    elif ncols == 3:
        result = {
            'wl': data[:, 0],
            'flux': data[:, 1],
            'error': data[:, 2],
            'half_bin_width': None,
            'format': 'TauREx 3-column (wavelength, depth, error)',
        }
    elif ncols >= 4:
        # Distinguish Exo_Skryer (wl, half_bin_width, depth, error) from
        # TauREx 4-col (wl, depth, error, bin_width).
        #
        # Key: in TauREx, col4 ≈ wavelength spacing (full bin width);
        # in Exo_Skryer, col2 ≈ half wavelength spacing and col4 is an error
        # (typically ≪ bin width).
        col2 = np.abs(data[:, 1])
        col4 = np.abs(data[:, 3])
        wl_diffs = np.abs(np.diff(data[:, 0]))
        median_wl_diff = float(np.median(wl_diffs)) if len(wl_diffs) else 0.0
        denom = max(median_wl_diff, 1e-10)
        ratio_col2 = float(np.median(col2)) / denom
        ratio_col4 = float(np.median(col4)) / denom

        # Prefer TauREx when col4 tracks the wavelength spacing
        if 0.3 < ratio_col4 < 5.0:
            is_exoskryer = False
        elif 0.1 < ratio_col2 < 5.0:
            is_exoskryer = True
        else:
            # Default to TauREx (native ASTER / new_aster format)
            is_exoskryer = False

        if is_exoskryer:
            result = {
                'wl': data[:, 0],
                'flux': data[:, 2],
                'error': data[:, 3],
                'half_bin_width': data[:, 1],
                'format': 'Exo_Skryer 4-column (wavelength, half_bin_width, depth, error)',
            }
        else:
            result = {
                'wl': data[:, 0],
                'flux': data[:, 1],
                'error': data[:, 2],
                'half_bin_width': data[:, 3] / 2.0,
                'format': 'TauREx 4-column (wavelength, depth, error, bin_width)',
            }
    else:
        raise ValueError(f"Unexpected number of columns ({ncols}) in {full_path}")

    return _sanitize_spectrum(result)


def _sanitize_spectrum(result: dict) -> dict:
    """Drop non-finite rows, sort ascending, deduplicate wavelengths."""
    wl = np.asarray(result['wl'], dtype=float)
    flux = np.asarray(result['flux'], dtype=float)
    error = result.get('error')
    hbw = result.get('half_bin_width')

    if error is not None:
        error = np.asarray(error, dtype=float)
    if hbw is not None:
        hbw = np.asarray(hbw, dtype=float)

    finite = np.isfinite(wl) & np.isfinite(flux)
    if error is not None:
        finite = finite & np.isfinite(error)
    n_dropped_nan = int(np.sum(~finite))
    wl, flux = wl[finite], flux[finite]
    if error is not None:
        error = error[finite]
    if hbw is not None:
        hbw = hbw[finite]

    was_sorted = bool(np.all(np.diff(wl) >= 0)) if len(wl) > 1 else True
    sort_idx = np.argsort(wl)
    wl, flux = wl[sort_idx], flux[sort_idx]
    if error is not None:
        error = error[sort_idx]
    if hbw is not None:
        hbw = hbw[sort_idx]

    # Deduplicate wavelengths (keep first occurrence after sort)
    if len(wl) > 1:
        unique_mask = np.concatenate([[True], np.diff(wl) > 0])
        n_deduped = int(np.sum(~unique_mask))
        wl, flux = wl[unique_mask], flux[unique_mask]
        if error is not None:
            error = error[unique_mask]
        if hbw is not None:
            hbw = hbw[unique_mask]
    else:
        n_deduped = 0

    result['wl'] = wl
    result['flux'] = flux
    result['error'] = error
    result['half_bin_width'] = hbw
    result['n_dropped_nan'] = n_dropped_nan
    result['n_deduped'] = n_deduped
    result['was_sorted'] = was_sorted
    return result


def _load_npy_pair(path: str) -> dict:
    """Load a wavelength/spectrum .npy file pair."""
    for suffix in ('_wavelength.npy', '_spectrum.npy', '.npy'):
        if path.endswith(suffix):
            base = path[:-len(suffix)]
            break
    else:
        base = os.path.splitext(path)[0]

    wl_path = base + '_wavelength.npy'
    # Prefer *_spectrum.npy; also try *_fm_spectrum.npy style already covered
    # by stripping _wavelength / _spectrum symmetrically.
    flux_path = base + '_spectrum.npy'

    if not os.path.isfile(wl_path):
        raise FileNotFoundError(f"Wavelength file not found: {wl_path}")
    if not os.path.isfile(flux_path):
        raise FileNotFoundError(f"Spectrum file not found: {flux_path}")

    wl = np.load(wl_path)
    flux = np.load(flux_path)

    return {
        'wl': wl,
        'flux': flux,
        'error': None,
        'half_bin_width': None,
        'format': 'NumPy pair (*_wavelength.npy + *_spectrum.npy)',
    }


def save_binned_spectrum(
    result: dict,
    output_path: str,
    header_info: str = '',
) -> str:
    """
    Save a binned spectrum in TauREx 4-column format.

    Columns: wavelength_um, transit_depth, error, bin_width_um
    (bin_width is the full width = 2 * half_bin_width).

    If no errors are available, writes 3 columns:
    wavelength_um, transit_depth, bin_width_um.
    """
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

    wl = result['wl']
    hbw = result['half_bin_width']
    flux = result['flux']
    error = result.get('error')
    bin_width = 2.0 * hbw

    if error is None:
        out_data = np.column_stack([wl, flux, bin_width])
        header = 'wavelength_um  transit_depth  bin_width_um'
    else:
        out_data = np.column_stack([wl, flux, error, bin_width])
        header = 'wavelength_um  transit_depth  error  bin_width_um'

    if header_info:
        header = f'{header_info}\n{header}'

    np.savetxt(output_path, out_data, fmt='%.10e', header=header)
    return output_path