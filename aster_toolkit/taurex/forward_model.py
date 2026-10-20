import os
import numpy as np
import pandas as pd
from tqdm import tqdm
import matplotlib.pyplot as plt
from astropy.io import ascii
import ast

from orchestral.tools.filesystem.filesystem_tools import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from .citation import TAUREX_CITATION_NOTICE

# Taurex imports
import taurex
from taurex.cache import GlobalCache, OpacityCache, CIACache
from taurex.model import TransmissionModel, EmissionModel
from taurex.data.profiles.temperature import Isothermal
from taurex.planet import Planet
from taurex.stellar import BlackbodyStar
from taurex.chemistry import TaurexChemistry
from taurex.chemistry import ConstantGas
from taurex.contributions import AbsorptionContribution, RayleighContribution, CIAContribution
from taurex.data.spectrum.observed import ObservedSpectrum
from taurex.optimizer.nestle import NestleOptimizer
from taurex.optimizer.multinest import MultiNestOptimizer


# Fixed typical mixing ratios, used when the user names molecules without giving exact abundances (so the agent is not inventing numbers)
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


def build_chemistry(
    molecules=None,
    molecular_abundances=None,
    chemistry_type="free",
    metallicity=1.0,
    co_ratio=0.54,
    element_abundance_source="asplund_2021",
    chemistry_file_path=None,
    chemistry_file_gases=None,
    nlayers=100,
):
    """
    Build a taurex chemistry object, shared by the transmission and emission forward models.

    chemistry_type:
    - 'free' (default): fixed mixing ratios via molecular_abundances (exact values) > molecules (names only, looked up in the fixed default table) > fixed basic model (5 default molecules).
    - 'equilibrium': ACEChemistry (metallicity/co_ratio); ignores molecules/molecular_abundances.
    - 'fastchem': FastChemChemistry - same metallicity/co_ratio inputs as 'equilibrium' but uses FastChem instead of ACE; molecules (optional, defaults to the free-chemistry basic model) selects which species besides H2/He are solved for and included in the opacity model.
    - 'file': TauREx's own ChemistryFile - a fixed per-layer VMR profile loaded from chemistry_file_path, with columns named by chemistry_file_gases; ignores molecules/molecular_abundances/metallicity/co_ratio. The file's row count must match nlayers (default 100) - raise nlayers to match the file, rather than reformatting the file to 100 rows.
    """
    if chemistry_type == "equilibrium":
        if molecules is not None or molecular_abundances is not None:
            raise ValueError(
                "chemistry_type='equilibrium' ignores 'molecules'/'molecular_abundances' remove them or set chemistry_type='free' to use fixed mixing ratios instead."
            )
        from acepython.taurex3 import ACEChemistry
        # ACEChemistry's constructor takes ratio kwargs named '{Element}_ratio' (e.g. C_ratio for the default ratio_element='O'), it does NOT take a 'co_ratio' kwarg; that would be silently swallowed by its **kwargs and have no effect (confirmed: passing co_ratio=X produces the same ACEChemistry._ratios as not passing it at all).
        return ACEChemistry(metallicity=metallicity, C_ratio=co_ratio)

    if chemistry_type == "fastchem":
        if molecular_abundances is not None:
            raise ValueError(
                "chemistry_type='fastchem' ignores 'molecular_abundances' (FastChem computes abundances from equilibrium, it doesn't take fixed values); remove it, or use 'molecules' to pick which species to solve for, or set chemistry_type='free'."
            )
        from .fastchem_chemistry import FastChemChemistry
        resolved_molecules = molecules if molecules is not None else list(BASIC_MODEL_MOLECULES)
        return FastChemChemistry(
            molecules=resolved_molecules,
            metallicity=metallicity,
            co_ratio=co_ratio,
            element_abundance_source=element_abundance_source,
        )

    if chemistry_type == "file":
        if molecules is not None or molecular_abundances is not None:
            raise ValueError(
                "chemistry_type='file' ignores 'molecules'/'molecular_abundances' (the mixing profile comes entirely from the file); remove them, or set chemistry_type='free' to specify molecules directly."
            )
        if not chemistry_file_path or not chemistry_file_gases:
            raise ValueError(
                "chemistry_type='file' requires both chemistry_file_path (the text file to load) and chemistry_file_gases (the gas name for each column, in file order)."
            )
        from taurex.data.profiles.chemistry.filechemistry import ChemistryFile
        chemistry = ChemistryFile(gases=list(chemistry_file_gases), filename=chemistry_file_path)
        n_gases, n_layers = chemistry.mixProfile.shape
        if n_gases != len(chemistry_file_gases):
            raise ValueError(
                f"chemistry_file_gases has {len(chemistry_file_gases)} name(s) but {chemistry_file_path} has {n_gases} column(s)! these must match 1:1!"
            )
        if n_layers != nlayers:
            raise ValueError(
                f"{chemistry_file_path} has {n_layers} row(s), but nlayers={nlayers} - the file "
                f"must have exactly one row per atmospheric layer. Set nlayers={n_layers} to match the file, rather than reformatting the file."
            )
        return chemistry

    if chemistry_type != "free":
        raise ValueError(
            f"Unknown chemistry_type: {chemistry_type!r}. Must be 'free', 'equilibrium', 'fastchem', or 'file'."
        )

    chemistry = TaurexChemistry(fill_gases=['H2', 'He'], ratio=[0.17])

    if molecular_abundances is not None:
        gas_list = list(molecular_abundances.items())
    elif molecules is not None:
        unknown = [m for m in molecules if m not in DEFAULT_MOLECULE_ABUNDANCES]
        if unknown:
            raise ValueError(
                f"No default abundance available for molecule(s) {unknown}. "
                f"Known molecules: {sorted(DEFAULT_MOLECULE_ABUNDANCES)}. "
                "Specify an exact value via molecular_abundances instead."
            )
        gas_list = [(m, DEFAULT_MOLECULE_ABUNDANCES[m]) for m in molecules]
    else:
        gas_list = [(m, DEFAULT_MOLECULE_ABUNDANCES[m]) for m in BASIC_MODEL_MOLECULES]

    for molecule, mix_ratio in gas_list:
        chemistry.addGas(ConstantGas(molecule, mix_ratio=mix_ratio))

    return chemistry


