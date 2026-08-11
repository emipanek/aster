from multiprocessing import context
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


# Fixed typical mixing ratios, used as initial/seed values when the user names molecules to fit
# without giving exact starting abundances (so the agent isn't inventing numbers).
DEFAULT_MOLECULE_ABUNDANCES = {
    'H2O': 0.02,
    'CH4': 0.001,
    'CO2': 0.0001,
    'CO': 0.001,
    'NH3': 0.0001,
    'HCN': 1e-5,
    'H2S': 1e-5,
    'SO2': 1e-6,
    'C2H2': 1e-6,
    'Na': 1e-6,
    'K': 1e-7,
    'TiO': 1e-7,
    'VO': 1e-8,
}

BASIC_MODEL_MOLECULES = ['H2O', 'CH4', 'CO2', 'CO', 'NH3']


def resolve_free_gas_list(molecules=None, molecular_abundances=None):
    """
    Resolve which gases (and initial/seed mixing ratios) to fit for chemistry_type='free'.

    Precedence: explicit molecular_abundances > named molecules (looked up in the fixed
    default table) > fixed basic model (5 default molecules).
    """
    if molecular_abundances is not None:
        return list(molecular_abundances.items())
    if molecules is not None:
        unknown = [m for m in molecules if m not in DEFAULT_MOLECULE_ABUNDANCES]
        if unknown:
            raise ValueError(
                f"No default abundance available for molecule(s) {unknown}. "
                f"Known molecules: {sorted(DEFAULT_MOLECULE_ABUNDANCES)}. "
                "Specify an exact value via molecular_abundances instead."
            )
        return [(m, DEFAULT_MOLECULE_ABUNDANCES[m]) for m in molecules]
    return [(m, DEFAULT_MOLECULE_ABUNDANCES[m]) for m in BASIC_MODEL_MOLECULES]


