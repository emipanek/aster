import numpy as np
import taurex
from taurex.cache import GlobalCache, OpacityCache, CIACache
from taurex.model import TransmissionModel
from taurex.data.profiles.temperature import Isothermal
from taurex.planet import Planet
from taurex.stellar import BlackbodyStar
from taurex.chemistry import TaurexChemistry
from taurex.chemistry import ConstantGas
from taurex.contributions import AbsorptionContribution, RayleighContribution, CIAContribution
import matplotlib.pyplot as plt
from taurex.data.spectrum.observed import ObservedSpectrum
from taurex.optimizer.nestle import NestleOptimizer
from taurex.optimizer.multinest import MultiNestOptimizer
import os
import sys
from contextlib import redirect_stdout
# import requests
# from astropy.io import ascii
import pandas as pd
# import csv
# import io
# import json
from tqdm import tqdm
import corner
import ast

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

class SimulateTaurexRetrieval(BaseTool):
    """
    Run a TauREx atmospheric retrieval to fit model parameters to an observed transmission spectrum.

    **BEFORE using this tool, you MUST**:
    1. Read the skill file: ReadFileTool(file_path="skills/retrieval_best_practices.md")
    2. Set TauREx paths with SetTaurexPaths (using ls/pwd to get correct paths)
    3. Have observation data ready (from DownloadDataset or user-provided)

    **REQUIRED PARAMETER**: observation_path
    - Use the exact path from DownloadDataset output (e.g., 'spectra/WASP_39_b_3/WASP_39_b_3.11466_5502_2/spectrum.dat')
    - Or ask user for their file path

    **IMPORTANT - Optimizer Selection**:
    - Use optimizer="nestle" (recommended, always works, pure Python)
    - Do NOT use optimizer="multinest" unless confirmed it's installed (requires compiled Fortran libraries)
    - Read skills/retrieval_best_practices.md for detailed guidance

    **Observation file format**: 3-4 column text file:
    - Column 1: wavelength in microns
    - Column 2: transit depth (dimensionless, e.g., 0.02 for 2%)
    - Column 3: error on transit depth
    - Column 4 (optional): wavelength bin width
    """

    # Required parameters
    observation_path: str | None = RuntimeField(
        default=None,
        description="Path to the observed spectrum file (relative to base_directory). REQUIRED - you must provide this path."
    )
    fit_params: list | str | None = RuntimeField(
        default=None,
        description="List of parameters to fit during retrieval. Can be a Python list or a string representation of a list. Example: ['planet_radius', 'T', 'H2O', 'CH4', 'CO2', 'CO', 'NH3'] or \"['planet_radius', 'T', 'H2O', 'CH4']\". REQUIRED - you must provide fit parameters."
    )
    bounds: dict | str | None = RuntimeField(
        default=None,
        description="Dictionary specifying bounds for each fit parameter with [low, high]. Can be a Python dict or string representation. Example: {'planet_radius': [0.5, 2.0], 'T': [1000, 2000], 'H2O': [1e-9, 1e-2]} or \"{'planet_radius': [0.5, 2.0], 'T': [1000, 2000]}\". If not provided, reasonable defaults will be used based on fit_params."
    )

    # Optional parameters with defaults
    optimizer: str = RuntimeField(
        default="nestle",
        description="Optimizer to use for retrieval ('nestle' or 'multinest'). Use 'multinest' for better sampling if not specified."
    )
    num_live_points: int = RuntimeField(
        default=50,
        description="Number of live points for nested sampling. Lower values (10-25) are faster but less accurate. Higher values (50-100) are slower but more accurate. Default: 50 for production, use 10-25 for quick tests."
    )
    star_radius: float = RuntimeField(
        default=1.0,
        description="Radius of the host star in Solar radii."
    )
    planet_radius: float = RuntimeField(
        default=1.0,
        description="Radius of the planet in Jupiter radii."
    )
    planet_mass: float = RuntimeField(
        default=1.0,
        description="Mass of the planet in Jupiter masses."
    )
    planet_temp: float = RuntimeField(
        default=1500.0,
        description="Temperature of the planet in Kelvin."
    )
    atm_min_pressure: float = RuntimeField(
        default=1e-3,
        description="Minimum atmospheric pressure in Pa. IMPORTANT: TauREx works in Pa, NOT bars! Standard value is 1e-3 Pa."
    )
    atm_max_pressure: float = RuntimeField(
        default=1e5,
        description="Maximum atmospheric pressure in Pa. Standard value is 1e5 Pa."
    )
    nlayers: int = RuntimeField(
        default=100,
        description="Number of atmospheric layers to model. Standard value is 100."
    )
    free_chem_full_molecules: list | None = RuntimeField(
        default=None,
        description="List of molecules with mixing ratios for 'full' mode (e.g., [['H2O', 0.02], ['CH4', 0.001]]). Only used in 'full' retrieval mode. If not specified in 'full' mode, uses default molecules."
    )
    retrieval_mode: str = RuntimeField(
        default="reduced",
        description="Retrieval mode: 'reduced' (fit 5 molecules: H2O, CH4, CO2, CO, NH3), 'full' (fit custom molecule list), or 'equilibrium' (fit metallicity and C/O ratio)."
    )
    output_basename: str = RuntimeField(
        default="retrieval_output",
        description="Base name for output files generated by the retrieval."
    )

    # State field - agent doesn't see this
    base_directory: str = StateField()

    def _generate_default_bounds(self, fit_params: list) -> dict:
        """
        Generate reasonable default bounds for fit parameters.

        Args:
            fit_params: List of parameter names to fit

        Returns:
            Dictionary of bounds for each parameter
        """
        default_bounds = {
            # Physical parameters
            'planet_radius': [0.5, 2.5],      # Jupiter radii
            'planet_mass': [0.1, 5.0],        # Jupiter masses
            'T': [500, 3000],                  # Temperature in Kelvin
            'star_radius': [0.5, 2.0],        # Solar radii

            # Molecular abundances (log-space)
            'H2O': [1e-9, 1e-2],
            'CH4': [1e-9, 1e-2],
            'CO2': [1e-9, 1e-2],
            'CO': [1e-9, 1e-2],
            'NH3': [1e-9, 1e-2],
            'N2': [1e-9, 1e-2],
            'O2': [1e-9, 1e-2],
            'HCN': [1e-9, 1e-2],
            'H2S': [1e-9, 1e-2],

            # Equilibrium chemistry parameters
            'metallicity': [0.1, 10.0],       # Solar metallicity
            'c_o_ratio': [0.1, 2.0],          # C/O ratio
        }

        # Build bounds dict for requested parameters
        bounds = {}
        for param in fit_params:
            if param in default_bounds:
                bounds[param] = default_bounds[param]
            else:
                raise ValueError(f"Unknown parameter '{param}' - cannot generate default bounds. Please provide bounds manually.")

        return bounds

    def _run(self) -> str:
        """Execute the TauREx retrieval with streaming support."""
        # Get streaming callback if available
        stream_callback = getattr(self, '_stream_callback', None)

        # Validate observation_path is provided
        if self.observation_path is None or self.observation_path == "":
            raise ValueError(
                "observation_path is required but was not provided. "
                "Please provide the path to your observed spectrum file. "
                "If you used DownloadDataset earlier, use the path from the 'Spectrum file paths:' section of that output."
            )

        # Validate fit_params is provided
        if self.fit_params is None:
            raise ValueError(
                "fit_params is required but was not provided. "
                "Please provide a list of parameters to fit. "
                "Example: fit_params=['planet_radius', 'T', 'H2O', 'CH4', 'CO2', 'CO', 'NH3']"
            )

        # Resolve observation path relative to base_directory
        full_observation_path = os.path.join(self.base_directory, self.observation_path)

        # Parse fit_params if it's a string
        if isinstance(self.fit_params, str):
            try:
                fit_params = ast.literal_eval(self.fit_params)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to parse fit_params string: {self.fit_params}. Error: {e}")
        else:
            fit_params = self.fit_params

        # Parse bounds if it's a string
        if isinstance(self.bounds, str):
            try:
                bounds = ast.literal_eval(self.bounds)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to parse bounds string: {self.bounds}. Error: {e}")
        elif self.bounds is None:
            # Auto-generate default bounds based on fit_params
            bounds = self._generate_default_bounds(fit_params)
        else:
            bounds = self.bounds

        # Call the retrieval function with all parameters
        result = run_taurex_retrieval(
            observation_path=full_observation_path,
            fit_params=fit_params,
            bounds=bounds,
            optimizer=self.optimizer,
            num_live_points=self.num_live_points,
            star_radius=self.star_radius,
            planet_radius=self.planet_radius,
            planet_mass=self.planet_mass,
            planet_temp=self.planet_temp,
            atm_min_pressure=self.atm_min_pressure,
            atm_max_pressure=self.atm_max_pressure,
            nlayers=self.nlayers,
            free_chem_full_molecules=self.free_chem_full_molecules,
            retrieval_mode=self.retrieval_mode,
            output_basename=self.output_basename,
            output_path=self.base_directory,  # Save outputs to base_directory
            stream_callback=stream_callback
        )

        # Format the result as a string for the agent
        output = f"TauREx Retrieval Complete!\n\n"
        output += f"Best-fit parameters:\n"
        for param, value in zip(self.fit_params, result['best_parameters']):
            output += f"  - {param}: {value:.6e}\n"
        output += f"\nLog-likelihood: {result['best_value']}\n\n"
        output += f"Output files (in workspace):\n"
        for key, path in result['outputs'].items():
            # Show relative path to the agent
            rel_path = os.path.relpath(path, self.base_directory)
            output += f"  - {key}: {rel_path}\n"

        return output