class RunTaurexTransmissionModelTool(BaseTool):
    """
    Run a Taurex forward model with specified parameters and generate a transmission spectrum plot.

    Also saves '{filename}_vmr_profile.npz' with the per-layer volume mixing ratio (VMR) of every gas vs. pressure/altitude, use it to make a VMR-vs-altitude ("spaghetti") plot. Seesave_vmr_profile()'s docstring in forward_model.py for the exact array layout and a plotting snippet.

    IMPORTANT: Every result from this tool MUST be cited as TauREx (Al-Refaie et al. 2021, ApJ, 917, 37, doi:10.3847/1538-4357/ac0252) the tool appends this citation requirement to its own output, always relay it to the user rather than dropping it.
    """

    star_radius: float = RuntimeField(description="Radius of the star in solar radii", default=1.0)
    planet_radius: float = RuntimeField(description="Radius of the planet in Jupiter radii", default=1.0)
    planet_mass: float = RuntimeField(description="Mass of the planet in Jupiter masses", default=1.0)
    planet_temp: float = RuntimeField(description="Temperature of the planet in Kelvin", default=1500.0)
    atm_min_pressure: float | None = RuntimeField(description="Minimum atmospheric pressure in Pascal (pressure at the highest simulated layer)", default=1e-1)
    atm_max_pressure: float | None = RuntimeField(description="Maximum atmospheric pressure in Pascal (pressure at the lowest simulated layer)", default=1e6)
    molecules: list[str] | str = RuntimeField(
        description="List of molecule names to include, e.g. ['H2O', 'CH4'] or \"['H2O', 'CH4']\". Use this when the user asks for specific molecules. Ignored if molecular_abundances is provided. Not used in 'equilibrium'/'file' chemistry_type; in 'fastchem' it instead picks which species (besides H2/He) FastChem solves for, defaulting to the basic model set if not given. Leave as an empty string if not specifying molecules.",
        default=""
    )
    molecular_abundances: dict | str = RuntimeField(
        description="Dictionary of molecule names to exact mixing ratios. Can be a Python dict or string representation. Example: {'H2O': 0.02, 'CH4': 0.001} or \"{'H2O': 0.02, 'CH4': 0.001}\". Use this only when the user specifies exact abundance values. Takes precedence over 'molecules'. If neither is provided, uses the fixed basic model (H2O, CH4, CO2, CO, NH3 at default abundances). Only used in 'free' chemistry_type. Leave as an empty string if not specifying exact abundances.",
        default=""
    )
    chemistry_type: str = RuntimeField(
        description=(
            "'free' (default) sets fixed molecule mixing ratios via 'molecules'/'molecular_abundances' (or the basic model if neither is given). "
            "'equilibrium' uses ACEChemistry to compute abundances from thermochemical equilibrium given 'metallicity' and 'co_ratio' only set this if the user explicitly asks for equilibrium/ACE chemistry. "
            "'fastchem' is the same metallicity/co_ratio equilibrium idea but computed via FastChem instead of ACE (see 'molecules' to pick which species it solves for) only set this if the user explicitly asks for FastChem chemistry. "
            "'file' loads a fixed per-layer mixing-ratio profile from 'chemistry_file_path'/'chemistry_file_gases' (e.g. from a GCM or photochemical model) only set this if the user explicitly provides/references such a file."
        ),
        default="free"
    )
    metallicity: float = RuntimeField(
        description="Metallicity relative to solar, only used when chemistry_type='equilibrium' or 'fastchem'.",
        default=1.0
    )
    co_ratio: float = RuntimeField(
        description="Carbon-to-oxygen ratio, only used when chemistry_type='equilibrium' or 'fastchem'. Solar value is ~0.54.",
        default=0.54
    )
    element_abundance_source: str = RuntimeField(
        description="Which solar composition reference FastChem starts from, before metallicity/co_ratio scaling. Only used when chemistry_type='fastchem'. One of 'asplund_2021' (default), 'asplund_2009', 'lodders_2003', 'lodders_2025'.",
        default="asplund_2021"
    )
    chemistry_file_path: str = RuntimeField(
        description="Path to a text file of mixing ratios (whitespace-delimited, one row per atmospheric layer, must match 'nlayers', one column per gas). Only used when chemistry_type='file'. Leave as an empty string otherwise.",
        default=""
    )
    chemistry_file_gases: list[str] | str = RuntimeField(
        description="Gas name for each column of chemistry_file_path, in file column order, e.g. ['H2', 'He', 'H2O', 'CH4']. Only used when chemistry_type='file'. Leave as an empty string otherwise.",
        default=""
    )
    nlayers: int = RuntimeField(
        description="Number of atmospheric layers to model. Standard value is 100. When chemistry_type='file', this MUST match the row count of chemistry_file_path exactly so set this to however many rows the file has.",
        default=100
    )
    filename: str = RuntimeField(description="The output file will be saved as '{filename}_spectrum.png'", default='')
    base_directory: str = StateField()

    def _run(self):
        # "" is this tool's not-given sentinel (RuntimeField can't reliably default to None).
        # Put molecular_abundances if it's a string
        molecular_abundances = self.molecular_abundances
        if molecular_abundances == "":
            molecular_abundances = None
        elif isinstance(molecular_abundances, str):
            try:
                molecular_abundances = ast.literal_eval(molecular_abundances)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to compute molecular_abundances string: {molecular_abundances}. Error: {e}")

        # Put molecules if it's a string
        molecules = self.molecules
        if molecules == "":
            molecules = None
        elif isinstance(molecules, str):
            try:
                molecules = ast.literal_eval(molecules)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to compute molecules string: {molecules}. Error: {e}")

        # Put chemistry_file_gases if it's a string
        chemistry_file_gases = self.chemistry_file_gases
        if chemistry_file_gases == "":
            chemistry_file_gases = None
        elif isinstance(chemistry_file_gases, str):
            try:
                chemistry_file_gases = ast.literal_eval(chemistry_file_gases)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to compute chemistry_file_gases string: {chemistry_file_gases}. Error: {e}")

        generate_taurex_model(
            star_radius=self.star_radius,
            planet_radius=self.planet_radius,
            planet_mass=self.planet_mass,
            planet_temp=self.planet_temp,
            atm_min_pressure=self.atm_min_pressure,
            atm_max_pressure=self.atm_max_pressure,
            molecules=molecules,
            molecular_abundances=molecular_abundances,
            chemistry_type=self.chemistry_type,
            metallicity=self.metallicity,
            co_ratio=self.co_ratio,
            element_abundance_source=self.element_abundance_source,
            chemistry_file_path=self.chemistry_file_path or None,
            chemistry_file_gases=chemistry_file_gases,
            nlayers=self.nlayers,
            filename=self.filename,
            base_directory=self.base_directory
        )
        return (
            f"Taurex model run successfully with output file: {self.filename}_spectrum.png "
            f"(wavelength and spectrum arrays also saved as {self.filename}_fm_wavelength.npy "
            f"and {self.filename}_fm_spectrum.npy). Volume mixing ratio vs. pressure/altitude profiles saved to {self.filename}_vmr_profile.npz."
            + TAUREX_CITATION_NOTICE
        )


