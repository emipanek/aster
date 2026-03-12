import os
import json
import io
import csv
import requests
import numpy as np
import pandas as pd
from astropy.io import ascii
from tqdm import tqdm

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField


def process_wgets_file(base_directory, wgets_file_path: str, data_path: str) -> None:
    # Read the wgets file and build a dictionary mapping planet names to their data file URLs
    full_wgets_path = os.path.join(base_directory, wgets_file_path)
    with open(full_wgets_path, 'r') as file:
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
        planet_dir = os.path.join(base_directory, data_path, planet_name)
        os.makedirs(planet_dir, exist_ok=True)

        for url in urls:
            response = requests.get(url)
            response.raise_for_status()

            contents = response.text
            tbl = ascii.read(contents, format="ipac")
            df = tbl.to_pandas()      # type: ignore

            file_name = url.split('/')[-1]
            file_path = os.path.join(planet_dir, file_name.replace('.tbl', '.csv'))
            df.to_csv(file_path, index=False)
            print(f'Saved data for {planet_name} to {file_path}')


def process_downloads(base_directory, data_path: str, output_dir: str) -> None:
    '''
    Process the downloaded data files, extract meaningful spectral data and metadata, and save them in a structured format.
    Parameters:
    - base_directory: The base directory for the project.
    - data_path: The relative path to the base_directory containing the raw downloaded data files.
    - output_dir: The relative path to the base_directory where the processed data will be saved.
    Note that the agent never needs to think about the base_directory, thus the need for data and output paths to be relative paths.
    '''
    def extract_meaningful_data(df):
        # Essential spectral data
        central_wavelength = df.CENTRALWAVELNG.values

        # Check if this is transit or eclipse data
        if 'PL_TRANDEP' in df.columns:
            # Transit observation
            transit_depth = df.PL_TRANDEP.values / 100  # Modulation fraction (not in percent)
            transit_depth_error = df.PL_TRANDEPERR1.values / 100  # Error in modulation fraction
            authors = df.PL_TRANDEP_AUTHORS.values[0]
            url = df.PL_TRANDEP_URL.values[0]
            observation_type = 'transit'
        elif 'ESPECLIPDEP' in df.columns:
            # Eclipse observation
            raise ValueError('This sample contains an Eclipse observation rather than a Transit observation. Please filter for only transit observations on the Exoplanet Archive website and re-download the data. ')
            # transit_depth = df.ESPECLIPDEP.values / 100  # Modulation fraction (not in percent)
            # transit_depth_error = df.ESPECLIPDEPERR1.values / 100  # Error in modulation fraction
            # authors = df.ESPECLIPDEP_AUTHORS.values[0] if 'ESPECLIPDEP_AUTHORS' in df.columns else 'Unknown'
            # url = df.ESPECLIPDEP_URL.values[0] if 'ESPECLIPDEP_URL' in df.columns else 'Unknown'
            # observation_type = 'eclipse'
        else:
            raise ValueError(f"Cannot find transit depth column in  dataframe. Available columns: {df.columns.tolist()}")

        spec = np.column_stack([
                    central_wavelength,
                    transit_depth,
                    transit_depth_error
                ])

        # Metadata
        facility = 'JWST'
        instrument = 'NIRSpec'

        metadata = {
            'observation_type': observation_type,
            'authors': authors,
            'url': url,
            'facility': facility,
            'instrument': instrument,
            'units': {
                'central_wavelength': '[μm]',
                'transit_depth': 'fraction: [R_P^2/R_S^2]' if observation_type == 'transit' else 'fraction: eclipse depth',
                'transit_depth_error': 'fraction: [R_P^2/R_S^2]' if observation_type == 'transit' else 'fraction: eclipse depth',
            }
        }

        return spec, metadata

    def extract_name(file_name):
        """Given a file name like 'WASP_39_b_3.11466_5077_1.csv' extract 'WASP_39_b_3.11466_5077_1'"""
        return file_name.replace('.csv', '')

    planets = os.listdir(os.path.join(base_directory, data_path))
    for planet in tqdm(planets):
        planet_dir = os.path.join(base_directory, data_path, planet)
        if not os.path.isdir(planet_dir):
            continue  # skip non-directories, just in case

        observations = os.listdir(planet_dir)

        for obs_file in observations:
            obs_name = extract_name(obs_file)
            df = pd.read_csv(os.path.join(base_directory, data_path, planet, obs_file))

            spec, metadata = extract_meaningful_data(df)

            # Create the output directories if they don't exist
            os.makedirs(os.path.join(base_directory, output_dir, planet, obs_name), exist_ok=True)

            # Save the spectrum data
            # spec.to_csv(
            #     os.path.join(output_dir, planet, obs_name, 'spectrum.dat'),
            #     index=False
            # )
            #np.save(spec.to_numpy(), os.path.join(output_dir, planet, obs_name, 'spectrum.npy'))
            np.savetxt(
                os.path.join(base_directory, output_dir, planet, obs_name, "spectrum.dat"),
                spec,
                fmt="%.10e"
            )


            # Save the metadata
            with open(os.path.join(base_directory, output_dir, planet, obs_name, 'metadata.json'), 'w') as f:
                json.dump(metadata, f, indent=4)


