import os
import numpy as np
import pandas as pd
from tqdm import tqdm
import matplotlib.pyplot as plt
from astropy.io import ascii
import ast

from orchestral.tools.filesystem.filesystem_tools import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

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


def build_chemistry(molecules=None, molecular_abundances=None, chemistry_type="free", metallicity=1.0, co_ratio=0.54):
    """
    Build a taurex chemistry object, shared by the transmission and emission forward models.

    Precedence: explicit molecular_abundances > named molecules (looked up in the fixed
    default table) > fixed basic model (5 default molecules). Only used for chemistry_type='free';
    chemistry_type='equilibrium' uses ACEChemistry (metallicity/co_ratio) instead and ignores
    molecules/molecular_abundances entirely.
    """
    if chemistry_type == "equilibrium":
        if molecules is not None or molecular_abundances is not None:
            raise ValueError(
                "chemistry_type='equilibrium' ignores 'molecules'/'molecular_abundances'; "
                "remove them or set chemistry_type='free' to use fixed mixing ratios instead."
            )
        from acepython.taurex3 import ACEChemistry
        return ACEChemistry(metallicity=metallicity, co_ratio=co_ratio)

    if chemistry_type != "free":
        raise ValueError(f"Unknown chemistry_type: {chemistry_type!r}. Must be 'free' or 'equilibrium'.")

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
    """Run a Taurex forward model with specified parameters and generate a transmission spectrum plot."""

    star_radius: float = RuntimeField(description="Radius of the star in solar radii", default=1.0)
    planet_radius: float = RuntimeField(description="Radius of the planet in Jupiter radii", default=1.0)
    planet_mass: float = RuntimeField(description="Mass of the planet in Jupiter masses", default=1.0)
    planet_temp: float = RuntimeField(description="Temperature of the planet in Kelvin", default=1500.0)
    atm_min_pressure: float | None = RuntimeField(description="Minimum atmospheric pressure in Pascal (pressure at the highest simulated layer)", default=1e-1)
    atm_max_pressure: float | None = RuntimeField(description="Maximum atmospheric pressure in Pascal (pressure at the lowest simulated layer)", default=1e6)
    molecules: list[str] | str = RuntimeField(
        description="List of molecule names to include, e.g. ['H2O', 'CH4'] or \"['H2O', 'CH4']\". Use this when the user asks for specific molecules. Ignored if molecular_abundances is provided. Not used in 'equilibrium' chemistry_type. Leave as an empty string if not specifying molecules.",
        default=""
    )
    molecular_abundances: dict | str = RuntimeField(
        description="Dictionary of molecule names to exact mixing ratios. Can be a Python dict or string representation. Example: {'H2O': 0.02, 'CH4': 0.001} or \"{'H2O': 0.02, 'CH4': 0.001}\". Use this only when the user specifies exact abundance values. Takes precedence over 'molecules'. If neither is provided, uses the fixed basic model (H2O, CH4, CO2, CO, NH3 at default abundances). Not used in 'equilibrium' chemistry_type. Leave as an empty string if not specifying exact abundances.",
        default=""
    )
    chemistry_type: str = RuntimeField(
        description="'free' (default) sets fixed molecule mixing ratios via 'molecules'/'molecular_abundances' (or the basic model if neither is given). 'equilibrium' instead uses ACEChemistry to compute abundances from thermochemical equilibrium given 'metallicity' and 'co_ratio' only set this to 'equilibrium' if the user explicitly asks for equilibrium/ACE chemistry.",
        default="free"
    )
    metallicity: float = RuntimeField(
        description="Metallicity relative to solar, only used when chemistry_type='equilibrium'.",
        default=1.0
    )
    co_ratio: float = RuntimeField(
        description="Carbon-to-oxygen ratio, only used when chemistry_type='equilibrium'. Solar value is ~0.54.",
        default=0.54
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
            filename=self.filename,
            base_directory=self.base_directory
        )
        return (
            f"Taurex model run successfully with output file: {self.filename}_spectrum.png "
            f"(wavelength and spectrum arrays also saved as {self.filename}_fm_wavelength.npy "
            f"and {self.filename}_fm_spectrum.npy)"
        )