def default_fit_params_for_chemistry(chemistry_type, molecules=None, molecular_abundances=None):
    """Auto-generate a fit_params list from the chemistry configuration, when the user/agent
    doesn't supply fit_params explicitly."""
    if chemistry_type == "equilibrium":
        return ["planet_radius", "T", "metallicity", "C_O_ratio"]
    gas_list = resolve_free_gas_list(molecules, molecular_abundances)
    return ["planet_radius", "T"] + [name for name, _ in gas_list]


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
    - Use optimizer="ultranest" (recommended)
    - Can also use optimizer="multinest" or optimizer="nestle" if the user specifically requests, but ultranest is the preferred option for better sampling and performance.
    - Read skills/retrieval_best_practices.md for detailed guidance

    **Observation file format**: 3-4 column text file:
    - Column 1: wavelength in microns
    - Column 2: transit depth (dimensionless, e.g., 0.02 for 2%)
    - Column 3: error on transit depth
    - Column 4 (optional): wavelength bin width

    **Chemistry configuration** - same flexibility as the forward model tools:
    - `chemistry_type='free'` (default): fits molecule mixing ratios via `molecules` (names only,
      seeded from a fixed lookup table) or `molecular_abundances` (exact initial values), or the
      fixed basic 5-molecule set if neither is given.
    - `chemistry_type='equilibrium'`: fits metallicity and C/O ratio via ACEChemistry instead -
      only use this if the user explicitly asks for equilibrium/ACE chemistry.
    - `fit_params`/`bounds` remain full override escape hatches, but are now optional - if not
      given, they're auto-generated from the chemistry configuration above.
    """

    # Required parameters
    observation_path: str = RuntimeField(
        default="",
        description="Path to the observed spectrum file (relative to base_directory). REQUIRED - you must provide this path."
    )
    fit_params: list | str = RuntimeField(
        default="",
        description="List of parameters to fit during retrieval. Can be a Python list or a string representation of a list. Example: ['planet_radius', 'T', 'H2O', 'CH4', 'CO2', 'CO', 'NH3']. Optional - if left as an empty string, auto-generated as ['planet_radius', 'T'] + the resolved molecule list (or ['planet_radius', 'T', 'metallicity', 'C_O_ratio'] for chemistry_type='equilibrium')."
    )
    bounds: dict | str = RuntimeField(
        default="",
        description="Dictionary specifying bounds for each fit parameter with [low, high]. Can be a Python dict or string representation. Example: {'planet_radius': [0.5, 2.0], 'T': [1000, 2000], 'H2O': [1e-12, 1e-2]} or \"{'planet_radius': [0.5, 2.0], 'T': [1000, 2000]}\". Optional - if left as an empty string, auto-generated."
    )

    # Optional parameters with defaults
    optimizer: str = RuntimeField(
        default="ultranest",
        description="Optimizer to use for retrieval ('ultranest', 'multinest', or 'nestle'). Use 'multinest' for better sampling if not specified."
    )
    num_live_points: int = RuntimeField(
        default=100,
        description="Number of live points for nested sampling. Controls accuracy vs speed trade-off. Minimum: 50 (low quality), 100 (standard quality), 200 (high quality, maximum). For production science results, use 100-200."
    )
    star_radius: float = RuntimeField(
        default=1.0,
        description="Radius of the host star in Solar radii. MUST be changed to the value for the considered planet, if not already printed before run the getexoplanetparameters tool. If you used the GetExoplanetParameters tool, use the value from the 'Star radius (Solar radii):' section of that output."
    )
    planet_radius: float = RuntimeField(
        default=1.0,
        description="Radius of the planet in Jupiter radii. MUST be changed to the value for the considered planet, if not already printed before run the getexoplanetparameters tool. If you used the GetExoplanetParameters tool, use the value from the 'Planet radius (Jupiter radii):' section of that output."
    )
    planet_mass: float = RuntimeField(
        default=1.0,
        description="Mass of the planet in Jupiter masses. MUST be changed to the value for the considered planet, if not already printed before run the getexoplanetparameters tool. If you used the GetExoplanetParameters tool, use the value from the 'Planet mass (Jupiter masses):' section of that output."
    )
    planet_temp: float = RuntimeField(
        default=1500.0,
        description="Temperature of the planet in Kelvin. MUST be changed to the value for the considered planet, if not already printed before run the getexoplanetparameters tool. If you used the GetExoplanetParameters tool, use the value from the 'Planet temperature (K):' section of that output."
    )
    atm_min_pressure: float = RuntimeField(
        default=1e-1,
        description="Minimum atmospheric pressure in Pa. IMPORTANT: TauREx works in Pa, NOT bars! Standard value is 1e-1 Pa."
    )
    atm_max_pressure: float = RuntimeField(
        default=1e6,
        description="Maximum atmospheric pressure in Pa. Standard value is 1e6 Pa."
    )
    nlayers: int = RuntimeField(
        default=100,
        description="Number of atmospheric layers to model. Standard value is 100."
    )
    molecules: list[str] | str = RuntimeField(
        default="",
        description="List of molecule names to fit, e.g. ['H2O', 'CH4']. Each gets an initial/seed abundance from a fixed lookup table before fitting - use this when the user names specific molecules without giving exact starting values. Ignored if molecular_abundances is provided. Not used in 'equilibrium' chemistry_type. Leave as an empty string if not specifying molecules."
    )
    molecular_abundances: dict | str = RuntimeField(
        default="",
        description="Dict of molecule name to initial/seed mixing ratio to fit, e.g. {'H2O': 0.02, 'CH4': 0.001}. Takes precedence over 'molecules'. If neither is given, fits the fixed basic set (H2O, CH4, CO2, CO, NH3). Not used in 'equilibrium' chemistry_type. Leave as an empty string if not specifying exact abundances."
    )
    chemistry_type: str = RuntimeField(
        default="free",
        description="'free' (default) fits molecule mixing ratios via 'molecules'/'molecular_abundances' (or the basic 5-molecule set if neither given). 'equilibrium' instead fits metallicity and C/O ratio via ACEChemistry thermochemical equilibrium - only set this if the user explicitly asks for equilibrium/ACE chemistry retrieval."
    )
    metallicity: float = RuntimeField(
        default=1.0,
        description="Initial/seed metallicity relative to solar, before fitting. Only used when chemistry_type='equilibrium'."
    )
    co_ratio: float = RuntimeField(
        default=0.54,
        description="Initial/seed carbon-to-oxygen ratio, before fitting. Only used when chemistry_type='equilibrium'. Solar value is ~0.54."
    )
    output_basename: str = RuntimeField(
        default="retrieval_output",
        description="Base name for output files generated by the retrieval."
    )

    # State field - agent doesn't see this
    base_directory: str = StateField()

 #   def _generate_default_bounds(self, fit_params: list) -> dict:
 #       """
 #       Generate reasonable default bounds for fit parameters.

 #       Args:
 #           fit_params: List of parameter names to fit

 #       Returns:
 #           Dictionary of bounds for each parameter
 #       """
 #       default_bounds = {
 #           # Physical parameters
 #           'planet_radius': [0.5, 2.5],      # Jupiter radii
 #           'planet_mass': [0.1, 5.0],        # Jupiter masses
 #           'T': [500, 3000],                  # Temperature in Kelvin
 #           'star_radius': [0.5, 2.0],        # Solar radii

            # Molecular abundances (log-space)
