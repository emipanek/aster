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
import requests
from astropy.io import ascii
import pandas as pd
import csv
import io
import json
from tqdm import tqdm
import corner

def ensure_opacity_and_cia_paths(opa_path=None, cia_path=None, ask_user=True):
    """
    Ensure opacity AND CIA paths are set correctly in Taurex.
    
    Checks if paths are set.
    Verifies the folders exist and contain valid files.
    Sets paths with OpacityCache and CIACache

    opa_path : str or None Optional path to set for opacity files.
    cia_path : str or None Optional path to set for CIA files.
    ask_user : bool Whether to ask user for paths if missing.

    Please redirect to this website https://taurex3.readthedocs.io/en/latest/ if an error occurs.
    """
    # try existing Taurex paths
    #cache = GlobalCache()
    #opa_path = cache["xsec_path"] 
    #cia_path = cache["cia_path"]

    if opa_path is None:
        print(f"Sacrebleu! No opacity path is set.")
        #opa_path = input("Enter a valid opacity path: ").strip()

    if cia_path is None:
        print(f"Sacrebleu! No CIA path is set.")
        #cia_path = input("Enter a valid CIA path: ").strip()

    # Set the paths 
    OpacityCache().set_opacity_path(opa_path)
    CIACache().set_cia_path(cia_path)

    print(f"Opacity path set successfully:\n{opa_path}!")
    print(f"CIA path set successfully:\n{cia_path}!")

    return opa_path, cia_path


def generate_taurex_model(
    star_radius=1.0,  # solar radii
    planet_radius=1.0,  # Jupiter radii
    planet_mass=1.0,  # Jupiter masses
    orbital_period=10.0,  # days
    semi_major_axis=0.05,  # AU
    planet_temp=1500.0,  # Kelvin
    atm_min_pressure=1e-3,
    atm_max_pressure=1e5,
    filename='default_planet',
    workspace_directory=''):

    """
    Generate a Taurex transmission model with specified parameters. 
    To calculate the spectrum, this function needs the planet radius in jupiter radii,
    planet mass in jupiter masses, and temperature in Kelvin.
    The spectrum will be plotted and saved as an image file with the given filename: '{filename}_spectrum.png'.
    Requires opacity files to be properly set via set_opacity_path().
    Please redirect to this website https://taurex3.readthedocs.io/en/latest/ if an error occurs.
    """
    
    # Create temperature profile (isothermal)
    temperature_profile = Isothermal(T=planet_temp)
    
    # Create chemistry with background gases
    chemistry_1 = TaurexChemistry(fill_gases=['H2', 'He'], ratio=[0.17])
    
    # Add specific gas molecules
    molecules = [
        ('H2O', 0.02),
        ('CH4', 0.001),
        ('CO2', 0.0001),
        ('CO', 0.001),
        ('NH3', 0.0001)
    ]
    
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

    np.save(os.path.join(workspace_directory, 'fm_wavelength.npy'), wavelengths)
    np.save(os.path.join(workspace_directory, 'fm_spectrum.npy'), spectrum)

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
    plt.savefig(os.path.join(workspace_directory, f'{filename}_spectrum.png'))
    # print("Spectrum plot saved as transmission_spectrum.png")
    return f"Successfully generated spectrum plot: {filename}_spectrum.png"