# NASA Exoplanet Archive TAP service URL
TAP_SYNC_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"


def get_exoplanet_params_tap(planet_name: str, columns: list | str, table: str = "pscomppars") -> dict | None:
    """
    Query NASA Exoplanet Archive TAP service for one or multiple parameters.

    Parameters:
    - planet_name: Name of the exoplanet (e.g., "Kepler-10 b")
    - columns: List of parameter columns to retrieve or single column name string
    - table: TAP table name, "pscomppars" (default) for composite parameters or "ps" for all parameters

    Returns:
    - Dictionary with requested parameters or None if planet not found
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


class GetExoplanetParameters(BaseTool):
    """
    Get exoplanet parameters from NASA Exoplanet Archive via TAP service.

    This tool allows programmatic access to exoplanet data from the NASA Exoplanet Archive.
    Uses the "pscomppars" table by default, which provides composite parameters for confirmed
    exoplanets combining data from multiple sources. Note that parameters may not be self-consistent
    as they come from different sources.

    IMPORTANT: Users should cite the archive's DOI if they use any datasets for research.
    """

    planet_name: str = RuntimeField(
        description="Name of the exoplanet (e.g., 'Kepler-10 b'), must match exactly with the archive."
    )
    columns: list = RuntimeField(
        default=["pl_radj", "pl_bmassj", "pl_eqt", "st_rad", "st_teff", "st_mass"],
        description="""List of parameter columns to retrieve. Common columns include:
        - pl_name: Planet name
        - hostname: Stellar name
        - pl_orbper: Orbital Period [days]
        - pl_orbsmax: Semi-major Axis [AU]
        - pl_rade: Planet Radius [Earth Radii]
        - pl_radj: Planet Radius [Jupiter Radii]
        - pl_bmasse: Planet Mass [Earth Masses]
        - pl_bmassj: Planet Mass [Jupiter Masses]
        - pl_eqt: Planet Equilibrium Temperature [K]
        - st_rad: Star Radius [Solar Radii]
        - st_teff: Star Temperature [K]
        - st_mass: Star Mass [Solar Masses]
        - st_met: Stellar Metallicity [dex]
        - st_logg: Stellar surface gravity [log10(cm/s^2)]

        Full list at: https://exoplanetarchive.ipac.caltech.edu/docs/API_PS_columns.html"""
    )
    table: str = RuntimeField(
        default="pscomppars",
        description="TAP table name: 'pscomppars' (composite parameters) or 'ps' (all parameter sets)"
    )

    def _run(self) -> str:
        """Query NASA Exoplanet Archive and return formatted results."""
        result = get_exoplanet_params_tap(
            planet_name=self.planet_name,
            columns=self.columns,
            table=self.table
        )

        if result is None:
            return f"No data found for planet '{self.planet_name}' in table '{self.table}'"

        # Format output
        output = f"Parameters for {self.planet_name}:\n\n"
        for param, value in result.items():
            output += f"  {param}: {value}\n"

        output += "\n⚠️ CITATION REQUIRED: If using this data for research, cite the NASA Exoplanet Archive DOI."

        return output


class DownloadDataset(BaseTool):
    """
    Download transit spectra from NASA Exoplanet Archive and reformat them.

    This tool processes a wgets file containing URLs to exoplanet spectra, downloads the data,
    and saves it in a structured format ready for retrieval analysis.

    To get the wgets file:
    1. Go to https://exoplanetarchive.ipac.caltech.edu/cgi-bin/atmospheres/nph-firefly
    2. Filter by instrument and planet(s)
    3. Check boxes for desired spectra
    4. Click "Download all checked spectra"
    5. Copy wget commands for .tbl files to a text file
    """

    wgets_file_path: str = RuntimeField(
        description="Path to the wgets text file containing URLs (relative to base_directory)"
    )
    raw_data_path: str = RuntimeField(
        default="tmp/raw_data",
        description="Directory to store intermediate CSV files (relative to base_directory)"
    )
    processed_data_path: str = RuntimeField(
        default="tmp/processed_data",
        description="Directory where final spectrum.dat and metadata.json will be saved (relative to base_directory)"
    )

    base_directory: str = StateField()

    def _run(self) -> str:
        """Download and process spectra from NASA archive."""
        # Process wget file to download data
        process_wgets_file(
            base_directory=self.base_directory,
            wgets_file_path=self.wgets_file_path,
            data_path=self.raw_data_path
        )

        # Process downloads to extract spectral data
        process_downloads(
            base_directory=self.base_directory,
            data_path=self.raw_data_path,
            output_dir=self.processed_data_path
        )

        return (
            f"Download complete!\n\n"
            f"Raw CSV data saved in: {self.raw_data_path}\n"
            f"Processed spectra saved in: {self.processed_data_path}\n\n"
            f"Each spectrum is in {self.processed_data_path}/PLANET_NAME/DATASET_ID/spectrum.dat"
        )


if __name__ == "__main__":
    base_directory = 'workspace'
    wgets_file = 'wfc3_wgets.txt'
    raw_data_path = 'raw_data'
    processed_data_path = 'processed_data'

    process_wgets_file(base_directory, wgets_file, raw_data_path)
    process_downloads(base_directory, raw_data_path, processed_data_path)