# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ASTER (Agentic Science Toolkit for Exoplanet Research) is a refactored agentic system for exoplanet atmospheric research using TauREx spectral modeling. The system uses the `orchestral-ai` package to provide AI agents with tools for downloading exoplanet data, running forward models, and performing atmospheric retrievals.

## Skills System

ASTER includes specialized skill files in `workspace/skills/` that contain detailed knowledge about specific tasks:

- **taurex_setup.md**: TauREx configuration, line list downloads, path setup, and troubleshooting
- **corner_plots.md**: How to create publication-quality corner plots from retrieval results

**Important**: These skill files are NOT loaded into the system prompt. When you need information about these topics, use the ReadFileTool to read the relevant skill file from `workspace/skills/`.

## Environment Setup

This project uses a Python virtual environment located at `aster-env/`:

```bash
# Activate the virtual environment
source aster-env/bin/activate

# Install dependencies
pip install -r requirements.txt
```

Key dependencies:
- `orchestral-ai` - Agent framework
- `taurex` - Atmospheric modeling engine
- `astropy`, `pandas`, `numpy` - Data handling
- `corner-2.2.3` - Posterior visualization

## Running the Application

```bash
# Start the ASTER web UI server
python run_aster.py
```

This starts a web server on `localhost:8000` with an agent that has access to TauREx modeling tools.

## Architecture

### Agent System

The agent system is built on `orchestral-ai` and configured in [run_aster.py](run_aster.py):
- Uses Claude or GPT as the LLM backend (configured via environment variables in `.env`)
- Provides a persistent command execution environment in the `workspace/` directory
- Includes safety hooks (`DangerousCommandHook`) to prevent destructive operations

### Tool Organization

Tools are organized in the `aster_toolkit/` package with clear separation of concerns:

**TauREx Tools** (`aster_toolkit/taurex/`):
- `forward_model.py` - `RunTaurexModelTool` for generating synthetic transmission spectra
- `retrieval.py` - `SimulateTaurexRetrieval` for atmospheric parameter fitting
- `set_paths.py` - `SetTaurexPaths` for configuring opacity/CIA data paths

**Data Acquisition** (`aster_toolkit/data_acquisition/`):
- `exoarchive.py` - `GetExoplanetParameters` for TAP queries to NASA Exoplanet Archive
- `exoarchive.py` - `DownloadDataset` for downloading and processing spectra from NASA archive

### TauREx Path Configuration

TauREx requires opacity cross-sections and CIA (collision-induced absorption) files:

1. **Download line lists** (first time setup):
   ```bash
   python download_linelists.py
   ```
   This downloads molecular cross-sections (H2O, CO2, NH3, CH4, CO) to `workspace/linelists/xsec/` and CIA files (H2-H2, H2-He) to `workspace/linelists/cia/`

2. **Set paths before running models**:
   The agent must call `SetTaurexPaths` with absolute paths:
   ```python
   SetTaurexPaths(
       opacity_path='/full/path/to/workspace/linelists/xsec',
       cia_path='/full/path/to/workspace/linelists/cia'
   )
   ```

### Forward Modeling

`RunTaurexModelTool` generates synthetic transmission spectra given planet/star parameters:

Key parameters:
- Physical: `planet_radius` (RJup), `planet_mass` (MJup), `star_radius` (Rsun)
- Orbital: `orbital_period` (days), `semi_major_axis` (AU)
- Atmospheric: `planet_temp` (K), `atm_min_pressure`/`atm_max_pressure` (bar)
- Chemistry: `molecular_abundances` (optional dict, e.g., `{'H2O': 0.02, 'CH4': 0.001}`)
- Output: `filename` (saves as `{filename}_spectrum.png`)

The tool uses:
- Isothermal temperature profile
- TaurexChemistry with H2/He background (ratio 0.17)
- Default molecular abundances (if not specified): H2O (0.02), CH4 (0.001), CO2 (0.0001), CO (0.001), NH3 (0.0001)
- Custom abundances can be specified via `molecular_abundances` parameter
- Absorption, Rayleigh, and CIA contributions

Output files in `workspace/`:
- `{filename}_spectrum.png` - Plot
- `fm_wavelength.npy`, `fm_spectrum.npy` - Raw data

**Important**: The forward model outputs spectra at full line-list resolution (~100k points). For visualization purposes, these should be binned to observational resolution. Unbinned spectra are too noisy to display meaningfully. Use numpy to bin wavelength and spectrum arrays before custom plotting.

### Atmospheric Retrieval

`SimulateTaurexRetrieval` fits atmospheric parameters to observed spectra using nested sampling.

**Retrieval Modes**:
- `"reduced"` (default) - Fits mixing ratios of 5 predefined molecules (H2O, CH4, CO2, CO, NH3)
- `"equilibrium"` - Fits metallicity and C/O ratio using ACE thermochemical equilibrium
- `"full"` - Fits custom list of molecules specified by user

