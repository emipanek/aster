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
from taurex.model import TransmissionModel
from taurex.data.profiles.temperature import Isothermal
from taurex.planet import Planet
from taurex.stellar import BlackbodyStar
from taurex.chemistry import TaurexChemistry
from taurex.chemistry import ConstantGas
from taurex.contributions import AbsorptionContribution, RayleighContribution, CIAContribution
from taurex.data.spectrum.observed import ObservedSpectrum
from taurex.optimizer.nestle import NestleOptimizer
from taurex.optimizer.multinest import MultiNestOptimizer



class RunTaurexModelTool(BaseTool):
    """Run a Taurex forward model with specified parameters and generate a transmission spectrum plot."""

    star_radius: float = RuntimeField(description="Radius of the star in solar radii", default=1.0)
    planet_radius: float = RuntimeField(description="Radius of the planet in Jupiter radii", default=1.0)
    planet_mass: float = RuntimeField(description="Mass of the planet in Jupiter masses", default=1.0)
    orbital_period: float = RuntimeField(description="Orbital period of the planet in days", default=10.0)
    semi_major_axis: float = RuntimeField(description="Semi-major axis of the planet's orbit in AU", default=0.05)
    planet_temp: float = RuntimeField(description="Temperature of the planet in Kelvin", default=1500.0)
    atm_min_pressure: float | None = RuntimeField(description="Minimum atmospheric pressure in bar (pressure at the highest simulated layer)", default=1e-3)
    atm_max_pressure: float | None = RuntimeField(description="Maximum atmospheric pressure in bar (pressure at the lowest simulated layer)", default=1e5)
    molecular_abundances: dict | str | None = RuntimeField(
        description="Dictionary of molecule names to mixing ratios. Can be a Python dict or string representation. Example: {'H2O': 0.02, 'CH4': 0.001} or \"{'H2O': 0.02, 'CH4': 0.001}\". If not provided, uses default values.",
        default=None
    )
    filename: str = RuntimeField(description="The output file will be saved as '{filename}_spectrum.png'", default='')
    base_directory: str = StateField()

    def _run(self):
        # Parse molecular_abundances if it's a string
        molecular_abundances = self.molecular_abundances
        if isinstance(molecular_abundances, str):
            try:
                molecular_abundances = ast.literal_eval(molecular_abundances)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to parse molecular_abundances string: {molecular_abundances}. Error: {e}")

        generate_taurex_model(
            star_radius=self.star_radius,
            planet_radius=self.planet_radius,
            planet_mass=self.planet_mass,
            orbital_period=self.orbital_period,
            semi_major_axis=self.semi_major_axis,
            planet_temp=self.planet_temp,
            atm_min_pressure=self.atm_min_pressure,
            atm_max_pressure=self.atm_max_pressure,
            molecular_abundances=molecular_abundances,
            filename=self.filename,
            base_directory=self.base_directory
        )
        return f"Taurex model run successfully with output file: {self.filename}_spectrum.png"



def generate_taurex_model(
    star_radius=1.0,  # solar radii
    planet_radius=1.0,  # Jupiter radii
    planet_mass=1.0,  # Jupiter masses
    orbital_period=10.0,  # days
    semi_major_axis=0.05,  # AU
    planet_temp=1500.0,  # Kelvin
    atm_min_pressure=None,
    atm_max_pressure=None,
    molecular_abundances=None,
    filename='default_planet',
    base_directory=''):

    """
    Generate a Taurex transmission model with specified parameters. 
    To calculate the spectrum, this function needs the planet radius in jupiter radii,
    planet mass in jupiter masses, and temperature in Kelvin.
    The spectrum will be plotted and saved as an image file with the given filename: '{filename}_spectrum.png'.
    Requires opacity files to be properly set via set_opacity_path().
    Please redirect to this website https://taurex3.readthedocs.io/en/latest/ if an error occurs.
    """
    if not atm_min_pressure:
        atm_min_pressure = 1e-3
    if not atm_max_pressure:
        atm_max_pressure = 1e5
    
    # Create temperature profile (isothermal)
    temperature_profile = Isothermal(T=planet_temp)
    
    # Create chemistry with background gases
    chemistry_1 = TaurexChemistry(fill_gases=['H2', 'He'], ratio=[0.17])

    # Add specific gas molecules
    if molecular_abundances is None:
        # Default molecular abundances
        molecules = [
            ('H2O', 0.02),
            ('CH4', 0.001),
            ('CO2', 0.0001),
            ('CO', 0.001),
            ('NH3', 0.0001)
        ]
    else:
        # Use user-provided molecular abundances
        molecules = list(molecular_abundances.items())

    for molecule, mix_ratio in molecules:
        chemistry_1.addGas(ConstantGas(molecule, mix_ratio=mix_ratio))

    # Example for different chemistry setup, using an equilibrium chemistry code 
    # from acepython.taurex3 import ACEChemistry
    # chemistry_2 = ACEChemistry(metallicity=1, co_ratio=0.1)
    # and then chemistry_2 would be used in the model instead of chemistry_1
    
    # Create transmission model
    model = TransmissionModel(
        temperature_profile=temperature_profile,
        chemistry=chemistry_1,
        atm_min_pressure=atm_min_pressure,
        atm_max_pressure=atm_max_pressure
    )
    
    # Add absorption and Rayleigh contributions
    model.add_contribution(AbsorptionContribution())
    model.add_contribution(RayleighContribution())
    model.add_contribution(CIAContribution(cia_pairs=['H2-H2','H2-He']))
    
    # Build the model
    model.build()

    # Generate the spectrum
    model_result = model.model()

    # Attempt to extract wavelengths and spectrum
    wavenumbers = model_result[0]
    spectrum = model_result[1]
    wavelengths = 1e4 / wavenumbers  # Convert from cm^-1 to µm

    np.save(os.path.join(base_directory, 'fm_wavelength.npy'), wavelengths)
    np.save(os.path.join(base_directory, 'fm_spectrum.npy'), spectrum)

    # Plot the spectrum
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
    return f"Successfully generated spectrum plot: {filename}_spectrum.png"