def run_taurex_retrieval(
    observation_path,
    optimizer="nestle",
    fit_params=None, 
    bounds=None, 
    # to build a model 
    star_radius=1.0,  # solar radii
    star_temp=5500.0,  # Kelvin
    planet_radius=1.0,  # Jupiter radii
    planet_mass=1.0,  # Jupiter masses
    planet_temp=1500.0,
    atm_min_pressure=1e-3, # dont really change these bounds for now
    atm_max_pressure=1e5,
    nlayers=100,
    workspace_directory=''):
    """
    observation_path : path to the observed spectrum file (e.g., 'path/to/test_data.dat')
    optimizer : 'nestle' Which optimizer to use. can also use multinest, but for now nestle is the standard.
    fit_params : ['planet_radius','T']
    bounds : dict[str, [low, high]]
    """

    # Build a simple model 
    planet = Planet(planet_radius=planet_radius, planet_mass=planet_mass)
    star = BlackbodyStar(temperature=star_temp, radius=star_radius)
    temperature_profile = Isothermal(T=planet_temp)

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

    # Example for different chemistry setup, using an equilibrium chemistry code 
    # from acepython.taurex3 import ACEChemistry
    # chemistry_2 = ACEChemistry(metallicity=1, co_ratio=0.1)
    # and then chemistry_2 would be used in the model instead of chemistry
    
    model = TransmissionModel(planet=planet,
            temperature_profile=temperature_profile,
            chemistry=chemistry,
            star=star,
            atm_min_pressure=atm_min_pressure,
            atm_max_pressure=atm_max_pressure,
            nlayers = nlayers
    )
    
    model.add_contribution(AbsorptionContribution())
    model.add_contribution(RayleighContribution())
    model.add_contribution(CIAContribution(cia_pairs=['H2-H2','H2-He']))
    model.build()
    model.model()

    # Load observations
    obs = ObservedSpectrum(observation_path)
    obin = obs.create_binner()  # used to bin the model onto the obs grid

    # Build optimizer
    # Keep it simple default num_live_points=50 
    opt = NestleOptimizer(num_live_points=50)
    # example using the multinest optimizer
    # opt = MultiNestOptimizer(num_live_points=50, num_live_points = 200, multi_nest_path = ./multinest, search_multi_modes = True, resume = False, importance_sampling = False)

    opt.set_model(model)
    opt.set_observed(obs)

    for p in fit_params:
        opt.enable_fit(p)
        if p in bounds and isinstance(bounds[p], (list, tuple)) and len(bounds[p]) == 2:
            opt.set_boundary(p, list(bounds[p]))

    # Run retrieval
    solution = opt.fit()
    taurex.log.disableLogging()

    # Grab the best solution, update model, make a quick plot and save it
    best_map = None
    best_value = None
    for soln, optimized_map, optimized_value, values in opt.get_solution():
        best_map = optimized_map
        best_value = optimized_value
        # Update model to best parameters
        opt.update_model(optimized_map)

    # Safety check: if nothing came back
    if best_map is None:
        raise RuntimeError("Retrieval finished but no solution was returned.")

    # Plot observed vs binned best-fit model
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
    plt.savefig(os.path.join(workspace_directory, "retrieval_fit.png"), dpi=200)

    np.save(os.path.join(workspace_directory, 'retrieved_wavelength.npy'), model_wl)
    np.save(os.path.join(workspace_directory, 'retrieved_spectrum.npy'), model_binned)

    #grab posterior samples + weights from the optimizer
    samples = opt.get_samples(0)
    weights = opt.get_weights(0)
    labels = opt.fit_names

    np.save(os.path.join(workspace_directory, 'retrieval_samples.npy'), samples)
    np.save(os.path.join(workspace_directory, 'retrieval_weights.npy'), weights)

    # sanity checks
    #print(samples.shape, weights.shape, len(labels))
    #print("weights sum:", np.sum(weights))
    fig = corner.corner(
        samples,
        weights=weights,
        labels=labels,
        quantiles=[0.16, 0.5, 0.84],
        show_titles=True,
        title_fmt=".3g",
    )
    plt.savefig(os.path.join(workspace_directory, "retrieval_corner.png"), dpi=200)
    plt.show()


    return {
        'best_parameters': best_map,        
        'best_value': best_value,
        'optimizer': optimizer.lower()
    }


TAP_SYNC_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"

def get_exoplanet_params_tap(planet_name, columns, table="pscomppars"):
    """
    Query NASA Exoplanet Archive TAP service for one or multiple parameters.
    planet_name "Kepler-10 b"
    "pl_rade" or ["pl_rade", "pl_bmasse"]
    TAP table name, here ps for Planetary Systems = All confirmed planets (and hosts) in the archive
    with parameters derived from a single, published reference that are designated as the archive's default parameter set
    can also put "pscomppars" for composite parameters from multiple references.
    """

    # Convert single string to list
    if isinstance(columns, str):
        columns = [columns]

    # Build ADQL SELECT clause
    select_clause = ", ".join(columns)

    # Build query
    adql = (
        f"SELECT {select_clause} "
        f"FROM {table} "
        f"WHERE pl_name = '{planet_name}'"
    )

    # Call TAP sync
    params = {
        "query": adql,
        "format": "csv"
    }

    response = requests.get(TAP_SYNC_URL, params=params)
    response.raise_for_status()

    # Parse CSV in memory
    csv_file = io.StringIO(response.text)
    reader = csv.DictReader(csv_file)

    row = next(reader, None)
    if row is None:
        return None

    # Return results as a dictionary
    return {col: row[col] for col in columns}


