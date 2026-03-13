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
        default=["pl_radj", "pl_bmassj", "pl_eqt"],
        # Uncomment below to include orbital parameters for forward modeling:
        # default=["pl_radj", "pl_bmassj", "pl_eqt", "pl_orbper", "pl_orbsmax"],
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

        # Parameter display mapping (column_name -> (label, unit))
        param_labels = {
            # Planet parameters
            'pl_name': ('Planet Name', ''),
            'pl_radj': ('Planet Radius', 'RJup'),
            'pl_rade': ('Planet Radius', 'REarth'),
            'pl_bmassj': ('Planet Mass', 'MJup'),
            'pl_bmasse': ('Planet Mass', 'MEarth'),
            'pl_eqt': ('Equilibrium Temp', 'K'),
            'pl_orbper': ('Orbital Period', 'days'),
            'pl_orbsmax': ('Semi-major Axis', 'AU'),

            # Stellar parameters
            'hostname': ('Star Name', ''),
            'st_rad': ('Star Radius', 'Rsun'),
            'st_teff': ('Star Temp', 'K'),
            'st_mass': ('Star Mass', 'Msun'),
            'st_met': ('Stellar Metallicity', 'dex'),
            'st_logg': ('Stellar log(g)', 'log10(cm/s^2)'),
        }

        # Format output
        output = f"Parameters for {self.planet_name}:\n\n"
        for param, value in result.items():
            if param in param_labels:
                label, unit = param_labels[param]
                # Format numeric values with 5 significant figures
                try:
                    numeric_value = float(value)
                    formatted_value = f"{numeric_value:.5g}"
                except (ValueError, TypeError):
                    formatted_value = value

                if unit:
                    output += f"  {label}: {formatted_value} {unit}\n"
                else:
                    output += f"  {label}: {formatted_value}\n"
            else:
                # Fallback for any column not in our mapping
                output += f"  {param}: {value}\n"

        return output


class DownloadDataset(BaseTool):
    """
    Download transit spectra from NASA Exoplanet Archive and reformat them.

    This tool supports THREE ways to provide wget commands:

    1. **File path** (wgets_file_path): User saves wget commands to a file, agent reads it
       - User creates 'wgets.txt' in workspace
       - Agent calls: DownloadDataset(wgets_file_path='wgets.txt')

    2. **Direct text** (wget_text): User pastes wget commands directly into chat
       - User: "Here are my wget commands: [pastes text]"
       - Agent calls: DownloadDataset(wget_text="wget -O file.tbl 'http://...'")

    3. **URL to wget page** (wget_url): User provides URL to Firefly wget page (EASIEST!)
       - User pastes: https://exoplanetarchive.ipac.caltech.edu/staging/...
       - Agent calls: DownloadDataset(wget_url="https://...")
       - Tool automatically scrapes wget commands from the page

    To generate wget commands:
    1. Go to https://exoplanetarchive.ipac.caltech.edu/cgi-bin/atmospheres/nph-firefly
    2. Filter by instrument and planet(s)
    3. Check boxes for desired spectra
    4. Click "Download all checked spectra"
    5. Provide the URL, text, or save to file
    """

    # Only ONE of these three should be provided
    wgets_file_path: str | None = RuntimeField(
        default=None,
        description="Path to existing wgets text file (relative to base_directory). Use if user saved wget commands to a file."
    )
    wget_text: str | None = RuntimeField(
        default=None,
        description="Raw wget commands as text. Use when user pastes wget commands directly into chat."
    )
    wget_url: str | None = RuntimeField(
        default=None,
        description="URL to Firefly wget page. Tool will scrape wget commands from this URL automatically."
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
        import tempfile

        # Count how many input methods were provided
        inputs_provided = sum([
            self.wgets_file_path is not None,
            self.wget_text is not None,
            self.wget_url is not None
        ])

        if inputs_provided == 0:
            return "Error: Please provide ONE of: wgets_file_path, wget_text, or wget_url"

        if inputs_provided > 1:
            return "Error: Please provide only ONE input method (file_path, text, or url)"

        # Determine the wget file path
        wget_file_to_use = None
        temp_file_created = False

        if self.wgets_file_path:
            # Method 1: User provided file path
            wget_file_to_use = self.wgets_file_path

        elif self.wget_text:
            # Method 2: User provided text directly - create temporary file
            temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, dir=self.base_directory)
            temp_file.write(self.wget_text)
            temp_file.close()
            wget_file_to_use = os.path.basename(temp_file.name)
            temp_file_created = True

        elif self.wget_url:
            # Method 3: User provided URL - scrape and create temporary file
            try:
                response = requests.get(self.wget_url)
                response.raise_for_status()
                wget_commands = response.text

                # Create temporary file with scraped content
                temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, dir=self.base_directory)
                temp_file.write(wget_commands)
                temp_file.close()
                wget_file_to_use = os.path.basename(temp_file.name)
                temp_file_created = True

            except requests.RequestException as e:
                return f"Error fetching wget commands from URL: {e}"

        try:
            # Process wget file to download data
            process_wgets_file(
                base_directory=self.base_directory,
                wgets_file_path=wget_file_to_use,
                data_path=self.raw_data_path
            )

            # Process downloads to extract spectral data
            process_downloads(
                base_directory=self.base_directory,
                data_path=self.raw_data_path,
                output_dir=self.processed_data_path
            )

            result = (
                f"Download complete!\n\n"
                f"Raw CSV data saved in: {self.raw_data_path}\n"
                f"Processed spectra saved in: {self.processed_data_path}\n\n"
                f"Each spectrum is in {self.processed_data_path}/PLANET_NAME/DATASET_ID/spectrum.dat"
            )

        finally:
            # Clean up temporary file if we created one
            if temp_file_created and wget_file_to_use:
                try:
                    os.remove(os.path.join(self.base_directory, wget_file_to_use))
                except:
                    pass

        return result


if __name__ == "__main__":
    base_directory = 'workspace'
    wgets_file = 'wfc3_wgets.txt'
    raw_data_path = 'raw_data'
    processed_data_path = 'processed_data'

    process_wgets_file(base_directory, wgets_file, raw_data_path)
    process_downloads(base_directory, raw_data_path, processed_data_path)