class RunTaurexEmissionModelTool(BaseTool):
    """
    Run a Taurex forward model with specified parameters and generate an emission spectrum plot.

    Also saves '{filename}_vmr_profile.npz' with the per-layer volume mixing ratio (VMR) of every gas vs. pressure/altitude, you use it to make a VMR-vs-altitude ("spaghetti") plot. See save_vmr_profile()'s docstring in forward_model.py for the exact array layout and a plotting snippet.

    IMPORTANT: Every result from this tool MUST be cited as TauREx (Al-Refaie et al. 2021, ApJ, 917, 37, doi:10.3847/1538-4357/ac0252) the tool appends this citation requirement to its own output, always relay it to the user rather than dropping it.
    """

    star_radius: float = RuntimeField(description="Radius of the star in solar radii", default=1.0)
    star_temp: float = RuntimeField(description="Temperature of the star in Kelvin (blackbody), used for stellar flux normalization", default=5500.0)
    planet_radius: float = RuntimeField(description="Radius of the planet in Jupiter radii", default=1.0)
    planet_mass: float = RuntimeField(description="Mass of the planet in Jupiter masses", default=1.0)
    planet_temp: float = RuntimeField(description="Temperature of the planet in Kelvin", default=1500.0)
    atm_min_pressure: float | None = RuntimeField(description="Minimum atmospheric pressure in Pascal (pressure at the highest simulated layer)", default=1e-1)
    atm_max_pressure: float | None = RuntimeField(description="Maximum atmospheric pressure in Pascal (pressure at the lowest simulated layer)", default=1e6)
    molecules: list[str] | str = RuntimeField(
        description="List of molecule names to include, e.g. ['H2O', 'CH4'] or \"['H2O', 'CH4']\". Use this when the user asks for specific molecules. Ignored if molecular_abundances is provided. Not used in 'equilibrium'/'file' chemistry_type; in 'fastchem' it instead picks which species (besides H2/He) FastChem solves for, defaulting to the basic model set if not given. Leave as an empty string if not specifying molecules.",
        default=""
    )
    molecular_abundances: dict | str = RuntimeField(
        description="Dictionary of molecule names to exact mixing ratios. Can be a Python dict or string representation. Example: {'H2O': 0.02, 'CH4': 0.001} or \"{'H2O': 0.02, 'CH4': 0.001}\". Use this only when the user specifies exact abundance values. Takes precedence over 'molecules'. If neither is provided, uses the fixed basic model (H2O, CH4, CO2, CO, NH3 at default abundances). Only used in 'free' chemistry_type. Leave as an empty string if not specifying exact abundances.",
        default=""
    )
    chemistry_type: str = RuntimeField(
        description=(
            "'free' (default) sets fixed molecule mixing ratios via 'molecules'/'molecular_abundances' (or the basic model if neither is given). "
            "'equilibrium' uses ACEChemistry to compute abundances from thermochemical equilibrium given 'metallicity' and 'co_ratio' only set this if the user explicitly asks for equilibrium/ACE chemistry. "
            "'fastchem' is the same metallicity/co_ratio equilibrium idea but computed via FastChem instead of ACE (see 'molecules' to pick which species it solves for) only set this if the user explicitly asks for FastChem chemistry. "
            "'file' loads a fixed per-layer mixing-ratio profile from 'chemistry_file_path'/'chemistry_file_gases' (e.g. from a GCM or photochemical model) only set this if the user explicitly provides/references such a file."
        ),
        default="free"
    )
    metallicity: float = RuntimeField(
        description="Metallicity relative to solar, only used when chemistry_type='equilibrium' or 'fastchem'.",
        default=1.0
    )
    co_ratio: float = RuntimeField(
        description="Carbon-to-oxygen ratio, only used when chemistry_type='equilibrium' or 'fastchem'. Solar value is ~0.54.",
        default=0.54
    )
    element_abundance_source: str = RuntimeField(
        description="Which solar composition reference FastChem starts from, before metallicity/co_ratio scaling. Only used when chemistry_type='fastchem'. One of 'asplund_2021' (default), 'asplund_2009', 'lodders_2003', 'lodders_2025'.",
        default="asplund_2021"
    )
    chemistry_file_path: str = RuntimeField(
        description="Path to a text file of mixing ratios (whitespace-delimited, one row per atmospheric layer, must match 'nlayers', one column per gas). Only used when chemistry_type='file'. Leave as an empty string otherwise.",
        default=""
    )
    chemistry_file_gases: list[str] | str = RuntimeField(
        description="Gas name for each column of chemistry_file_path, in file column order, e.g. ['H2', 'He', 'H2O', 'CH4']. Only used when chemistry_type='file'. Leave as an empty string otherwise.",
        default=""
    )
    nlayers: int = RuntimeField(
        description="Number of atmospheric layers to model. Standard value is 100. When chemistry_type='file', this MUST match the row count of chemistry_file_path exactly so set this to however many rows the file has.",
        default=100
    )
    filename: str = RuntimeField(description="The output file will be saved as '{filename}_emission_spectrum.png'", default='')
    base_directory: str = StateField()

    def _run(self):
        # "" is this tool's not-given sentinel (RuntimeField can't reliably default to None).
        molecular_abundances = self.molecular_abundances
        if molecular_abundances == "":
            molecular_abundances = None
        elif isinstance(molecular_abundances, str):
            try:
                molecular_abundances = ast.literal_eval(molecular_abundances)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to parse molecular_abundances string: {molecular_abundances}. Error: {e}")

        molecules = self.molecules
        if molecules == "":
            molecules = None
        elif isinstance(molecules, str):
            try:
                molecules = ast.literal_eval(molecules)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to parse molecules string: {molecules}. Error: {e}")

        # Put chemistry_file_gases if it's a string
        chemistry_file_gases = self.chemistry_file_gases
        if chemistry_file_gases == "":
            chemistry_file_gases = None
        elif isinstance(chemistry_file_gases, str):
            try:
                chemistry_file_gases = ast.literal_eval(chemistry_file_gases)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to parse chemistry_file_gases string: {chemistry_file_gases}. Error: {e}")

        generate_taurex_emission_model(
            star_radius=self.star_radius,
            star_temp=self.star_temp,
            planet_radius=self.planet_radius,
            planet_mass=self.planet_mass,
            planet_temp=self.planet_temp,
            atm_min_pressure=self.atm_min_pressure,
            atm_max_pressure=self.atm_max_pressure,
            molecules=molecules,
            molecular_abundances=molecular_abundances,
            chemistry_type=self.chemistry_type,
            metallicity=self.metallicity,
            co_ratio=self.co_ratio,
            element_abundance_source=self.element_abundance_source,
            chemistry_file_path=self.chemistry_file_path or None,
            chemistry_file_gases=chemistry_file_gases,
            nlayers=self.nlayers,
            filename=self.filename,
            base_directory=self.base_directory
        )
        return (
            f"Taurex emission model run successfully with output file: {self.filename}_emission_spectrum.png "
            f"(wavelength and flux arrays also saved as {self.filename}_em_wavelength.npy "
            f"and {self.filename}_em_spectrum.npy). Volume mixing ratio vs. pressure/altitude profiles (for a VMR 'spaghetti' plot) saved to {self.filename}_vmr_profile.npz."
            + TAUREX_CITATION_NOTICE
        )