def run_taurex_retrieval(
    observation_path,
    fit_params,
    bounds,
    optimizer="nestle",
    num_live_points=50,
    # to build a model
    star_radius=1.0,  # solar radii
    star_temp=5500.0,  # Kelvin
    planet_radius=1.0,  # Jupiter radii
    planet_mass=1.0,  # Jupiter masses
    planet_temp=1500.0,
    atm_min_pressure=1e-3, # dont change these bounds for now
    atm_max_pressure=1e5,
    nlayers=100,
    free_chem_full_molecules=None,
    retrieval_mode="reduced",
    output_basename="retrieval_output",
    output_path=None,
    stream_callback=None):
    """
    Function to run a TauREx retrieval on an observed spectrum data. 

    Multiple retrieval modes are supported: 'reduced', 'equilibrium', 'full'.
    - 'reduced' uses a predefined set of common molecules with fixed mixing ratios, namely H2O, CH4, CO2, CO, and NH3. The parameters fitted are here the mixing ratios of these molecules. It is the simplest option, hence the default if the user doesn't specify anything.
    - 'equilibrium' uses ACEChemistry to calculate the thermochemical equilibrium abundances, reducing the Gibbs free energy. The parameters fitted are here the metallicity and C/O ratio.
    - 'full' allows the user to specify a full list of molecules and their mixing ratios, the agent can look at the cross-section files downloaded and use the full available list. The parameters fitted are here the mixing ratios of these molecules. The argument for the molecules used and their mixing ratios need to be in the form of a list of tuples: [['H2O', 0.02], ['CH4', 0.001], ...].

    observation_path : path to the observed spectrum file (e.g., 'path/to/test_data.dat'), the file should contain three or four columns: wavelength (microns), spectrum (transit depth or flux), vertical error on the transit depth (same units as spectrum), and width of the bins (optional).
    optimizer : 'nestle' Which optimizer to use, can also use multinest. Multinest should be the preferred option for better sampling, but requires multinest to be installed, please use multinest if not specified otherwise.
    fit_params : which parameters to fit, the minimum is ['planet_radius','T'] but it should also include some chemical parameters depending on the retrieval mode.
    bounds : dict[str, [low, high]], the bounds for each fitted parameter. The range should be fairly narrow to help the optimizer converge quickly, but not too narrow to avoid cutting off valid solutions. It should be physically motivated.
    retrieval_mode : 'reduced' as default. Type of retrieval to perform, can also be 'equilibrium' or 'full'.

    This function requires opacity files to be properly set via set_opacity_path(). It also requires the base parameters of the planet and star to build a model: star_radius (solar radii), star_temp (Kelvin), planet_radius (Jupiter radii), planet_mass (Jupiter masses), planet_temp (Kelvin).
    It also needs the pressure range for the atmosphere to be set to [1e-3, 1e5] Pa. It could be modified only if the user specifically asks for it. 
    Output basename and output path can be specified to save the output files in a specific directory with a specific base name. The default directory is the current working directory.
    """

    # Helper function to send streaming updates
    def stream(message):
        if stream_callback:
            stream_callback(message)

    stream("Starting TauREx retrieval...\n")

    # If user did not provide fit_params/bounds, use per-mode defaults.
    # Default molecule lists for modes (used to build default fit params/bounds)
    reduced_molecules = ['H2O', 'CH4', 'CO2', 'CO', 'NH3']

    # For "full", use the user list if provided; otherwise fall back to your default list
    if free_chem_full_molecules is None:
        full_molecules = ['H2O', 'CH4', 'CO2', 'CO', 'NH3', 'C2H2', 'HCN']
    else:
        full_molecules = [m for (m, _) in free_chem_full_molecules]

    mode_default_fit_params = {
        #fit planet radius, temperature, and all reduced gas mixing ratios
        "reduced": ["planet_radius", "T"] + reduced_molecules,
        #equilibrium chemistry fits metallicity and C/O ratio
        "equilibrium": ["planet_radius", "T", "metallicity", "C_O_ratio"],
        #fit planet radius, temperature, and all user-provided "full" gases
        "full": ["planet_radius", "T"] + full_molecules,
    }

    mode_default_bounds = {
        "reduced": {
            "planet_radius": [0.5, 2.0],
            "T": [500.0, 3000.0],
            "H2O": [1e-9, 1e-2],
            "CH4": [1e-9, 1e-2],
            "CO2": [1e-9, 1e-2],
            "CO":  [1e-9, 1e-2],
            "NH3": [1e-9, 1e-2],
        },
        "equilibrium": {
            "planet_radius": [0.5, 2.0],
            "T": [500.0, 3000.0],
            "metallicity": [0.1, 100.0],
            "co_ratio": [0.1, 2.0],
        },
        "full": {
            "planet_radius": [0.5, 2.0],
            "T": [500.0, 3000.0],
            # fill gas bounds below
        },
    }

    # Auto-add bounds for all "full" gases (only if not already there)
    for mol in full_molecules:
        mode_default_bounds["full"].setdefault(mol, [1e-9, 1e-2])

    if fit_params is None:
        fit_params = mode_default_fit_params.get(retrieval_mode, [])
    if bounds is None:
        bounds = mode_default_bounds.get(retrieval_mode, {})
    # (If user passes fit_params/bounds, they override defaults automatically.)

    stream(f"Retrieval mode: {retrieval_mode}\n")
    stream(f"Fitting parameters: {fit_params}\n")
    stream("Building atmospheric model...\n")

    # Build a simple model
    planet = Planet(planet_radius=planet_radius, planet_mass=planet_mass)
    star = BlackbodyStar(temperature=star_temp, radius=star_radius)
    temperature_profile = Isothermal(T=planet_temp)

    if retrieval_mode == "reduced":
        stream("Setting up reduced chemistry with H2O, CH4, CO2, CO, NH3...\n")
        chemistry = TaurexChemistry(fill_gases=['H2', 'He'], ratio=[0.17])
        molecules = [
                ('H2O', 0.02),
                ('CH4', 0.001),
                ('CO2', 0.0001),
                ('CO', 0.001),
                ('NH3', 0.0001)
            ]

        for molecule, mix_ratio in molecules:
            chemistry.addGas(ConstantGas(molecule, mix_ratio=mix_ratio))

    elif retrieval_mode == "equilibrium":
        stream("Setting up equilibrium chemistry (ACE)...\n")
        from acepython.taurex3 import ACEChemistry
        chemistry = ACEChemistry(metallicity=1, co_ratio=0.55)

    else: # retrieval_mode == "full":
        stream("Setting up full chemistry mode...\n")
        chemistry = TaurexChemistry(fill_gases=['H2', 'He'], ratio=[0.17])
        # user-provided molecule list otherwise use default
        if free_chem_full_molecules is None:
            free_chem_full_molecules = [
                ('H2O', 0.02),
                ('CH4', 0.001),
                ('CO2', 0.0001),
                ('CO', 0.001),
                ('NH3', 0.0001),
                ('C2H2', 0.0001),
                ('HCN', 0.0001)
            ]
        for molecule, mix_ratio in free_chem_full_molecules:
            chemistry.addGas(ConstantGas(molecule, mix_ratio=mix_ratio))


    model = TransmissionModel(planet=planet,
                temperature_profile=temperature_profile,
                chemistry=chemistry,
                star=star,
                atm_min_pressure=atm_min_pressure,
                atm_max_pressure=atm_max_pressure,
                nlayers = nlayers
        )

    stream("Adding opacity contributions...\n")
    model.add_contribution(AbsorptionContribution())
    model.add_contribution(RayleighContribution())
    model.add_contribution(CIAContribution(cia_pairs=['H2-H2','H2-He']))

    stream("Building forward model...\n")
    model.build()
    model.model()

    # Load observations
    stream(f"Loading observation from {observation_path}...\n")
    obs = ObservedSpectrum(observation_path)
    obin = obs.create_binner()  # used to bin the model onto the obs grid

    # Build optimizer
    if optimizer is None:
        optimizer = "nestle"

    stream(f"Setting up {optimizer} optimizer...\n")
    stream(f"Using {num_live_points} live points (lower = faster but less accurate)\n")
    if optimizer == "nestle":
        opt = NestleOptimizer(num_live_points=num_live_points)

    elif optimizer == "multinest":
        opt = MultiNestOptimizer(
            num_live_points=num_live_points,
            multi_nest_path="./multinest",
            search_multi_modes=True,
            resume=False,
            importance_sampling=False
        )

    else:
        raise ValueError(f"Unknown optimizer: {optimizer}")

    opt.set_model(model)
    opt.set_observed(obs)

    stream("Configuring fit parameters...\n")
    for p in fit_params:
        opt.enable_fit(p)
        if p in bounds and isinstance(bounds[p], (list, tuple)) and len(bounds[p]) == 2:
            opt.set_boundary(p, list(bounds[p]))
            stream(f"  - {p}: {bounds[p]}\n")

    # Run retrieval with stdout capture
    stream(f"\nStarting {optimizer} retrieval...\n")
    stream(f"This may take several minutes depending on the number of fit parameters.\n")
    stream(f"Fitting {len(fit_params)} parameters: {', '.join(fit_params)}\n")
    stream("="*60 + "\n")

    # Note: TauREx writes directly to stdout during nested sampling.
    # We capture and relay it, but updates may be batched.
    if stream_callback:
        # Create a custom stdout that streams with smart progress throttling
        # CRITICAL: Must restore original stdout when calling callback to prevent recursion
        class StreamingStdout:
            def __init__(self, callback, original_stdout):
                self.callback = callback
                self.original_stdout = original_stdout
                self.buffer = []
                self.last_iteration = -1  # Track last reported iteration
                self.update_frequency = 5  # Report every N iterations

            def write(self, text: str) -> int:
                self.buffer.append(text)

                # Send output when we see newlines
                if '\n' in text:
                    msg = ''.join(self.buffer)

                    # Check if this is a progress line (e.g., "it= 125 logz=...")
                    # and throttle these updates
                    should_send = True
                    if 'it=' in msg and 'logz=' in msg:
                        # Extract iteration number
                        try:
                            it_part = msg.split('it=')[1].split()[0]
                            iteration = int(it_part)

                            # Only send every Nth iteration
                            if iteration - self.last_iteration < self.update_frequency:
                                should_send = False
                            else:
                                self.last_iteration = iteration
                        except (IndexError, ValueError):
                            pass  # Failed to parse, send anyway

                    if should_send:
                        # Temporarily restore original stdout before calling callback
                        current_stdout = sys.stdout
                        sys.stdout = self.original_stdout
                        try:
                            self.callback(msg)
                        finally:
                            sys.stdout = current_stdout

                    self.buffer = []
                return len(text)

            def flush(self):
                if self.buffer:
                    # Temporarily restore original stdout before calling callback
                    current_stdout = sys.stdout
                    sys.stdout = self.original_stdout
                    try:
                        self.callback(''.join(self.buffer))
                    finally:
                        sys.stdout = current_stdout
                    self.buffer = []

        # Pass stream_callback directly and save original stdout
        original_stdout = sys.stdout
        streaming_out = StreamingStdout(stream_callback, original_stdout)
        with redirect_stdout(streaming_out):
            solution = opt.fit()
        streaming_out.flush()
    else:
        solution = opt.fit()

    stream("="*60 + "\n")
    stream("Retrieval completed!\n")

    taurex.log.disableLogging()

    # Grab the best solution, update model, make a quick plot and save it
    stream("Extracting best-fit parameters...\n")
    best_map = None
    best_values_tuple = None
    statistics = None

    for soln, optimized_map, optimized_median, values in opt.get_solution():
        best_map = optimized_map
        best_values_tuple = values
        # Update model to best parameters
        opt.update_model(optimized_map)

    # Safety check: if nothing came back
    if best_map is None:
        raise RuntimeError("Retrieval finished but no solution was returned.")

    # Extract statistics and fit params from values tuple
    fit_params_dict = None
    if best_values_tuple:
        for item in best_values_tuple:
            if item[0] == 'fit_params':
                fit_params_dict = item[1]
            elif item[0] == 'Statistics':
                statistics = item[1]

    stream("Best-fit parameters (MAP values):\n")
    if fit_params_dict:
        for param_name, param_data in fit_params_dict.items():
            # param_data is a FitParamOutput object with attributes
            value = getattr(param_data, 'value', None)
            if value is not None:
                stream(f"  - {param_name}: {value:.6e}\n")
            else:
                stream(f"  - {param_name}: {param_data}\n")
    else:
        # Fallback: just print the array values with param names
        for param, value in zip(fit_params, best_map):
            stream(f"  - {param}: {value:.6e}\n")

    if statistics is not None:
        stream(f"Log-likelihood: {statistics:.2f}\n")

    fit_png = f"{output_basename}_fit.png"
    corner_png = f"{output_basename}_corner.png"
    wl_npy = f"{output_basename}_wavelength.npy"
    sp_npy = f"{output_basename}_spectrum.npy"
    samples_npy = f"{output_basename}_samples.npy"
    weights_npy = f"{output_basename}_weights.npy"

    if output_path is not None:
        fit_png = os.path.join(output_path, fit_png)
        corner_png = os.path.join(output_path, corner_png)
        wl_npy = os.path.join(output_path, wl_npy)
        sp_npy = os.path.join(output_path, sp_npy)
        samples_npy = os.path.join(output_path, samples_npy)
        weights_npy = os.path.join(output_path, weights_npy)

    # Plot observed vs binned best-fit model
    stream("Generating fit plot...\n")
    plt.figure(figsize=(10, 6))
    plt.errorbar(obs.wavelengthGrid, obs.spectrum, obs.errorBar, label='Observed', fmt='o', ms=3)
    model_wl = obs.wavelengthGrid
    model_binned = obin.bin_model(model.model(obs.wavenumberGrid))[1]
    plt.plot(model_wl, model_binned, label='Best-fit model')
    plt.xlabel('Wavelength (µm)')
    plt.ylabel('Transit Depth / Flux')
    plt.title('TauREx Retrieval: Observed vs Best-fit Model')
    plt.legend()
    plt.tight_layout()
    plt.savefig(fit_png, dpi=200)
    plt.close()
    stream(f"Saved fit plot to {fit_png}\n")

    np.save(wl_npy, model_wl)
    np.save(sp_npy, model_binned)

    #grab posterior samples + weights from the optimizer
    stream("Extracting posterior samples...\n")
    samples = opt.get_samples(0)
    weights = opt.get_weights(0)
    labels = opt.fit_names

    np.save(samples_npy, samples)
    np.save(weights_npy, weights)
    stream(f"Saved {len(samples)} posterior samples\n")

    # sanity checks
    #print(samples.shape, weights.shape, len(labels))
    #print("weights sum:", np.sum(weights))
    stream("Generating corner plot...\n")

    # Ensure labels is a list of strings (not dict or other type)
    if isinstance(labels, dict):
        labels = list(labels.keys())
    elif not isinstance(labels, list):
        labels = list(labels)

    fig = corner.corner(
        samples,
        weights=weights,
        labels=labels,
        quantiles=[0.16, 0.5, 0.84],
        show_titles=True,
        title_fmt=".4g",
        bins=60
    )
    plt.savefig(corner_png, dpi=200)
    plt.close()
    stream(f"Saved corner plot to {corner_png}\n")

    stream("\nRetrieval complete!\n")

    return {
        'best_parameters': best_map,
        'best_value': statistics,
        # 'optimizer': optimizer,
        # 'mode': retrieval_mode,
        # 'fit_params': fit_params,
        # 'bounds': bounds,
        'outputs': {
            'fit_png': fit_png,
            'corner_png': corner_png,
            'wavelength_npy': wl_npy,
            'spectrum_npy': sp_npy,
            'samples_npy': samples_npy,
            'weights_npy': weights_npy,
        }
    }


if __name__ == "__main__":
    # Set opacity and CIA paths before running retrieval
    opacity_path = '/Users/adroman/research/exoplanets/agents/new_aster/workspace/linelists/xsec'
    cia_path = '/Users/adroman/research/exoplanets/agents/new_aster/workspace/linelists/cia'

    OpacityCache().set_opacity_path(opacity_path)
    CIACache().set_cia_path(cia_path)
    print(f"Opacity path set to: {opacity_path}")
    print(f"CIA path set to: {cia_path}")

    # Example usage
    observation_path = '/Users/adroman/research/exoplanets/agents/new_aster/workspace/tmp/processed_data/WASP_39_b_3/WASP_39_b_3.11466_4132_1/spectrum.dat'  # Update with actual path to observed spectrum
    fit_params = ['planet_radius', 'T']
    bounds = {
        'planet_radius': [0.5, 2.0],  # Jupiter radii
        'T': [500, 3000]  # Kelvin
    }
    results = run_taurex_retrieval(observation_path, fit_params, bounds, output_basename='retrieval_workspace')
    print("Retrieval results:", results)