def process_wgets_file(wgets_file_path: str, data_path: str) -> None:
    # Read the wgets file and build a dictionary mapping planet names to their data file URLs
    with open(wgets_file_path, 'r') as file:
        lines = file.readlines()

    names_to_urls = {}
    manifest_url = None  # keep this in case needed it later

    for line in lines:
        line = line.strip()  # Remove leading/trailing whitespace and newline characters
        _, _, file_name, url = line.split(' ')
        planet_name = file_name.split('.')[0]

        if planet_name == 'spectra':
            # do we need this?
            manifest_url = url
            continue

        if planet_name not in names_to_urls:
            names_to_urls[planet_name] = [url]
        else:
            names_to_urls[planet_name].append(url)

    print('Found data files for planets:')
    for i, name in enumerate(names_to_urls, start=1):
        print(f'{i:4} - {name:16}: With {len(names_to_urls[name])} file(s)')

    # Fetch and save planet data
    for planet_name, urls in names_to_urls.items():
        planet_dir = os.path.join(data_path, planet_name)
        os.makedirs(planet_dir, exist_ok=True)

        for url in urls:
            response = requests.get(url)
            response.raise_for_status()

            contents = response.text
            tbl = ascii.read(contents, format="ipac")
            df = tbl.to_pandas()

            file_name = url.split('/')[-1]
            file_path = os.path.join(planet_dir, file_name.replace('.tbl', '.csv'))
            df.to_csv(file_path, index=False)
            print(f'Saved data for {planet_name} to {file_path}')


def process_downloads(data_path: str, output_dir: str) -> None:
    def extract_meaningful_data(df):
        # Essential spectral data
        central_wavelength = df.CENTRALWAVELNG.values
        transit_depth = df.PL_TRANDEP.values / 100  # Modulation fraction (not in percent)
        transit_depth_error = df.PL_TRANDEPERR1.values / 100  # Error in modulation fraction

        #spec = pd.DataFrame({
        #    'central_wavelength': central_wavelength,
        #    'transit_depth': transit_depth,
        #    'transit_depth_error': transit_depth_error,
        #})

        spec = np.column_stack([
                    central_wavelength,
                    transit_depth,
                    transit_depth_error
                ])

        # Metadata
        authors = df.PL_TRANDEP_AUTHORS.values[0]
        url = df.PL_TRANDEP_URL.values[0]
        facility = 'JWST'
        instrument = 'NIRSpec'

        metadata = {
            'authors': authors,
            'url': url,
            'facility': facility,
            'instrument': instrument,
            'units': {
                'central_wavelength': '[μm]',
                'transit_depth': 'fraction: [R_P^2/R_S^2]',
                'transit_depth_error': 'fraction: [R_P^2/R_S^2]',
            }
        }

        return spec, metadata

    def extract_name(file_name):
        """Given a file name like 'WASP_39_b_3.11466_5077_1.csv' extract 'WASP_39_b_3.11466_5077_1'"""
        return file_name.replace('.csv', '')

    planets = os.listdir(data_path)
    for planet in tqdm(planets):
        planet_dir = os.path.join(data_path, planet)
        if not os.path.isdir(planet_dir):
            continue  # skip non-directories, just in case

        observations = os.listdir(planet_dir)

        for obs_file in observations:
            obs_name = extract_name(obs_file)
            df = pd.read_csv(os.path.join(data_path, planet, obs_file))

            spec, metadata = extract_meaningful_data(df)

            # Create the output directories if they don't exist
            os.makedirs(os.path.join(output_dir, planet, obs_name), exist_ok=True)

            # Save the spectrum data
            # spec.to_csv(
            #     os.path.join(output_dir, planet, obs_name, 'spectrum.dat'),
            #     index=False
            # )
            #np.save(spec.to_numpy(), os.path.join(output_dir, planet, obs_name, 'spectrum.npy'))
            np.savetxt(
                os.path.join(output_dir, planet, obs_name, "spectrum.dat"),
                spec,
                fmt="%.10e"
            )


            # Save the metadata
            with open(os.path.join(output_dir, planet, obs_name, 'metadata.json'), 'w') as f:
                json.dump(metadata, f, indent=4)