class RunTaurexEmissionModelTool(BaseTool):
    """Run a Taurex forward model with specified parameters and generate an emission spectrum plot."""

    star_radius: float = RuntimeField(description="Radius of the star in solar radii", default=1.0)
    star_temp: float = RuntimeField(description="Temperature of the star in Kelvin (blackbody), used for stellar flux normalization", default=5500.0)
    planet_radius: float = RuntimeField(description="Radius of the planet in Jupiter radii", default=1.0)
    planet_mass: float = RuntimeField(description="Mass of the planet in Jupiter masses", default=1.0)
    planet_temp: float = RuntimeField(description="Temperature of the planet in Kelvin", default=1500.0)
    atm_min_pressure: float | None = RuntimeField(description="Minimum atmospheric pressure in Pascal (pressure at the highest simulated layer)", default=1e-1)
    atm_max_pressure: float | None = RuntimeField(description="Maximum atmospheric pressure in Pascal (pressure at the lowest simulated layer)", default=1e6)
    molecules: list[str] | str = RuntimeField(
        description="List of molecule names to include, e.g. ['H2O', 'CH4'] or \"['H2O', 'CH4']\". Use this when the user asks for specific molecules. Ignored if molecular_abundances is provided. Not used in 'equilibrium' chemistry_type. Leave as an empty string if not specifying molecules.",
        default=""
    )
    molecular_abundances: dict | str = RuntimeField(
        description="Dictionary of molecule names to exact mixing ratios. Can be a Python dict or string representation. Example: {'H2O': 0.02, 'CH4': 0.001} or \"{'H2O': 0.02, 'CH4': 0.001}\". Use this only when the user specifies exact abundance values. Takes precedence over 'molecules'. If neither is provided, uses the fixed basic model (H2O, CH4, CO2, CO, NH3 at default abundances). Not used in 'equilibrium' chemistry_type. Leave as an empty string if not specifying exact abundances.",
        default=""
    )
    chemistry_type: str = RuntimeField(
        description="'free' (default) sets fixed molecule mixing ratios via 'molecules'/'molecular_abundances' (or the basic model if neither is given). 'equilibrium' instead uses ACEChemistry to compute abundances from thermochemical equilibrium given 'metallicity' and 'co_ratio' only set this to 'equilibrium' if the user explicitly asks for equilibrium/ACE chemistry.",
        default="free"
    )
    metallicity: float = RuntimeField(
        description="Metallicity relative to solar, only used when chemistry_type='equilibrium'.",
        default=1.0
    )
    co_ratio: float = RuntimeField(
        description="Carbon-to-oxygen ratio, only used when chemistry_type='equilibrium'. Solar value is ~0.54.",
        default=0.54
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
            filename=self.filename,
            base_directory=self.base_directory
        )
        return (
            f"Taurex emission model run successfully with output file: {self.filename}_emission_spectrum.png "
            f"(wavelength and flux arrays also saved as {self.filename}_em_wavelength.npy "
            f"and {self.filename}_em_spectrum.npy)"
        )


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
    filename='default_planet',
    base_directory=''):

    """
    Generate a Taurex transmission model with specified parameters. 
    To calculate the spectrum, this function needs the planet radius in jupiter radii,
    planet mass in jupiter masses, and temperature in Kelvin.
    The spectrum will be plotted and saved as an image file with the given filename: '{filename}_spectrum.png'. The wavelength and spectrum arrays will also be saved as '{filename}_fm_wavelength.npy' and '{filename}_fm_spectrum.npy'.
    Requires opacity files to be properly set via set_opacity_path().
    Please redirect to this website https://taurex3.readthedocs.io/en/latest/ if an error occurs.
    """
    if not atm_min_pressure:
        atm_min_pressure = 1e-1
    if not atm_max_pressure:
        atm_max_pressure = 1e6

    # Create temperature profile (isothermal)
    temperature_profile = Isothermal(T=planet_temp)

    chemistry_1 = build_chemistry(molecules, molecular_abundances, chemistry_type, metallicity, co_ratio)

    model = TransmissionModel(
        temperature_profile=temperature_profile,
        chemistry=chemistry_1,
        atm_min_pressure=atm_min_pressure,
        atm_max_pressure=atm_max_pressure
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
        f"(wavelength and spectrum arrays also saved as {filename}_fm_wavelength.npy "
        f"and {filename}_fm_spectrum.npy)"
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
    filename='default_planet',
    base_directory=''):

    """
    Generate a Taurex emission model with specified parameters.
    Same setup as the transmission model, but computes the planet's thermal emission spectrum
    (dayside flux) instead of the transmission spectrum, so it needs a Planet and a BlackbodyStar
    (star_temp) for the stellar flux normalization, which the transmission model doesn't use.
    The spectrum will be plotted and saved as an image file with the given filename: '{filename}_emission_spectrum.png'.
    The native-resolution wavelength/flux arrays will also be saved as '{filename}_em_wavelength.npy' and '{filename}_em_spectrum.npy'.
    Requires opacity files to be properly set via set_opacity_path().
    Please redirect to this website https://taurex3.readthedocs.io/en/latest/ if an error occurs.
    """
    if not atm_min_pressure:
        atm_min_pressure = 1e-1
    if not atm_max_pressure:
        atm_max_pressure = 1e6

    temperature_profile = Isothermal(T=planet_temp)
    chemistry_1 = build_chemistry(molecules, molecular_abundances, chemistry_type, metallicity, co_ratio)
    planet = Planet(planet_radius=planet_radius, planet_mass=planet_mass)
    star = BlackbodyStar(temperature=star_temp, radius=star_radius)

    model = EmissionModel(
        planet=planet,
        star=star,
        temperature_profile=temperature_profile,
        chemistry=chemistry_1,
        atm_min_pressure=atm_min_pressure,
        atm_max_pressure=atm_max_pressure,
        nlayers=100
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
        f"(native-resolution wavelength and flux arrays also saved as {filename}_em_wavelength.npy "
        f"and {filename}_em_spectrum.npy)"
    )