**Key Parameters**:
- `observation_path` - Path to 3-4 column spectrum file (wavelength μm, depth, error, [bin width])
- `fit_params` - Parameters to fit (minimum: `['planet_radius', 'T']` + chemistry params)
- `bounds` - Dict of `{param: [low, high]}` bounds
- `optimizer` - `"multinest"` (preferred) or `"nestle"`

**Important Notes**:
- Pressure units in TauREx are **Pascals**, not bars (default range: 1e-3 to 1e5 Pa)
- Molecular abundance bounds should be `[1e-9, 1e-2]`
- Standard `nlayers=100` (only change if user requests)

**Outputs** (saved to `output_path` with `output_basename` prefix):
- `*_fit.png` - Observed vs best-fit comparison
- `*_corner.png` - Posterior distributions
- `*_samples.npy`, `*_weights.npy` - Full posterior samples
- `*_wavelength.npy`, `*_spectrum.npy` - Best-fit spectrum

### Data Acquisition

The `exoarchive.py` module provides access to NASA Exoplanet Archive data:

**Tools**:
- `GetExoplanetParameters` - TAP queries for planet/star parameters from pscomppars table
  - Parameters: `planet_name`, `columns` (list of parameter names), `table` (default: "pscomppars")
  - Returns: Dictionary with requested parameters

- `DownloadDataset` - Download and process spectra from NASA archive
  - **Three input methods** (provide only ONE):
    1. `wgets_file_path` - Path to file containing wget commands (user created)
    2. `wget_text` - Raw wget commands pasted directly into chat
    3. `wget_url` - URL to Firefly wget page (tool scrapes commands automatically) ⭐ EASIEST
  - Other parameters: `raw_data_path`, `processed_data_path`
  - Output: spectrum.dat files in `workspace/tmp/processed_data/PLANET_NAME_3/DATASET_ID/`
  - Firefly interface: https://exoplanetarchive.ipac.caltech.edu/cgi-bin/atmospheres/nph-firefly

**Key Functions** (for advanced use):
- `get_exoplanet_params_tap()` - Direct TAP query function
- `process_wgets_file()` - Download IPAC tables from URLs
- `process_downloads()` - Convert raw data to spectrum.dat format

Spectra are stored in `workspace/tmp/processed_data/PLANET_NAME_3/DATASET_ID/spectrum.dat`

## Common Workflows

### Running a Forward Model

1. Ensure line lists are downloaded (`download_linelists.py`)
2. Set TauREx paths using absolute paths
3. Call `RunTaurexModelTool` with planet/star parameters
4. Output saved to `workspace/{filename}_spectrum.png`

### Running a Retrieval

1. Ensure line lists downloaded and paths set
2. Obtain observed spectrum (via `exoarchive.py` or user-provided)
3. Choose retrieval mode and configure fit parameters/bounds
4. Call `simulate_taurex_retrieval()` with appropriate optimizer
5. Review outputs: fit plot, corner plot, and posterior samples

### Downloading Spectra

The `DownloadDataset` tool supports three input methods:

**Method 1: User provides URL (easiest)**
```
User: "Download spectra from https://exoplanetarchive.ipac.caltech.edu/staging/..."
Agent: DownloadDataset(wget_url="https://...")
```

**Method 2: User pastes wget text**
```
User: "Here are the wget commands: wget -O WASP_39_b.tbl '...'"
Agent: DownloadDataset(wget_text="wget -O WASP_39_b.tbl '...'")
```

**Method 3: User saves to file**
```
User: "I saved the wget commands to wgets.txt"
Agent: DownloadDataset(wgets_file_path="wgets.txt")
```

### Querying Exoplanet Data

Use `GetExoplanetParameters` tool for programmatic access to archive data:
```python
# Get planet parameters
GetExoplanetParameters(
    planet_name="WASP-39 b",
    columns=["pl_radj", "pl_bmassj", "st_rad", "st_teff"]
)
```

## Workspace Organization

```
workspace/
├── linelists/          # TauREx opacity/CIA data
│   ├── xsec/          # Molecular cross-sections (.h5 files)
│   └── cia/           # CIA files (.cia files)
├── tmp/               # Downloaded spectra and processed data
│   └── processed_data/PLANET_NAME_3/DATASET_ID/spectrum.dat
├── fm_*.npy           # Forward model outputs
└── *.png              # Plots
```

## Tool Usage Patterns

When working with the agent system:

1. **StateField vs RuntimeField**: Tools use `StateField` for agent-managed state (e.g., `base_directory`) and `RuntimeField` for user/LLM-provided inputs
2. **Streaming callbacks**: Retrieval functions support streaming output via `stream_callback` parameter for real-time progress
3. **Lazy imports**: The codebase uses lazy imports to speed startup time
4. **CamelCase naming**: All tool names follow Python class conventions (e.g., `RunTaurexModelTool`, not `run_taurex_model_tool`)

## Important Notes

- Always use **absolute paths** for TauREx opacity/CIA configuration
- Pressure units in TauREx are **Pascals** (Pa), not bars
- For retrieval, prefer `"multinest"` optimizer for better sampling quality
- The `.env` file contains API keys for LLM backends - never commit this file
- Planet names in archive queries use format like `"WASP-39 b"` (space, lowercase designation)