def save_vmr_profile(model, base_directory, filename):
    """
    Save per-layer volume mixing ratio (VMR) profiles alongside pressure/altitude, so the agent can make a VMR-vs-altitude ("spaghetti") plot after a forward model run. This data is computed by TauREx during model.build()/model.model() but otherwise discarded, since neither generate_taurex_model() nor generate_taurex_emission_model() plot or save it.

    Uses model.generate_profiles() (same TauREx API SimulateTaurexRetrieval already uses to populate its output .h5's Profiles group) plus model.chemistry.activeGases/inactiveGases for the molecule names matching each row. This mirrors taurex.plot.plotter.Plotter's own built-in VMR plotting methods (plot_forward_xprofile/plot_fit_xprofile), which read exactly these fields from a saved TauREx HDF5 output.

    Saves '{filename}_vmr_profile.npz' with keys:
    - pressure_profile: per-layer pressure, Pa, shape (nlayers,)
    - altitude_profile: per-layer altitude, m, shape (nlayers,)
    - active_mix_profile: VMR of each spectrally-active gas, shape (n_active_gases, nlayers)
    - inactive_mix_profile: VMR of each spectrally-inactive gas (e.g. H2/He fill gases), shape (n_inactive_gases, nlayers)
    - active_gases / inactive_gases: molecule name for each row above, in matching order

    To plot (spaghetti plot, one line per molecule, mixing ratio vs. pressure):
        data = np.load(f'{filename}_vmr_profile.npz')
        for gases, profile in [('active_gases', 'active_mix_profile'), ('inactive_gases', 'inactive_mix_profile')]:
            for name, vmr in zip(data[gases], data[profile]):
                plt.plot(vmr, data['pressure_profile'], label=name)
        plt.xscale('log'); plt.yscale('log'); plt.gca().invert_yaxis()  # pressure decreases upward
    """
    profiles = model.generate_profiles()
    vmr_npz_path = os.path.join(base_directory, f'{filename}_vmr_profile.npz')
    np.savez(
        vmr_npz_path,
        pressure_profile=profiles['pressure_profile'],
        altitude_profile=profiles['altitude_profile'],
        active_mix_profile=profiles['active_mix_profile'],
        inactive_mix_profile=profiles['inactive_mix_profile'],
        active_gases=np.array(model.chemistry.activeGases),
        inactive_gases=np.array(model.chemistry.inactiveGases),
    )
    return vmr_npz_path


