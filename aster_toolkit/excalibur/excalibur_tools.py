"""
Excalibur (JPL exoplanet pipeline) tools: target lookup, Cerberus cross sections, Cerberus forward models(equilibrium and free chemistry), simulated Ariel observations, and LETHE transit light curves.

Excalibur needs Python 3.12 and its own dependencies, so these tools run `excalibur_run.py` with the Excalibur environment's Python (excalibur_python) instead of importing Excalibur here.

Setup (once):
    excalibur_python        = Python of the Excalibur env (numpy 2, recent dawgie)    (EXCALIBUR_PYTHON)
    excalibur_data_dir      = folder with CERBERUS/, ariel/, pickles/               (DATA_BASEDIR)
    excalibur_esp_directory = esp checkout to use instead of the installed one       (EXCALIBUR_ESP_DIR)
LETHE (optional overrides, default to the values above): lethe_python, lethe_esp_directory, lethe_directory (LETHE_PYTHON, LETHE_ESP_DIR, LETHE_DIR)
"""
import json
import os
import re
import subprocess
from pathlib import Path

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField


EXCALIBUR_RUN = Path(__file__).with_name("excalibur_run.py")
RESULT_PREFIX = "ASTER_EXCALIBUR_RESULT:"

def _run_excalibur(excalibur_python: str, command: str, arguments: dict, timeout: int) -> dict:
    """Run one excalibur_run.py command in the Excalibur environment and return its result dict."""
    python = os.path.expanduser(excalibur_python or os.getenv("EXCALIBUR_PYTHON", ""))
    if not python or not Path(python).is_file():
        raise RuntimeError(
            "excalibur_python is not set or does not exist. Set it to the Python of the environment where Excalibur runs (Python 3.12), e.g. /opt/anaconda3/envs/lethe/bin/python."
        )
    try:
        completed = subprocess.run(
            [python, str(EXCALIBUR_RUN), command, json.dumps(arguments)], capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Excalibur '{command}' timed out after {timeout // 60} minutes.")

    lines = [line for line in completed.stdout.splitlines() if line.startswith(RESULT_PREFIX)]
    if not lines:
        tail = "\n".join((completed.stderr or completed.stdout).strip().splitlines()[-8:])
        raise RuntimeError(f"excalibur_run.py crashed (exit {completed.returncode}):\n{tail}")
    payload = json.loads(lines[-1][len(RESULT_PREFIX):])
    if not payload["ok"]:
        raise RuntimeError(payload["error"])
    return payload["result"]


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip()).strip("_") or "run"

def _as_dict(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


class _ExcaliburTool(BaseTool):
    """Shared Excalibur environment settings."""

    excalibur_python: str = StateField(default="", description="Python of the Excalibur (3.12) environment.")
    excalibur_data_dir: str = StateField(default="", description="Excalibur DATA_BASEDIR (CERBERUS/, ariel/, pickles/).")
    excalibur_esp_directory: str = StateField(default="", description="esp checkout to use. Empty = the installed Excalibur.")

    def _environment(self) -> dict:
        return {
            "data_dir": os.path.expanduser(self.excalibur_data_dir or os.getenv("DATA_BASEDIR", "")),
            "esp_dir": os.path.expanduser(self.excalibur_esp_directory or os.getenv("EXCALIBUR_ESP_DIR", "")),
        }

    def _excalibur(self, command: str, arguments: dict, timeout: int, python: str = "") -> dict:
        return _run_excalibur(python or self.excalibur_python, command, {**self._environment(), **arguments}, timeout)


class FindExcaliburTarget(_ExcaliburTool):
    """Check whether a planet is in the Excalibur target list (~2000 systems, from the Excalibur system pickles) and return its star and planet parameters with units and literature references, Rp/R*, and whether Ariel noise (ArielRad) is available for it, with its Ariel tiers.

    Matching ignores case, spaces and hyphens ("k2-12b" finds "K2-12 b"). If the planet is not found, the result lists the planets of that star or close star names, so you can ask the user which one they meant. Radius/mass are in Jupiter units, as the Cerberus tools use them.
    """

    planet_name: str | None = RuntimeField(description="REQUIRED. Planet name, e.g. 'K2-12 b', 'HD 189733 b', 'WASP-39b'.")

    def _run(self):
        if not self.planet_name:
            raise ValueError("planet_name is required.")
        return json.dumps(self._excalibur("find_target", {"name": self.planet_name}, timeout=300), indent=2)


class CalculateExcaliburCrossSections(_ExcaliburTool):
    """Compute the Cerberus (Excalibur) molecular + CIA cross-section library on a wavelength grid, used by the Cerberus forward-model tools. This is slow (about 2.5 minutes), so it is done once per wavelength grid and reused by name: if `name` already exists it is reused instantly unless overwrite=True. The Cerberus model tools compute the 'ariel' library automatically if it is missing.

    Wavelength grid options:
      - 'ariel' (default): the 103 ArielRad instrument channels. Use this for SimulateArielObservation.
      - 'min,max,n': a uniform grid in microns, e.g. '0.55,7.6,1200'.
      - a file path (.txt or .npy; first column = wavelength in microns), relative to the workspace.

    Atoms (Na, K, Ca) are not in the library: they come from Excalibur's ATOM_XSEC grids at model time.
    Saved to workspace/excalibur/cross_sections/<name>.pkl (+ .pkl.json with the grid and species).
    """

    name: str = RuntimeField(default="ariel", description="Name for this cross-section library (default 'ariel').")
    wavelength_grid: str = RuntimeField(default="ariel", description="'ariel', 'min,max,n' (microns), or a file path.")
    molecules: list = RuntimeField(
        default=[],
        description="Optional EXOMOL molecule list. Default: TIO, H2O, H2CO, HCN, CO, CO2, NH3, CH4, H2S, SO2, PH3 (HITEMP NO, OH, C2H2, N2, N2O, O2 and CIA are always included).",
    )
    overwrite: bool = RuntimeField(default=False, description="Recompute even if a library with this name exists.")

    base_directory: str = StateField(description="ASTER workspace directory.")

    def _run(self):
        grid = self.wavelength_grid or "ariel"
        if grid.lower() != "ariel" and not re.fullmatch(r"[\d.eE+\-\s]+,[\d.eE+\-\s]+,\s*\d+\s*", grid):
            grid = str(Path(self.base_directory) / grid) if not os.path.isabs(os.path.expanduser(grid)) else grid
        out_path = Path(self.base_directory) / "excalibur" / "cross_sections" / f"{_safe_name(self.name or 'ariel')}.pkl"
        result = self._excalibur(
            "cross_sections", {"wavelength_grid": grid, "molecules": self.molecules or None, "out_path": str(out_path.resolve()), "overwrite": bool(self.overwrite)}, timeout=3600,
        )
        status = "Reused existing" if result["reused"] else "Computed"
        return f"{status} cross-section library '{self.name}':\n" + json.dumps(result, indent=2)


class _CerberusModelTool(_ExcaliburTool):
    """Shared planet, cloud and output settings of the Cerberus forward models."""

    run_name: str | None = RuntimeField(description="REQUIRED. Folder name for this model, e.g. 'TOI-270b_eq_10xsolar'. Reuse it in SimulateArielObservation.")
    target: str = RuntimeField(default="", description="Planet from the Excalibur target list to take parameters from, e.g. 'TOI-270 b'. Empty = give all parameters explicitly.")
    cross_sections: str = RuntimeField(default="ariel", description="Cross-section library name (default 'ariel', computed automatically if missing).")
    teq_K: float = RuntimeField(default=0.0, description="Isothermal atmosphere temperature (K). 0 = use the database Teq.")
    rp_rjup: float = RuntimeField(default=0.0, description="Planet radius (Jupiter radii). 0 = use the database value.")
    mp_mjup: float = RuntimeField(default=0.0, description="Planet mass (Jupiter masses). 0 = use the database value.")
    rstar_rsun: float = RuntimeField(default=0.0, description="Stellar radius (solar radii). 0 = use the database value.")
    tstar_K: float = RuntimeField(default=0.0, description="Stellar effective temperature (K). 0 = use the database value.")
    sma_au: float = RuntimeField(default=0.0, description="Semi-major axis (AU). 0 = use the database value.")
    cloud_top_log10_bar: float = RuntimeField(default=3.0, description="log10 of cloud-top pressure in bar (3.0 = no clouds).")
    haze_log10_scale: float = RuntimeField(default=-10.0, description="log10 haze scale vs Jupiter (-10 = no haze, 0 = Jupiter-like).")

    base_directory: str = StateField(description="ASTER workspace directory.")

    def _model(self, command: str, chemistry: dict) -> str:
        if not self.run_name:
            raise ValueError("run_name is required.")
        workspace = Path(self.base_directory)
        xs_name = _safe_name(self.cross_sections or "ariel")
        xs_path = workspace / "excalibur" / "cross_sections" / f"{xs_name}.pkl"
        if xs_name == "ariel":
            # computes the library once (~2.5 min), then reuses it
            self._excalibur("cross_sections", {"wavelength_grid": "ariel", "molecules": None,"out_path": str(xs_path.resolve()), "overwrite": False}, timeout=3600)
        out_dir = workspace / "excalibur" / "forward_models" / _safe_name(self.run_name)
        args = {
            "cross_section_file": str(xs_path.resolve()),
            "cache_dir": str((workspace / "excalibur" / "cache").resolve()),
            "out_dir": str(out_dir.resolve()),
            "target": self.target or None,
            # 0 means "not given": fall back to the database value
            "teq_K": self.teq_K or None, "rp_rjup": self.rp_rjup or None, "mp_mjup": self.mp_mjup or None,
            "rstar_rsun": self.rstar_rsun or None, "tstar_K": self.tstar_K or None, "sma_au": self.sma_au or None,
            "cloud_top_log10_bar": self.cloud_top_log10_bar, "haze_log10_scale": self.haze_log10_scale,
            **chemistry,
        }
        result = self._excalibur(command, args, timeout=1800)
        rel = os.path.relpath(out_dir, workspace)
        return (
            f"Forward model saved in {rel}/ (spectrum.csv, mixing_ratios.csv, spectrum.png, parameters.json). For a simulated Ariel observation and its plot, call SimulateArielObservation(run_name='{self.run_name}').\n"
            + json.dumps(result, indent=2)
        )


class RunCerberusEquilibriumModel(_CerberusModelTool):
    """Run a Cerberus (Excalibur) transmission forward model with thermochemical-equilibrium chemistry and save the spectrum.

    Chemistry comes from metallicity_dex ([M/H] relative to solar) and c_to_o (linear C/O, solar 0.55):
      - 'TEA' (default): Excalibur's interpolated TEA equilibrium grid (CERBERUS/INTERP_TEA; pressure-dependent abundances of ~20 species). Grid limits: metallicity -2 to +4 dex, log10(C/O / solar) -2 to +2, T 100-10000 K. The first run in a session builds the grid (~1.5 min, then cached).
      - 'TEC': the older analytic equilibrium (faster, fewer species).
    If the user gives no metallicity, use solar (0) unless they ask for something else.

    Planet/star parameters come from the Excalibur target list when `target` is given (see FindExcaliburTarget); any value passed explicitly overrides it. Without a target, give all of teq_K, rp_rjup, mp_mjup, rstar_rsun, tstar_K and sma_au. Isothermal at Teq. Clouds default to none.

    Output in workspace/excalibur/forward_models/<run_name>/: spectrum.csv (wavelength_um, transit_depth as (Rp/R*)^2 fraction, contribution_<species> columns), mixing_ratios.csv (log10 VMR vs pressure), spectrum.png, parameters.json.

    A 'Cerberus simulation' (or 'Ariel simulation') request means BOTH steps: a Cerberus forward model (RunCerberusEquilibriumModel by default, RunCerberusFreeChemistryModel if the user gives abundances) followed by SimulateArielObservation on the same run_name, then show its plot.
    """

    metallicity_dex: float = RuntimeField(default=0.0, description="[M/H] in dex relative to solar (0 = solar, 1 = 10x solar).")
    c_to_o: float = RuntimeField(default=0.54951, description="C/O ratio, linear (solar ~0.55).")
    equilibrium_model: str = RuntimeField(default="TEA", description="'TEA' (interpolated TEA grid, default) or 'TEC' (older analytic).")

    def _run(self):
        return self._model("equilibrium_model", {"metallicity_dex": self.metallicity_dex, "c_to_o": self.c_to_o, "equilibrium_model": self.equilibrium_model or "TEA",})


class RunCerberusFreeChemistryModel(_CerberusModelTool):
    """Run a Cerberus (Excalibur) transmission forward model with free chemistry (constant-with-altitude abundances you choose) and save the spectrum.

    log10_vmr is the log10 VOLUME MIXING RATIO per species, e.g. {"H2O": -3, "CH4": -5, "CO2": -6, "Na": -6} (-3 = 0.1%). The H2/He background fills the rest. Use only abundances the user gives, never invent them (if none are given, use RunCerberusEquilibriumModel instead). Species: TIO, H2O, H2CO, HCN, CO, CO2, NH3, CH4, H2S, SO2, PH3 (EXOMOL), NO, OH, C2H2, N2, N2O, O2 (HITEMP), and the atoms Na, K, Ca (Excalibur ATOM_XSEC grids, valid for 100-3000 K).

    Planet/star parameters come from the Excalibur target list when `target` is given (see FindExcaliburTarget) any value passed explicitly overrides it. Without a target, give all of teq_K, rp_rjup, mp_mjup, rstar_rsun, tstar_K and sma_au. Isothermal at Teq. Clouds default to none.

    Output in workspace/excalibur/forward_models/<run_name>/: spectrum.csv, mixing_ratios.csv, spectrum.png, parameters.json.

    A 'Cerberus simulation' (or 'Ariel simulation') request means BOTH steps: a Cerberus forward model (RunCerberusEquilibriumModel by default, RunCerberusFreeChemistryModel if the user gives abundances) followed by SimulateArielObservation on the same run_name, then show its plot.
    """

    log10_vmr: dict = RuntimeField(default={}, description="REQUIRED. log10 volume mixing ratios, e.g. {'H2O': -3, 'CH4': -5, 'Na': -6}.")

    def _run(self):
        return self._model("free_model", {"log10_vmr": _as_dict(self.log10_vmr)})


class SimulateArielObservation(_ExcaliburTool):
    """Simulate an Ariel observation of a Cerberus forward model: the model is binned to the Ariel channels and Gaussian noise from the ArielRad noise model is added (single-transit noise / sqrt(number of transits for the tier)). Makes the standard plot: forward model line + simulated Ariel points with error bars, transit depth in ppm vs wavelength.

    Run RunCerberusEquilibriumModel or RunCerberusFreeChemistryModel first and pass the same run_name. The planet must be in ArielRad (FindExcaliburTarget tells whether noise is available)the target is taken from the forward model unless given. Use the 'ariel' cross-section grid (the default).

    Output in workspace/excalibur/simulations/<run_name>_tier<N>/:
      - ariel_tier<N>.png: the plot to show the user.
      - observation.csv: wavelength_um, channel edges, model_transit_depth, observed_transit_depth, uncertainty.
      - spectrum.dat: 4-column spectrum (wavelength, depth, error, bin width) usable as observation_path in SimulateTaurexRetrieval.
      - simulation.json: tier, number of transits, seed, noise summary.

    A 'Cerberus simulation' (or 'Ariel simulation') request means BOTH steps: a Cerberus forward model (RunCerberusEquilibriumModel by default, RunCerberusFreeChemistryModel if the user gives abundances) followed by SimulateArielObservation on the same run_name, then show its plot.
    """

    run_name: str | None = RuntimeField(description="REQUIRED. run_name of the Cerberus forward model to observe.")
    tier: int = RuntimeField(default=2, description="Ariel tier (1, 2 or 3): sets the number of transits / noise level.")
    target: str = RuntimeField(default="", description="Planet for the noise model. Empty = the forward model's target.")
    n_transits: int = RuntimeField(default=0, description="Number of transits. 0 = ArielRad's number for the tier.")
    seed: int = RuntimeField(default=1234567, description="Random seed for the simulated noise.")

    base_directory: str = StateField(description="ASTER workspace directory.")

    def _run(self):
        if not self.run_name:
            raise ValueError("run_name is required.")
        workspace = Path(self.base_directory)
        fm_dir = workspace / "excalibur" / "forward_models" / _safe_name(self.run_name)
        out_dir = workspace / "excalibur" / "simulations" / f"{_safe_name(self.run_name)}_tier{self.tier}"
        result = self._excalibur("ariel_simulation", {
            "forward_model_dir": str(fm_dir.resolve()), "out_dir": str(out_dir.resolve()),"tier": self.tier, "target": self.target or None, "n_transits": self.n_transits or None,"seed": self.seed,
        }, timeout=600)
        rel = os.path.relpath(out_dir, workspace)
        return (
            f"Simulated Ariel Tier {self.tier} observation saved in {rel}/. Show the plot {rel}/ariel_tier{self.tier}.png.\n" + json.dumps(result, indent=2)
        )


class ComputeLetheLightCurve(_ExcaliburTool):
    """Compute a transit light curve with Excalibur's LETHE model (C. Bernardin): the flux occulted by the planet is interpolated from precomputed grids instead of integrated numerically, for an 8-coefficient limb-darkening law (S. Grusnis), via excalibur.transit.core.tldlc.

    Limb darkening, I(mu)/I(1) = 1 - sum_k g_k (1 - mu^a_k) - sum_k g_(k+4) mu^k ln(mu):
      - g1..g4: Claret 4-parameter non-linear law (powers a_k = 0.5, 1, 1.5, 2).
      - g5..g8: extended terms mu^k ln(mu), k = 1..4.
    Give 0 to 8 coefficients in that order; missing ones are 0 (4 values = plain Claret law, none = uniform disk). Never invent coefficients: use values from the user.

    Geometry, one of:
      - separation (default): z_grid 'min,max,n' in stellar radii, e.g. '-1.5,1.5,601'.
      - time: give period_days, sma_rstar (a/R*) and inclination_deg (optionally ecc, tmid_days and
        time_grid 'start,end,n' in days; default tmid +/- T14). Converted to z with Excalibur's time2z.

    Rp/R* must be within the LETHE grid (0 < rprs <= 0.2). compare_numerical=True also computes Excalibur's numerical-integration model as a check (difference reported in ppm).

    Output in workspace/excalibur/light_curves/<run_name>/:
      - light_curve.csv: [time_days, phase,] z_rstar, flux_lethe [, flux_numerical].
      - parameters.json: every input and a summary (transit depth in ppm vs (Rp/R*)^2).
    """

    run_name: str | None = RuntimeField(description="REQUIRED. Folder name for this light curve, e.g. 'WASP-39b_lethe'.")
    rprs: float | None = RuntimeField(description="REQUIRED. Planet-to-star radius ratio Rp/R* (0 < rprs <= 0.2). This is a RATIO, NOT the planet radius by itself, e.g. for a 1.2 Rjup planet orbiting a 1.0 Rsun star, rprs = 1.2 * 0.10045 = 0.1205. If unsure, give `target` below so this can be checked against the database.")
    target: str = RuntimeField(default="", description="Optional database planet name (e.g. 'K2-12 b') used to sanity-check rprs against the database Rp/R*. Strongly recommended whenever the target is in the database, catches passing the planet radius instead of the Rp/R* ratio.")
    limb_darkening: list = RuntimeField(default=[], description="Up to 8 limb-darkening coefficients [g1, ..., g8] (see tool description).")
    z_grid: str = RuntimeField(default="-1.5,1.5,601", description="Separation grid 'min,max,n' in stellar radii (ignored in time mode).")
    period_days: float = RuntimeField(default=0.0, description="Orbital period (days). >0 switches to the time mode.")
    sma_rstar: float = RuntimeField(default=0.0, description="Time mode: semi-major axis in stellar radii (a/R*).")
    inclination_deg: float = RuntimeField(default=90.0, description="Time mode: orbital inclination (degrees).")
    ecc: float = RuntimeField(default=0.0, description="Time mode: eccentricity.")
    tmid_days: float = RuntimeField(default=0.0, description="Time mode: mid-transit time (days).")
    time_grid: str = RuntimeField(default="", description="Time mode: 'start,end,n' in days. Empty = tmid +/- T14, 1000 points.")
    compare_numerical: bool = RuntimeField(default=True, description="Also compute the numerical-integration model as a check.")

    base_directory: str = StateField(description="ASTER workspace directory.")
    lethe_python: str = StateField(default="", description="Python for LETHE (needs numpy >= 2). Empty = excalibur_python.")
    lethe_esp_directory: str = StateField(default="", description="esp checkout with LETHE. Empty = excalibur_esp_directory.")
    lethe_directory: str = StateField(default="", description="LETHE grids (grid_F/, grid_G/, parameters/). Empty = <DATA_BASEDIR>/LETHE.")

    def _run(self):
        if not self.run_name:
            raise ValueError("run_name is required.")
        if not self.rprs:
            raise ValueError("rprs is required.")

        if self.target:
            found = self._excalibur("find_target", {"name": self.target}, timeout=300)
            if found.get("found"):
                derived_rprs = found["target"]["planet"].get("rp_over_rstar")
                if derived_rprs and not (0.5 * derived_rprs <= self.rprs <= 2.0 * derived_rprs):
                    p, s = found["target"]["planet"], found["target"]["star"]
                    raise ValueError(
                        f"rprs={self.rprs} looks wrong for target '{self.target}': the database gives Rp={p['radius_rjup']:.4f} Rjup, R*={s['radius_rsun']:.4f} Rsun, so Rp/R* should be ~{derived_rprs:.4f}. Did you pass the planet radius instead of the Rp/R* ratio? You dumb dumb."
                    )

        coefficients = self.limb_darkening
        if isinstance(coefficients, str):
            coefficients = json.loads(coefficients)
        workspace = Path(self.base_directory)
        out_dir = workspace / "excalibur" / "light_curves" / _safe_name(self.run_name)
        args = {
            **self._environment(),
            "lethe_dir": os.path.expanduser(self.lethe_directory or os.getenv("LETHE_DIR", "")),
            "out_dir": str(out_dir.resolve()),
            "rprs": self.rprs, "limb_darkening": coefficients, "z_grid": self.z_grid,
            # 0 means "not given": separation mode
            "period_days": self.period_days or None, "sma_rstar": self.sma_rstar or None,
            "inclination_deg": self.inclination_deg, "ecc": self.ecc, "tmid_days": self.tmid_days,
            "time_grid": self.time_grid or None, "compare_numerical": bool(self.compare_numerical),
        }
        lethe_esp = self.lethe_esp_directory or os.getenv("LETHE_ESP_DIR", "")
        if lethe_esp:
            args["esp_dir"] = os.path.expanduser(lethe_esp)
        python = self.lethe_python or os.getenv("LETHE_PYTHON", "") or self.excalibur_python
        result = _run_excalibur(python, "lethe_light_curve", args, timeout=600)
        rel = os.path.relpath(out_dir, workspace)
        return (
            f"LETHE light curve saved in {rel}/ (light_curve.csv for plotting, parameters.json for inputs).\n"+ json.dumps(result, indent=2)
        )
