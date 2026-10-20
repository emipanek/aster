import os
import difflib

from orchestral.tools.filesystem.filesystem_tools import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField


class BinSpectrum(BaseTool):
    """Bin a high-resolution spectrum to lower resolution or to an instrument grid.

    Two modes of operation:
    1. **Custom binning**: Set ``resolving_power`` (R) or ``n_bins`` to bin the
       input spectrum to a custom wavelength grid.
    2. **Instrument binning**: Set ``instrument`` to one of the predefined
       instrument mode keys (e.g. ``'jwst_nirspec_prism'``, ``'hst_wfc3_g141'``)
       to bin directly onto that instrument's wavelength grid.

    Use ``instrument='list'`` to see all available instrument modes.

    **Supported input formats** (auto-detected):
    - TauREx 3-column: wavelength, depth, error
    - TauREx 4-column: wavelength, depth, error, bin_width
    - Exo_Skryer 4-column (.dat/.txt): wavelength, half_bin_width, depth, error
    - 2-column: wavelength, flux
    - NumPy pair: ``*_wavelength.npy`` + ``*_spectrum.npy``
      (including TauREx FM ``*_fm_wavelength.npy`` / ``*_fm_spectrum.npy``)

    Output is TauREx-format ``.dat`` (wavelength, depth, error, bin_width) ready
    for ``SimulateTaurexRetrieval``.

    Example usage:
        BinSpectrum(spectrum_path='planet_fm_wavelength.npy', instrument='jwst_nirspec_prism')
        BinSpectrum(spectrum_path='planet_fm_wavelength.npy', resolving_power=50)
        BinSpectrum(spectrum_path='planet_fm_wavelength.npy', n_bins=30)
        BinSpectrum(instrument='list')
        # constant_error ONLY if the user explicitly asks for it:
        # BinSpectrum(..., constant_error=50e-6)
    """

    # --- Input spectrum ---
    # orchestral SchemaGenerator treats default=None as REQUIRED for the
    # LLM schema. Optional fields must therefore use non-None sentinels
    # (empty string / 0 / -1) and we normalize those to "unset" in _run.
    spectrum_path: str = RuntimeField(
        default='',
        description=(
            "Path to the input spectrum file (relative to base_directory). "
            "Supports TauREx .dat/.txt, Exo_Skryer .dat, 2-column .txt, "
            "or .npy file pairs (*_wavelength.npy + *_spectrum.npy, including "
            "TauREx FM *_fm_wavelength.npy). Not required when instrument='list'. "
            "Omit or leave empty when listing instruments."
        ),
    )

    # --- Binning target (set EXACTLY ONE; leave the other two unset/omitted) ---
    instrument: str = RuntimeField(
        default='',
        description=(
            "OPTIONAL — set ONLY this (leave resolving_power and n_bins unset) for "
            "instrument-grid binning. Instrument mode key, e.g. 'jwst_nirspec_prism', "
            "'hst_wfc3_g141', 'ariel_airs_ch0'. Use 'list' to see all available modes. "
            "Omit or leave empty when using resolving_power or n_bins instead."
        ),
    )
    resolving_power: float = RuntimeField(
        default=0.0,
        description=(
            "OPTIONAL — set ONLY this (leave instrument and n_bins unset) for custom-R "
            "binning. Target resolving power R (e.g. 100, 50, 1000). "
            "Omit or leave as 0 when using instrument or n_bins instead."
        ),
    )
    n_bins: int = RuntimeField(
        default=0,
        description=(
            "OPTIONAL — set ONLY this (leave instrument and resolving_power unset) for "
            "fixed bin-count binning. Number of log-spaced bins (>= 2). "
            "Omit or leave as 0 when using instrument or resolving_power instead."
        ),
    )

    # --- Optional grid limits (custom modes only; omit for instrument grids) ---
    wl_min: float = RuntimeField(
        default=-1.0,
        description=(
            "OPTIONAL. Minimum wavelength (μm) for custom grid (resolving_power / n_bins). "
            "Omit or leave as -1 to use the input spectrum minimum. Not used with instrument."
        ),
    )
    wl_max: float = RuntimeField(
        default=-1.0,
        description=(
            "OPTIONAL. Maximum wavelength (μm) for custom grid (resolving_power / n_bins). "
            "Omit or leave as -1 to use the input spectrum maximum. Not used with instrument."
        ),
    )

    # --- Method ---
    method: str = RuntimeField(
        default='custom',
        description=(
            "Binning backend: 'custom' (flux-conserving trapezoidal, no extra deps) "
            "or 'spectres' (uses the spectres package). Default: 'custom'."
        ),
    )

    # --- Optional constant error for error-free inputs (e.g. forward models) ---
    constant_error: float = RuntimeField(
        default=-1.0,
        description=(
            "OPTIONAL — ONLY set this if the user explicitly asks to add a constant "
            "uncertainty (e.g. 'add 50 ppm errors'). Do NOT invent or auto-add an error. "
            "Absolute units matching the spectrum (e.g. 50e-6 for 50 ppm transit depth). "
            "Ignored if the input already has errors. Omit or leave as -1 otherwise."
        ),
    )

    # --- Output ---
    output_filename: str = RuntimeField(
        default='binned_spectrum',
        description="Output subfolder name and file prefix. All outputs saved in '{output_filename}/' subfolder.",
    )

    base_directory: str = StateField()

    @staticmethod
    def _unset_str(value: str | None) -> str | None:
        """Treat empty / whitespace-only strings as unset."""
        if value is None:
            return None
        value = value.strip()
        return value if value else None

    @staticmethod
    def _unset_positive(value: float | int | None) -> float | int | None:
        """Treat <= 0 numeric sentinels as unset (resolving_power / n_bins)."""
        if value is None or value <= 0:
            return None
        return value

    @staticmethod
    def _unset_nonneg_or_sentinel(value: float | None, sentinel: float = -1.0) -> float | None:
        """Treat sentinel (default -1) as unset for wl_min/wl_max/constant_error."""
        if value is None or value == sentinel:
            return None
        return value

    def _normalized(self) -> dict:
        """Return canonical parameter values with sentinels mapped to None."""
        return {
            'spectrum_path': self._unset_str(self.spectrum_path),
            'instrument': self._unset_str(self.instrument),
            'resolving_power': self._unset_positive(self.resolving_power),
            'n_bins': int(v) if (v := self._unset_positive(self.n_bins)) is not None else None,
            'wl_min': self._unset_nonneg_or_sentinel(self.wl_min),
            'wl_max': self._unset_nonneg_or_sentinel(self.wl_max),
            'constant_error': self._unset_nonneg_or_sentinel(self.constant_error),
            'method': (self.method or 'custom').strip().lower(),
            'output_filename': self.output_filename or 'binned_spectrum',
        }

    def _run(self) -> str:
        """Execute the spectrum binning operation."""
        import numpy as np

        p = self._normalized()

        # ---- Handle instrument='list' ----
        if p['instrument'] and p['instrument'].lower() == 'list':
            return self._list_instruments()

        # ---- Validate inputs ----
        err = self._validate(p)
        if err:
            return err

        from .spectrum_binner import (
            load_spectrum,
            bin_spectrum,
            bin_to_resolving_power,
            bin_to_n_bins,
            save_binned_spectrum,
        )

        try:
            spec = load_spectrum(p['spectrum_path'], base_directory=self.base_directory)
        except Exception as e:
            return f"Error loading spectrum: {e}"

        old_wl = spec['wl']
        old_flux = spec['flux']
        old_errors = spec.get('error')
        detected_format = spec.get('format', 'unknown')

        # Attach constant error if requested and input has none
        constant_error_applied = False
        if old_errors is None and p['constant_error'] is not None:
            old_errors = np.full_like(old_flux, float(p['constant_error']))
            constant_error_applied = True

        # ---- Perform binning ----
        target_description = ''
        try:
            if p['instrument'] is not None:
                from .instrument_grids import get_instrument_grid
                new_wl, new_hbw, info = get_instrument_grid(p['instrument'])
                result = bin_spectrum(
                    old_wl, old_flux, new_wl, new_hbw,
                    old_errors=old_errors, method=p['method'],
                )
                target_description = f"Instrument: {info['description']}"

            elif p['resolving_power'] is not None:
                result = bin_to_resolving_power(
                    old_wl, old_flux, p['resolving_power'],
                    wl_min=p['wl_min'], wl_max=p['wl_max'],
                    old_errors=old_errors, method=p['method'],
                )
                target_description = f"Custom R={p['resolving_power']}"

            elif p['n_bins'] is not None:
                result = bin_to_n_bins(
                    old_wl, old_flux, p['n_bins'],
                    wl_min=p['wl_min'], wl_max=p['wl_max'],
                    old_errors=old_errors, method=p['method'],
                )
                target_description = f"Custom {p['n_bins']} bins"

        except Exception as e:
            return f"Error during binning: {e}"

        if len(result['wl']) == 0:
            return (
                "Error: All target bins were dropped (insufficient overlap with "
                "the input spectrum). Check wavelength ranges."
            )

        # ---- Flux-conservation diagnostic ----
        # Compare ∫ F_old dλ over the covered range to Σ (F_bin · Δλ_bin).
        overlap_lo = float(result['wl'][0] - result['half_bin_width'][0])
        overlap_hi = float(result['wl'][-1] + result['half_bin_width'][-1])
        overlap_lo = max(overlap_lo, float(old_wl.min()))
        overlap_hi = min(overlap_hi, float(old_wl.max()))
        mask_old = (old_wl >= overlap_lo) & (old_wl <= overlap_hi)
        flux_cons_pct = None
        if np.sum(mask_old) >= 2:
            _trap = getattr(np, 'trapezoid', None) or np.trapz
            integ_old = float(_trap(old_flux[mask_old], old_wl[mask_old]))
            integ_new = float(np.sum(result['flux'] * 2.0 * result['half_bin_width']))
            if abs(integ_old) > 0:
                flux_cons_pct = 100.0 * abs(integ_new - integ_old) / abs(integ_old)

        # Achieved median resolving power
        bin_widths = 2.0 * result['half_bin_width']
        with np.errstate(divide='ignore', invalid='ignore'):
            R_achieved = result['wl'] / bin_widths
        median_R = float(np.nanmedian(R_achieved)) if len(R_achieved) else float('nan')

        # ---- Save outputs ----
        output_filename = p['output_filename']
        output_dir = os.path.join(self.base_directory, output_filename)
        os.makedirs(output_dir, exist_ok=True)

        header_info = (
            f"Binned spectrum (TauREx format)\n"
            f"# Source: {p['spectrum_path']}\n"
            f"# Target: {target_description}\n"
            f"# Method: {p['method']}"
        )
        if constant_error_applied:
            header_info += f"\n# Constant error attached: {p['constant_error']}"

        dat_path = os.path.join(output_dir, f"{output_filename}_binned.dat")
        save_binned_spectrum(result, dat_path, header_info=header_info)

        np.save(os.path.join(output_dir, f"{output_filename}_wavelength.npy"), result['wl'])
        np.save(os.path.join(output_dir, f"{output_filename}_spectrum.npy"), result['flux'])

        plot_path = os.path.join(output_dir, f"{output_filename}_comparison.png")
        self._plot_comparison(old_wl, old_flux, result, target_description, plot_path)

        # ---- Build summary ----
        rel_dir = os.path.relpath(output_dir, self.base_directory)
        n_input = len(old_wl)
        n_output = len(result['wl'])
        wl_range = f"{result['wl'].min():.4f}–{result['wl'].max():.4f}"

        summary = (
            f"Spectrum binned successfully!\n\n"
            f"Input: {p['spectrum_path']} ({n_input} points, format: {detected_format})\n"
            f"Target: {target_description}\n"
            f"Method: {p['method']}\n"
            f"Output: {n_output} bins, λ range: {wl_range} μm, median R ≈ {median_R:.1f}\n"
        )

        if flux_cons_pct is not None:
            summary += f"Flux conservation: |Δ∫F dλ| / ∫F = {flux_cons_pct:.4f}%\n"

        # Warnings
        warnings = []
        if spec.get('n_dropped_nan', 0) > 0:
            warnings.append(f"dropped {spec['n_dropped_nan']} non-finite input rows")
        if spec.get('n_deduped', 0) > 0:
            warnings.append(f"deduplicated {spec['n_deduped']} identical wavelengths")
        if not spec.get('was_sorted', True):
            warnings.append("input wavelengths were re-sorted ascending")
        if result.get('n_partial', 0) > 0:
            warnings.append(f"{result['n_partial']} partially covered edge bins (normalized by covered width)")
        if result.get('n_empty', 0) > 0:
            warnings.append(f"{result['n_empty']} empty bins filled by interpolation")
        if result.get('n_dropped', 0) > 0:
            warnings.append(f"{result['n_dropped']} bins dropped (coverage < 50%)")
        if warnings:
            summary += "Warnings: " + "; ".join(warnings) + "\n"

        summary += (
            f"\nOutput folder: {rel_dir}/\n"
            f"Output files:\n"
            f"  - {rel_dir}/{output_filename}_comparison.png (plot)\n"
            f"  - {rel_dir}/{output_filename}_binned.dat "
            f"(TauREx format: wl, depth, error, bin_width — retrieval-ready)\n"
            f"  - {rel_dir}/{output_filename}_wavelength.npy ({n_output} points)\n"
            f"  - {rel_dir}/{output_filename}_spectrum.npy\n"
        )

        if result.get('error') is not None:
            if constant_error_applied:
                summary += (
                    f"  - Errors: constant_error={p['constant_error']} attached to input, "
                    f"then propagated\n"
                )
            else:
                summary += "  - Errors propagated: yes (weighted quadrature)\n"
        else:
            summary += (
                "  - Errors propagated: no (input had no errors). "
                "No constant_error was requested.\n"
            )

        return summary

    def _validate(self, p: dict | None = None) -> str | None:
        """Return an error message string, or None if inputs are valid."""
        if p is None:
            p = self._normalized()

        if not p['spectrum_path']:
            return (
                "Error: spectrum_path is required. Provide a path to the input spectrum file.\n"
                "Supported formats: TauREx .dat/.txt, Exo_Skryer .dat, 2-column .txt, "
                "or .npy file pairs (*_wavelength.npy + *_spectrum.npy)."
            )

        targets_set = sum([
            p['instrument'] is not None,
            p['resolving_power'] is not None,
            p['n_bins'] is not None,
        ])
        if targets_set == 0:
            return (
                "Error: No binning target specified. Set EXACTLY ONE of:\n"
                "  - instrument='jwst_nirspec_prism' (leave resolving_power and n_bins unset)\n"
                "  - resolving_power=100 (leave instrument and n_bins unset)\n"
                "  - n_bins=50 (leave instrument and resolving_power unset)\n\n"
                "Use instrument='list' to see all available instrument modes."
            )
        if targets_set > 1:
            return (
                "Error: Multiple binning targets specified. "
                "Please set only ONE of: instrument, resolving_power, or n_bins "
                "(omit / leave the others at their defaults)."
            )

        if p['method'] not in ('custom', 'spectres'):
            return f"Error: Unknown method '{p['method']}'. Use 'custom' or 'spectres'."

        if p['resolving_power'] is not None and p['resolving_power'] <= 0:
            return f"Error: resolving_power must be positive, got {p['resolving_power']}."

        if p['n_bins'] is not None and p['n_bins'] < 2:
            return f"Error: n_bins must be >= 2, got {p['n_bins']}."

        if p['wl_min'] is not None and p['wl_max'] is not None and p['wl_min'] >= p['wl_max']:
            return f"Error: wl_min ({p['wl_min']}) must be < wl_max ({p['wl_max']})."

        if p['constant_error'] is not None and p['constant_error'] < 0:
            return f"Error: constant_error must be >= 0, got {p['constant_error']}."

        if p['instrument'] is not None:
            from .instrument_grids import INSTRUMENT_MODES
            key = p['instrument'].strip().lower()
            if key not in INSTRUMENT_MODES:
                suggestions = difflib.get_close_matches(
                    key, INSTRUMENT_MODES.keys(), n=5, cutoff=0.4,
                )
                msg = f"Error: Unknown instrument mode '{p['instrument']}'."
                if suggestions:
                    msg += f"\nDid you mean: {', '.join(suggestions)}?"
                msg += "\nUse instrument='list' to see all available modes."
                return msg

        return None

    def _list_instruments(self) -> str:
        """Return a formatted table of all available instrument modes."""
        from .instrument_grids import list_instruments

        instruments = list_instruments()

        lines = [
            "Available Instrument Modes for Spectrum Binning",
            "=" * 50,
            "",
            f"{'Key':<25} {'Telescope':<10} {'Instrument':<12} {'Mode':<18} {'λ range (μm)':<16} {'R':>8}  {'Bins':>5}",
            f"{'-'*25} {'-'*10} {'-'*12} {'-'*18} {'-'*16} {'-'*8}  {'-'*5}",
        ]

        for inst in instruments:
            wl_range = f"{inst['wl_min']:.2f}–{inst['wl_max']:.2f}"
            lines.append(
                f"{inst['key']:<25} {inst['telescope']:<10} {inst['instrument']:<12} "
                f"{inst['mode']:<18} {wl_range:<16} {inst['R']:>8}  {inst['n_bins']:>5}"
            )

        lines.append("")
        lines.append("Usage: BinSpectrum(spectrum_path='...', instrument='<key>')")
        lines.append(
            "Example: BinSpectrum(spectrum_path='planet_fm_wavelength.npy', "
            "instrument='jwst_nirspec_prism')"
        )

        return '\n'.join(lines)

    @staticmethod
    def _plot_comparison(old_wl, old_flux, result, target_description, plot_path):
        """Generate a comparison plot: spectrum + residuals."""
        import numpy as np
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        fig, ax1 = plt.subplots(1, 1, figsize=(13, 7), sharex=True)

        use_log = (result['wl'].max() / max(result['wl'].min(), 1e-30)) > 3

        # Main comparison
        ax1.plot(old_wl, old_flux, color='#888888', alpha=0.5, linewidth=0.5, label='Original (high-res)')
        ax1.errorbar(
            result['wl'], result['flux'],
            xerr=result['half_bin_width'],
            yerr=result.get('error'),
            fmt='o', markersize=3, capsize=2,
            color='#e63946', ecolor='#e63946', alpha=0.8,
            label=f'Binned ({len(result["wl"])} bins)',
        )
        ax1.set_ylabel('Transit Depth / Flux')
        ax1.set_title(f'Spectrum Binning: {target_description}')
        if use_log:
            ax1.set_xscale('log')
        ax1.legend(fontsize=9)
        ax1.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(plot_path, dpi=200, bbox_inches='tight')
        plt.close(fig)