def generate_taurex_model(
    star_radius=1.0,  # solar radii
    planet_radius=1.0,  # Jupiter radii
    planet_mass=1.0,  # Jupiter masses
    planet_temp=1500.0,  # Kelvin
    atm_min_pressure=None,
    atm_max_pressure=None,
    molecules=None,
    molecular_abundances=None,
    chemistry_type="free",
    metallicity=1.0,
    co_ratio=0.54,
    element_abundance_source="asplund_2021",
    chemistry_file_path=None,
    chemistry_file_gases=None,
    nlayers=100,
    filename='default_planet',
    base_directory=''):

    """
    Generate a Taurex transmission model with specified parameters.
    To calculate the spectrum, this function needs the planet radius in jupiter radii, planet mass in jupiter masses, and temperature in Kelvin. The spectrum will be plotted and saved as an image file with the given filename: '{filename}_spectrum.png'. The wavelength and spectrum arrays will also be saved as '{filename}_fm_wavelength.npy' and '{filename}_fm_spectrum.npy'.
    Per-layer volume mixing ratio profiles (vs. pressure/altitude, for a VMR "spaghetti" plot) are saved to '{filename}_vmr_profile.npz', see save_vmr_profile()'s docstring for its contents.
    nlayers (default 100) must match chemistry_file_path's row count exactly when chemistry_type='file'.
    Requires opacity files to be properly set via set_opacity_path().
    Please redirect to this website https://taurex3.readthedocs.io/en/latest/ if an error occurs.
    """
    if not atm_min_pressure:
        atm_min_pressure = 1e-1
    if not atm_max_pressure:
        atm_max_pressure = 1e6

    # Create temperature profile (isothermal)
    temperature_profile = Isothermal(T=planet_temp)

    chemistry_1 = build_chemistry(
        molecules, molecular_abundances, chemistry_type, metallicity, co_ratio, element_abundance_source, chemistry_file_path, chemistry_file_gases, nlayers,
    )

    planet = Planet(planet_radius=planet_radius, planet_mass=planet_mass)
    # Star temperature doesn't enter transmission physics (only radius, via Rp/Rs) BlackbodyStar still requires a value, so its own default (5000 K) is used rather than a star_temp parameter.
    star = BlackbodyStar(radius=star_radius)

    model = TransmissionModel(
        planet=planet,
        star=star,
        temperature_profile=temperature_profile,
        chemistry=chemistry_1,
        atm_min_pressure=atm_min_pressure,
        atm_max_pressure=atm_max_pressure,
        nlayers=nlayers
    )
    
    model.add_contribution(AbsorptionContribution())
    model.add_contribution(RayleighContribution())
    model.add_contribution(CIAContribution(cia_pairs=['H2-H2','H2-He']))

    # Build the model
    model.build()

    # Generate the spectrum
    model_result = model.model()

    # extract wavelengths and spectrum
    wavenumbers = model_result[0]
    spectrum = model_result[1]
    wavelengths = 1e4 / wavenumbers  # Convert from cm^-1 to µm

    np.save(os.path.join(base_directory, f'{filename}_fm_wavelength.npy'), wavelengths)
    np.save(os.path.join(base_directory, f'{filename}_fm_spectrum.npy'), spectrum)

    vmr_npz_path = save_vmr_profile(model, base_directory, filename)

    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend
    plt.figure(figsize=(10,6))
    plt.plot(wavelengths, spectrum)
    plt.xlabel('Wavelength (µm)')
    plt.ylabel('Transmission')
    plt.title('Transmission Spectrum')
    plt.xscale('log')
    plt.tight_layout()
    plt.savefig(os.path.join(base_directory, f'{filename}_spectrum.png'))
    # print("Spectrum plot saved as transmission_spectrum.png")
    return (
        f"Successfully generated spectrum plot: {filename}_spectrum.png "
        f"(wavelength and spectrum arrays also saved as {filename}_fm_wavelength.npy and {filename}_fm_spectrum.npy). Volume mixing ratio vs. pressure/altitude profiles saved to {os.path.basename(vmr_npz_path)} see save_vmr_profile()'s docstring for the array layout."
    )