#            'H2O': [1e-9, 1e-2],
#            'CH4': [1e-9, 1e-2],
#            'CO2': [1e-9, 1e-2],
#            'CO': [1e-9, 1e-2],
#            'NH3': [1e-9, 1e-2],
#            'N2': [1e-9, 1e-2],
#            'O2': [1e-9, 1e-2],
#            'HCN': [1e-9, 1e-2],
#            'H2S': [1e-9, 1e-2],

            # Equilibrium chemistry parameters
#            'metallicity': [0.1, 10.0],       # Solar metallicity
#            'c_o_ratio': [0.1, 2.0],          # C/O ratio
#        }

        # Build bounds dict for requested parameters
#        bounds = {}
#        for param in fit_params:
#            if param in default_bounds:
#                bounds[param] = default_bounds[param]
#            else:
#                raise ValueError(f"Unknown parameter '{param}' - cannot generate default bounds. Please provide bounds manually.")

#        return bounds

    def _generate_default_bounds(self, fit_params: list, pl_chosen: dict = None) -> dict:
        """
        Generate bounds based on the considered exoplanet.

        Args:
            fit_params: List of parameter names to fit
            pl_chosen: Dictionary with current parameter values (e.g. planet_radius, T, etc.)

        Returns:
            Dictionary of bounds for each parameter
        """

        bounds = {}

        for param in fit_params:

            # Planet radius
            if param == 'planet_radius':
                if pl_chosen and 'planet_radius' in pl_chosen:
                    r = pl_chosen['planet_radius']
                    bounds[param] = [max(0.1, r - 0.5), r + 0.5]
                else:
                    bounds[param] = [0.5, 2.5]

            # Temperature 
            elif param == 'T':
                if pl_chosen and 'planet_temp' in pl_chosen:
                    T = pl_chosen['planet_temp']
                    bounds[param] = [max(500, T - 500), T + 500]
                else:
                    bounds[param] = [500, 2500]

            # Planet mass
            elif param == 'planet_mass':
                if pl_chosen and 'planet_mass' in pl_chosen:
                    m = pl_chosen['planet_mass']
                    bounds[param] = [max(0.05, m * 0.5), m * 2.0]
                else:
                    bounds[param] = [0.1, 5.0]

            # Equilibrium chemistry
            elif param == 'metallicity':
                bounds[param] = [0.1, 10.0]

            elif param == 'C_O_ratio':
                bounds[param] = [0.1, 2.0]

            # Molecules - any other fit param is assumed to be a molecule mixing ratio,
            # since 'molecules'/'molecular_abundances' now allow arbitrary species names
            else:
                bounds[param] = [1e-12, 1e-2]

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

        # Resolve observation path relative to base_directory
        full_observation_path = os.path.join(self.base_directory, self.observation_path)

        # "" is this tool's not-given sentinel (RuntimeField can't reliably default to None),
        # so it's treated the same as None below for fit_params/molecules/molecular_abundances/bounds.

        # Parse fit_params if it's a string
        if self.fit_params == "":
            fit_params = None
        elif isinstance(self.fit_params, str):
            try:
                fit_params = ast.literal_eval(self.fit_params)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to parse fit_params string: {self.fit_params}. Error: {e}")
        else:
            fit_params = self.fit_params

        # Parse molecules if it's a string
        molecules = self.molecules
        if molecules == "":
            molecules = None
        elif isinstance(molecules, str):
            try:
                molecules = ast.literal_eval(molecules)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to parse molecules string: {molecules}. Error: {e}")

        # Parse molecular_abundances if it's a string
        molecular_abundances = self.molecular_abundances
        if molecular_abundances == "":
            molecular_abundances = None
        elif isinstance(molecular_abundances, str):
            try:
                molecular_abundances = ast.literal_eval(molecular_abundances)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to parse molecular_abundances string: {molecular_abundances}. Error: {e}")

        # Auto-generate fit_params from the chemistry configuration if not given
        if fit_params is None:
            fit_params = default_fit_params_for_chemistry(self.chemistry_type, molecules, molecular_abundances)

        # Parse bounds if it's a string
        if self.bounds == "" or self.bounds is None:
            # Auto-generate default bounds based on fit_params
            pl_chosen = {
                'planet_radius': self.planet_radius,
                'planet_mass': self.planet_mass,
                'planet_temp': self.planet_temp,
                'star_radius': self.star_radius,
            }

            bounds = self._generate_default_bounds(fit_params, pl_chosen=pl_chosen)
        elif isinstance(self.bounds, str):
            try:
                bounds = ast.literal_eval(self.bounds)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to parse bounds string: {self.bounds}. Error: {e}")
        else:
            bounds = self.bounds

        # Call the retrieval function with all parameters
        result = run_taurex_retrieval(
            observation_path=full_observation_path,
            fit_params=fit_params,
            bounds=bounds, #IMPORTANT, bounds MUST be provided
            optimizer=self.optimizer,
            num_live_points=self.num_live_points,
            star_radius=self.star_radius,
            planet_radius=self.planet_radius,
            planet_mass=self.planet_mass,
            planet_temp=self.planet_temp,
            atm_min_pressure=self.atm_min_pressure,
            atm_max_pressure=self.atm_max_pressure,
            nlayers=self.nlayers,
            molecules=molecules,
            molecular_abundances=molecular_abundances,
            chemistry_type=self.chemistry_type,
            metallicity=self.metallicity,
            co_ratio=self.co_ratio,
            output_basename=self.output_basename,
            output_path=self.base_directory,  # Save outputs to base_directory
            stream_callback=stream_callback
        )

        # Format the result as a string for the agent
        output = f"TauREx Retrieval Complete!\n\n"
        output += f"Best-fit parameters:\n"
        for param, value in zip(fit_params, result['best_parameters']):
            output += f"  - {param}: {value}\n"
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
    bounds=None,
    optimizer="ultranest",
    num_live_points=100, #keep the recommended value of 100 if nothing is specified by the user
    # to build a model
    star_radius=1.0,  # solar radii
    star_temp=5500.0,  # Kelvin
    planet_radius=1.0,  # Jupiter radii
    planet_mass=1.0,  # Jupiter masses
    planet_temp=1500.0,
    atm_min_pressure=1e-1,
    atm_max_pressure=1e6,
    nlayers=100,
    molecules=None,
    molecular_abundances=None,
    chemistry_type="free",
    metallicity=1.0,
    co_ratio=0.54,
    output_basename="retrieval_output",
    output_path=None,
    stream_callback=None):
    """
    Function to run a TauREx retrieval on an observed spectrum data.

    Chemistry configuration mirrors the forward model tools:
    - chemistry_type='free' (default) fits molecule mixing ratios, chosen by (in this order):
      molecular_abundances (exact initial values) > molecules (names only, seeded from a fixed
      lookup table) > the fixed basic 5-molecule set (H2O, CH4, CO2, CO, NH3) if neither is given.
    - chemistry_type='equilibrium' fits metallicity and C/O ratio via ACEChemistry instead, seeded
      from the metallicity/co_ratio arguments; molecules/molecular_abundances are ignored.

    observation_path : path to the observed spectrum file (e.g., 'path/to/test_data.dat'), the file should contain three or four columns: wavelength (microns), spectrum (transit depth or flux), vertical error on the transit depth (same units as spectrum), and width of the bins (optional).
    optimizer : 'ultranest'. Which optimizer to use, can also use multinest or nestle. Multinest should be the preferred option for better sampling, but requires multinest to be installed, please use ultranest if not specified otherwise.
    fit_params : which parameters to fit. If not given, auto-generated from the chemistry configuration: ['planet_radius', 'T'] + the resolved molecule list, or ['planet_radius', 'T', 'metallicity', 'C_O_ratio'] for chemistry_type='equilibrium'.
    bounds : dict[str, [low, high]], the bounds for each fitted parameter. The range should be fairly narrow to help the optimizer converge quickly, but not too narrow to avoid cutting off valid solutions. It should be physically motivated.

    This function requires opacity files to be properly set via set_opacity_path(). It also requires the base parameters of the planet and star to build a model: star_radius (solar radii), star_temp (Kelvin), planet_radius (Jupiter radii), planet_mass (Jupiter masses), planet_temp (Kelvin).
    It also needs the pressure range for the atmosphere to be set to [1e-1, 1e6] Pa. It could be modified only if the user specifically asks for it.
    Output basename and output path can be specified to save the output files in a specific directory with a specific base name. The default directory is the current working directory.
    """

    # Helper function to send streaming updates
    def stream(message):
        if stream_callback:
            stream_callback(message)

    stream("Starting TauREx retrieval...\n")

    if fit_params is None:
        fit_params = default_fit_params_for_chemistry(chemistry_type, molecules, molecular_abundances)

    # build adaptive bounds in function of the considered planet (not every planet has the same bounds!)
    adaptive_bounds = {}
    for p in fit_params:

        if p == 'planet_radius':
            adaptive_bounds[p] = [max(0.1, planet_radius - 0.5), planet_radius + 0.5]

        elif p == 'T':
            adaptive_bounds[p] = [max(500, planet_temp - 500), planet_temp + 500]

        elif p == 'planet_mass':
            adaptive_bounds[p] = [max(0.05, planet_mass * 0.5), planet_mass * 2.0]

        elif p == 'metallicity':
            adaptive_bounds[p] = [0.1, 10.0]

        elif p == 'C_O_ratio':
            adaptive_bounds[p] = [0.1, 2.0]

        # any other fit param is assumed to be a molecule mixing ratio, since
        # molecules/molecular_abundances now allow arbitrary species names
        else:
            adaptive_bounds[p] = [1e-12, 1e-2]

    # If user did not provide bounds, use adaptive bounds
    if bounds is None:
        bounds = adaptive_bounds
    else:
        # user-provided bounds override adaptive defaults
        merged_bounds = adaptive_bounds.copy()
        merged_bounds.update(bounds)  # user bounds override adaptive defaults
        bounds = merged_bounds

    stream(f"Chemistry type: {chemistry_type}\n")
    stream(f"Fitting parameters: {fit_params}\n")
    stream("Building atmospheric model...\n")

    # Build a simple model
    planet = Planet(planet_radius=planet_radius, planet_mass=planet_mass)
    star = BlackbodyStar(temperature=star_temp, radius=star_radius)
    temperature_profile = Isothermal(T=planet_temp)

    if chemistry_type == "equilibrium":
        if molecules is not None or molecular_abundances is not None:
            raise ValueError(
                "chemistry_type='equilibrium' ignores 'molecules'/'molecular_abundances'; "
                "remove them or set chemistry_type='free' to fit molecule mixing ratios instead."
            )
        stream(f"Setting up equilibrium chemistry (ACE), seed metallicity={metallicity}, C/O={co_ratio}...\n")
        from acepython.taurex3 import ACEChemistry
        # ACEChemistry's constructor takes ratio kwargs named '{Element}_ratio' (e.g. C_ratio for
        # the default ratio_element='O') - it does NOT take a 'co_ratio' kwarg; that would be
        # silently swallowed by its **kwargs and have no effect.
        chemistry = ACEChemistry(metallicity=metallicity, C_ratio=co_ratio)

    elif chemistry_type == "free":
        gas_list = resolve_free_gas_list(molecules, molecular_abundances)
        stream(f"Setting up free chemistry with: {', '.join(name for name, _ in gas_list)}...\n")
        chemistry = TaurexChemistry(fill_gases=['H2', 'He'], ratio=[0.17])
        for molecule, mix_ratio in gas_list:
            chemistry.addGas(ConstantGas(molecule, mix_ratio=mix_ratio))

    else:
        raise ValueError(f"Unknown chemistry_type: {chemistry_type!r}. Must be 'free' or 'equilibrium'.")

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
        optimizer = "ultranest"

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

    elif optimizer == "ultranest":
        from taurex_ultranest import UltranestSampler
        opt = UltranestSampler(
            num_live_points=num_live_points,
            dlogz=0.5,
            dkl=0.5
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
                stream(f"  - {param_name}: {value}\n")
            else:
                stream(f"  - {param_name}: {param_data}\n")
    else:
        # Fallback: just print the array values with param names
        for param, value in zip(fit_params, best_map):
            stream(f"  - {param}: {value}\n")

    if statistics is not None:
        stream(f"Log-likelihood: {statistics}\n")

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
    # Should be updated with the correct paths specified by the user
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