def generate_taurex_emission_model(
    star_radius=1.0,  # solar radii
    star_temp=5500.0,  # Kelvin
    planet_radius=1.0,  # Jupiter radii
    planet_mass=1.0,  # Jupiter masses
    planet_temp=1500.0,  # Kelvin
    atm_min_pressure=None,
    atm_max_pressure=None,
    molecules=None,
    molecular_abundances=None,
    chemistry_type="free",
    metallicity=1.0,
    co_ratio=0.54,
    element_abundance_source="asplund_2021",
    chemistry_file_path=None,
    chemistry_file_gases=None,
    nlayers=100,
    filename='default_planet',
    base_directory=''):

    """
    Generate a Taurex emission model with specified parameters.
    Same setup as the transmission model, but computes the planet's thermal emission spectrum (dayside flux) instead of the transmission spectrum, so it needs a Planet and a BlackbodyStar (star_temp) for the stellar flux normalization, which the transmission model doesn't use. The spectrum will be plotted and saved as an image file with the given filename: '{filename}_emission_spectrum.png'. The native-resolution wavelength/flux arrays will also be saved as '{filename}_em_wavelength.npy' and '{filename}_em_spectrum.npy'.
    Per-layer volume mixing ratio profiles (vs. pressure/altitude, for a VMR "spaghetti" plot) are saved to '{filename}_vmr_profile.npz', see save_vmr_profile()'s docstring for its contents.
    nlayers (default 100) must match chemistry_file_path's row count exactly when chemistry_type='file'.
    Requires opacity files to be properly set via set_opacity_path().
    Please redirect to this website https://taurex3.readthedocs.io/en/latest/ if an error occurs.
    """
    if not atm_min_pressure:
        atm_min_pressure = 1e-1
    if not atm_max_pressure:
        atm_max_pressure = 1e6

    temperature_profile = Isothermal(T=planet_temp)
    chemistry_1 = build_chemistry(
        molecules, molecular_abundances, chemistry_type, metallicity, co_ratio, element_abundance_source, chemistry_file_path, chemistry_file_gases, nlayers,
    )
    planet = Planet(planet_radius=planet_radius, planet_mass=planet_mass)
    star = BlackbodyStar(temperature=star_temp, radius=star_radius)

    model = EmissionModel(
        planet=planet,
        star=star,
        temperature_profile=temperature_profile,
        chemistry=chemistry_1,
        atm_min_pressure=atm_min_pressure,
        atm_max_pressure=atm_max_pressure,
        nlayers=nlayers
    )

    model.add_contribution(AbsorptionContribution())
    model.add_contribution(RayleighContribution())
    model.add_contribution(CIAContribution(cia_pairs=['H2-H2', 'H2-He']))

    model.build()

    # Generate the spectrum at native (full opacity) resolution
    model_result = model.model()

    wavenumbers = model_result[0]
    flux = model_result[1]
    wavelengths = 1e4 / wavenumbers  # Convert from cm^-1 to µm

    np.save(os.path.join(base_directory, f'{filename}_em_wavelength.npy'), wavelengths)
    np.save(os.path.join(base_directory, f'{filename}_em_spectrum.npy'), flux)

    vmr_npz_path = save_vmr_profile(model, base_directory, filename)

    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend
    plt.figure(figsize=(10, 6))
    plt.plot(wavelengths, flux)
    plt.xlabel('Wavelength (µm)')
    plt.ylabel('Flux (native TauREx units)')
    plt.title('Emission Spectrum')
    plt.xscale('log')
    plt.tight_layout()
    plt.savefig(os.path.join(base_directory, f'{filename}_emission_spectrum.png'))
    return (
        f"Successfully generated emission spectrum plot: {filename}_emission_spectrum.png "
        f"(native-resolution wavelength and flux arrays also saved as {filename}_em_wavelength.npy and {filename}_em_spectrum.npy). Volume mixing ratio vs. pressure/altitude profiles saved to {os.path.basename(vmr_npz_path)} see save_vmr_profile()'s docstring for the array layout."
    )