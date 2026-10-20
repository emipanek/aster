# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ASTER (Agentic Science Toolkit for Exoplanet Research) is a refactored agentic system for exoplanet atmospheric research using TauREx spectral modeling. The system uses the `orchestral-ai` package to provide AI agents with tools for downloading exoplanet data, running forward models, and performing atmospheric retrievals.

## Skills System

ASTER includes specialized skill files in `workspace/skills/` that contain detailed knowledge about specific tasks:

- **taurex_setup.md**: TauREx configuration, line list downloads, path setup, troubleshooting, where to find volume mixing ratio (VMR) profiles for a spaghetti plot (`{filename}_vmr_profile.npz` for forward models, the retrieval `.h5`'s `Output/Priors/Profiles` for retrievals), and the TauREx citation requirement (full BibTeX)
- **corner_plots.md**: How to create publication-quality corner plots from retrieval results
- **retrieval_best_practices.md**: Parameter bounds guidance, optimizer selection, and retrieval strategies for different use cases
- **cluster_setup.md**: SSH/SLURM cluster setup (Duo 2FA bypass via key-based auth), `.env` configuration, and the GetClusterPaths/WriteSlurmScript/SubmitSlurmJob/CheckSlurmJob/UploadClusterFile/FetchClusterFile workflow
- **fastchem_setup.md**: FastChem equilibrium-chemistry tool: input parameters, single-point vs. profile output, and troubleshooting
- **data_acquisition.md**: NASA Exoplanet Archive parameter tools (`GetExoplanetParameters`, `FindExoplanetsByCondition`): the `_reflink` citation requirement for every downloaded parameter, and how it's enforced

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
- `citation.py` - `TAUREX_CITATION_NOTICE`/`TAUREX_CITATION_BIBTEX`: every result produced with TauREx MUST be cited (Al-Refaie et al. 2021, ApJ, 917, 37, doi:10.3847/1538-4357/ac0252). `RunTaurexTransmissionModelTool`, `RunTaurexEmissionModelTool`, `SimulateTaurexRetrieval`, and `WriteTaurexParameterFile` all append `TAUREX_CITATION_NOTICE` to their own output automatically: always relay it to the user, don't trim it out of your response, and repeat it whenever a result from one of these tools gets used in a write-up later in the conversation. Read `skills/taurex_setup.md` for the full BibTeX entry. This does NOT cover ACE/FastChem's own citations when `chemistry_type='equilibrium'`/`'fastchem'` is used, only the TauREx framework itself.
- `forward_model.py`, `RunTaurexTransmissionModelTool` for generating synthetic transmission spectra, `RunTaurexEmissionModelTool` for emission (dayside flux) spectra
- `retrieval.py`, `SimulateTaurexRetrieval` for atmospheric parameter fitting
- `fastchem_chemistry.py`, `FastChemChemistry`, a custom TauREx `Chemistry` class (`chemistry_type='fastchem'` in the forward model/retrieval tools) computing per-layer thermochemical equilibrium via FastChem (`pyfastchem`) instead of ACE. Exposes `metallicity`/`C_O_ratio` as fittable parameters exactly like ACEChemistry, and reruns FastChem fresh on every model evaluation (via `initialize_chemistry()`), so it works as a genuine retrieval chemistry, not just a fixed forward-model profile. Reuses `aster_toolkit/chemistry/fastchem_tools.py`'s setup helpers (element/logK data files, metallicity/C-O scaling), so results are directly comparable to `RunFastChemEquilibriumTool`.
- `set_paths.py` - `SetTaurexPaths` for configuring opacity/CIA data paths
- `parfile_tools.py` - `WriteTaurexParameterFile`, generates a TauREx CLI `.par` file (for `taurex -i model.par -o output.h5 --retrieval`) instead of running in-session, this is useful for launching a job on a cluster via the Cluster/SLURM tools below. `xsec_path`/`cia_path` default to `CLUSTER_XSEC_PATH`/`CLUSTER_CIA_PATH` from `.env` (cluster-side paths, distinct from `SetTaurexPaths`' local ones) leave them unset rather than guessing a path. `chemistry_type='equilibrium'` writes ACE thermochemical equilibrium chemistry (`chemistry_type = equilibrium`/`metallicity`/`C_ratio` from the installed `acepython.taurex3.ACEChemistry`, the config key is `C_ratio`, not `co_ratio`), `molecules`/`molecular_abundances` must not be set in this mode. When `optimizer='multinest'` and `retrieval=True`, also pass/default `multinest_path` (from `CLUSTER_MULTINEST_PATH`) plus `multinest_search_multi_modes`/`multinest_resume`/`multinest_importance_sampling`, written into `[Optimizer]` as `multi_nest_path`/`search_multi_modes`/`resume`/`importance_sampling`. **Does NOT support `chemistry_type='fastchem'`/`'file'`** those raise a clear "Unknown chemistry_type" error here; they'd need `FastChemChemistry`/`ChemistryFile` registered as a discoverable TauREx plugin on the cluster's own TauREx install to work via `.par` files, which hasn't been set up.

**Data Acquisition** (`aster_toolkit/data_acquisition/`):
- `exoarchive.py` - `GetExoplanetParameters` for TAP queries to NASA Exoplanet Archive, `FindExoplanetsByCondition` for condition-based archive searches. Every requested parameter (e.g. `pl_radj`, `pl_eqt`, `st_teff`, `st_rad`) MUST be downloaded together with its `_reflink` reference column (`pl_radj_reflink`, `pl_eqt_reflink`, `st_teff_reflink`, `st_rad_reflink`, ...) since `pscomppars` blends each value from a different source paper. Both tools enforce this automatically via `add_reflink_columns()`, never strip the `_reflink` columns back out of a result. Read `skills/data_acquisition.md` for details.
- `exoarchive.py` - `DownloadDataset` for downloading and processing spectra from NASA archive

**Cluster / SLURM** (`aster_toolkit/cluster/`):
- `slurm_tools.py` - `WriteSlurmScript` (render an sbatch script; defaults `--qos=main`; supports MPI jobs via `ntasks` plus either `mpi_command` (simple case, auto-wrapped as `mpirun -np "${SLURM_NTASKS}" ...`) or `mpi_rank0_command`/`mpi_other_ranks_command`/`mpi_rank_env_var` (generates a rank-conditional `mpirun ... bash -c 'if [ "$RANK_VAR" = 0 ]; ... else ...; fi'` wrapper, use this whenever the parallelized program writes a shared output file, e.g. every MultiNest/`taurex --retrieval` job, since otherwise every rank races to write the same file); also `mpi_module`, `conda_env` (non-interactive-safe conda activation) + `conda_export_ld_library_path` (needed for `pymultinest`'s `libmultinest.so`), and `mem_per_cpu` as an alternative to `mem`), `SubmitSlurmJob` (upload + `sbatch` over SSH), `CheckSlurmJob` (`squeue`/`sacct` status)
- `remote_ops.py` - `GetClusterPaths` (returns the configured `CLUSTER_REMOTE_WORKDIR`/`CLUSTER_PARFILE_DIR`/`CLUSTER_DATA_DIR`/`CLUSTER_HDF5_DIR`/`CLUSTER_XSEC_PATH`/`CLUSTER_CIA_PATH` values. Call this before referencing any cluster path, since these are server-side env vars the agent otherwise can't see), `UploadClusterFile` (SFTP a file to the cluster without submitting a job), `FetchClusterFile` (SFTP a file, e.g. a finished retrieval's output `.h5`, back to the local workspace)
- `ssh_client.py` - `get_ssh_client()`, connects using key-based auth from `.env` (`CLUSTER_SSH_HOST`/`CLUSTER_SSH_USER`/`CLUSTER_SSH_KEY_PATH`/`CLUSTER_SSH_PORT`/`CLUSTER_REMOTE_WORKDIR`/`CLUSTER_XSEC_PATH`/`CLUSTER_CIA_PATH`)
- Read `skills/cluster_setup.md` before using these tools, covers the one-time key setup needed to bypass the cluster's Duo 2FA and the required `.env` variables

**Chemistry** (`aster_toolkit/chemistry/`):
- `fastchem_tools.py` - `RunFastChemEquilibriumTool`, gas-phase chemical-equilibrium mixing ratios via FastChem (`pyfastchem`). Independent of TauREx, no opacity paths or planet/star model needed, just a temperature (and optionally pressure/metallicity/C-O ratio).
- Input data (`fastchem_input/` at the project root) is located automatically by the tool, unlike TauREx line lists, the agent never needs to supply paths
- Read `skills/fastchem_setup.md` before using this tool for parameter details and single-point vs. profile output behavior

### TauREx Path Configuration

TauREx requires opacity cross-sections and CIA (collision-induced absorption) files:

1. **Download line lists** (first time setup):
   ```bash
   python download_linelists.py
   ```
   This downloads molecular cross-sections (H2O, CO2, NH3, CH4, CO) to `workspace/linelists/xsec/` and CIA files (H2-H2, H2-He) to `workspace/linelists/cia/`

2. **Set paths before running models**:
   The agent must call `SetTaurexPaths` with absolute paths. The agent should:
   - First run `pwd` to get the current working directory
   - Then construct the full absolute paths to `linelists/xsec/` and `linelists/cia/`
   - Never hardcode or guess paths (e.g., `/app/linelists` is wrong)

   Example:
   ```python
   # If pwd returns /Users/username/project/workspace
   SetTaurexPaths(
       opacity_path='/Users/username/project/workspace/linelists/xsec',
       cia_path='/Users/username/project/workspace/linelists/cia'
   )
   ```

### Forward Modeling

`RunTaurexTransmissionModelTool` generates synthetic transmission spectra given planet/star parameters:

Key parameters:
- Physical: `planet_radius` (RJup), `planet_mass` (MJup), `star_radius` (Rsun)
- Atmospheric: `planet_temp` (K), `atm_min_pressure`/`atm_max_pressure` (Pascal), `nlayers` (default 100)
- Chemistry: `molecules` (optional list of names, e.g. `['H2O', 'CH4']`) or `molecular_abundances` (optional dict of exact ratios, e.g., `{'H2O': 0.02, 'CH4': 0.001}`), or `chemistry_type='equilibrium'`/`'fastchem'` with `metallicity`/`co_ratio` for ACE/FastChem thermochemical equilibrium, or `chemistry_type='file'` to load a fixed profile from `chemistry_file_path`/`chemistry_file_gases` the file's row count MUST match `nlayers` exactly, so raise/lower `nlayers` to match the file rather than reformatting it
- Output: `filename` (saves as `{filename}_spectrum.png`, `{filename}_fm_wavelength.npy`, `{filename}_fm_spectrum.npy`, and `{filename}_vmr_profile.npz` per-layer volume mixing ratio of every gas vs. pressure/altitude, for a VMR "spaghetti" plot; see `save_vmr_profile()` in `forward_model.py` / `skills/taurex_setup.md`)

The tool uses:
- Isothermal temperature profile
- Absorption, Rayleigh, and CIA contributions

Chemistry is controlled by `chemistry_type` (default `'free'`):

**`chemistry_type='free'`** (default) -> `TaurexChemistry` with H2/He background (ratio 0.17), gas mixing ratios set via one of (only one path is used, in this order):
1. `molecular_abundances` exact mixing ratios given by the user, e.g. `{'H2O': 0.02, 'CH4': 0.001}`. Use this only when the user specifies actual numbers.
2. `molecules` just a list of molecule names, e.g. `['H2O', 'CH4']`. Each molecule's abundance is looked up from the fixed `DEFAULT_MOLECULE_ABUNDANCES` table in `forward_model.py` (covers H2O, CH4, CO2, CO, NH3, HCN, H2S, SO2, C2H2, Na, K, TiO, VO). Use this when the user names specific molecules without giving ratios - never invent abundance numbers yourself, this keeps results deterministic/reproducible.
3. If neither is given, the fixed **basic model** is used: H2O (0.02), CH4 (0.001), CO2 (0.0001), CO (0.001), NH3 (0.0001). Use this for a generic "basic model" request.

**`chemistry_type='equilibrium'`** only set this if the user explicitly asks for equilibrium/ACE chemistry. Uses `ACEChemistry` from `acepython.taurex3` instead of `TaurexChemistry`, computing abundances from thermochemical equilibrium:
```python
from acepython.taurex3 import ACEChemistry
# Constructor kwarg is 'C_ratio', NOT 'co_ratio' - ACEChemistry.__init__ takes ratio kwargs  named '{Element}_ratio' (C_ratio for the default ratio_element='O'); passing 'co_ratio' is silently swallowed by **kwargs and has no effect (this was a real bug here, now fixed).
chemistry = ACEChemistry(metallicity=metallicity, C_ratio=co_ratio)
```
`molecules`/`molecular_abundances` must not be set in this mode (the tool raises an error if they are) - fit chemistry here via `metallicity` (default 1.0, solar) and `co_ratio` (default 0.54, solar) instead.

**`chemistry_type='fastchem'`** only set this if the user explicitly asks for FastChem chemistry. Same `metallicity`/`co_ratio` inputs as `'equilibrium'`, but computed via the project's own `FastChemChemistry` (`aster_toolkit/taurex/fastchem_chemistry.py`, using `pyfastchem`) instead of ACE; `molecules` picks which species besides H2/He it solves for (defaults to the basic model set if not given), `element_abundance_source` (default `'asplund_2021'`) picks the starting solar composition. `molecular_abundances` must not be set in this mode.

**`chemistry_type='file'`** only set this if the user explicitly provides/references an external mixing-ratio file (e.g. a GCM or photochemical model output). Loads a fixed per-layer profile via TauREx's own `ChemistryFile` from `chemistry_file_path` (whitespace-delimited text, one row per atmospheric layer, one column per gas) with `chemistry_file_gases` naming each column in order. `molecules`/`molecular_abundances`/`metallicity`/`co_ratio` are all ignored; the file's row count MUST equal `nlayers` (default 100) if it doesn't, the tool raises a clear error naming the exact `nlayers` value to set (set `nlayers` to match the file, don't reformat the file), and the model itself is built with that many layers (`TransmissionModel`/`EmissionModel(nlayers=nlayers)`).

Output files in `workspace/`:
- `{filename}_spectrum.png` - Plot
- `{filename}_fm_wavelength.npy`, `{filename}_fm_spectrum.npy` - Raw data
- `{filename}_vmr_profile.npz` - Per-layer VMR of every gas vs. pressure/altitude (for a spaghetti plot); same file for `RunTaurexEmissionModelTool` (also under `{filename}_vmr_profile.npz`)

**Important**: The forward model outputs spectra at full line-list resolution (~100k points). For visualization purposes, these should be binned to observational resolution. Unbinned spectra are too noisy to display meaningfully. Use numpy to bin wavelength and spectrum arrays before custom plotting.

### Spectrum Binning

`BinSpectrum` rebins a high-resolution spectrum onto a lower-resolution or instrument wavelength grid. Output is TauREx 4-column `.dat` (wavelength, depth, error, bin_width) and can be passed directly to `SimulateTaurexRetrieval`.

**Binning targets** (set exactly one):
- `instrument` — predefined mode key, e.g. `'jwst_nirspec_prism'`, `'hst_wfc3_g141'`. Use `instrument='list'` to see all modes (JWST, HST, Ariel, ELT/VLT).
- `resolving_power` — custom R (e.g. `50`, `100`)
- `n_bins` — custom number of log-spaced bins (`>= 2`)

**Other parameters**:
- `spectrum_path` — input file relative to `base_directory`. Supports TauREx 3/4-col `.dat`, 2-col text, Exo_Skryer 4-col, and `.npy` pairs (`*_wavelength.npy` + `*_spectrum.npy`, including TauREx FM `*_fm_wavelength.npy`)
- `wl_min` / `wl_max` — optional wavelength limits for custom grids
- `method` — `'custom'` (default, NumPy-only flux-conserving) or `'spectres'`
- `constant_error` — **only when the user explicitly asks**. Attach a uniform uncertainty (e.g. `20e-6` for 20 ppm). Do not invent or auto-add errors.
- `output_filename` — output subfolder / file prefix (default `'binned_spectrum'`)

**Example**:
```python
# After a forward model that wrote planet_fm_wavelength.npy / planet_fm_spectrum.npy
BinSpectrum(
    spectrum_path='planet_fm_wavelength.npy',
    instrument='jwst_nirspec_prism',
    output_filename='planet_binned',
)
# Only if the user asks for constant errors:
# BinSpectrum(..., constant_error=20e-6)
```

**Outputs** (in `{output_filename}/`):
- `{output_filename}_binned.dat` — TauREx format, retrieval-ready
- `{output_filename}_wavelength.npy`, `{output_filename}_spectrum.npy`
- `{output_filename}_comparison.png` — original vs binned + residuals

### Atmospheric Retrieval

`SimulateTaurexRetrieval` fits atmospheric parameters to observed spectra using nested sampling.

**Chemistry Configuration** (same flexibility as the forward model tools):
- `chemistry_type='free'` (default) - fits molecule mixing ratios, chosen by (in this order): `molecular_abundances` (exact initial values) > `molecules` (names only, seeded from a fixed lookup table) > the fixed basic 5-molecule set (H2O, CH4, CO2, CO, NH3) if neither is given
- `chemistry_type='equilibrium'` - fits `metallicity` and C/O ratio via ACE thermochemical equilibrium instead, seeded from the `metallicity`/`co_ratio` arguments (only use if the user explicitly asks for equilibrium/ACE chemistry)
- `chemistry_type='fastchem'` - fits the same `metallicity`/`C_O_ratio` pair but via FastChem instead of ACE (`FastChemChemistry` reruns FastChem fresh on every nested-sampling iteration, a genuine fit, not a fixed seed profile); `molecules` picks which species besides H2/He it solves for. Only use if the user explicitly asks for FastChem chemistry.
- `chemistry_type='file'` - loads a fixed per-layer profile from `chemistry_file_path`/`chemistry_file_gases` and does NOT fit composition at all; only `planet_radius`/`T` are fit by default. Only use if the user explicitly provides/references such a file.

**Key Parameters**:
- `observation_path` - **REQUIRED**. Path to 3-4 column spectrum file (wavelength μm, depth, error, [bin width]). Use exact path from DownloadDataset output or user-provided file.
- `fit_params` - Parameters to fit. Can be passed as a list or string representation. **Optional** if not given, auto-generated from the chemistry configuration: `['planet_radius', 'T']` + the resolved molecule list, or `['planet_radius', 'T', 'metallicity', 'C_O_ratio']` for `chemistry_type='equilibrium'`.
- `bounds` - Dict of `{param: [low, high]}` bounds. Can be passed as a dict or string representation. **Optional** if not provided, reasonable defaults are auto-generated.
- `optimizer` - prefer `"multinest"` if `pymultinest` is confirmed installed and working (faster, publication standard) otherwise `"nestle"` (default, pure Python, always works). Only use `"ultranest"` if the user explicitly asks for it.

**Important Notes**:
- Pressure units in TauREx are **Pascals**, not bars (default range: 1e-1 to 1e6 Pa)
- Molecular abundance bounds should be `[1e-12, 1e-2]`
- Standard `nlayers=100` (only change if user requests)
- **String parameters**: `fit_params`, `bounds`, `molecules`, and `molecular_abundances` accept both native Python objects and string representations (e.g., `"['H2O', 'CH4']"` or `['H2O', 'CH4']`). The tool will parse strings automatically.
- **Auto-generated bounds**: If `bounds` is not provided, the tool generates sensible defaults: planet_radius [0.5, 2.5] RJup, T [500, 3000] K, molecules [1e-12, 1e-2], metallicity [0.1, 10.0], C_O_ratio [0.1, 2.0]
- **`num_live_points` minimum on a cluster**: `WriteTaurexParameterFile` (the tool that writes `.par` files for cluster jobs, via `parfile_tools.py`) enforces a floor of 500 live points when `retrieval=True`, silently raising anything lower. 1000 is recommended and is the tool's default for cluster-specific. When using `SimulateTaurexRetrieval` runs in-session locally rather than on a cluster, keeps its own lower default (100).
- **Note the exact casing `C_O_ratio`** (not `co_ratio` or `c_o_ratio`) when it appears in `fit_params`/`bounds` - it must match TauREx's `ACEChemistry.fitting_parameters()` name exactly. The tool-facing `co_ratio` argument (a plain float, used to seed the initial value) is different from this internal fit-parameter name.

**Outputs** (saved to `output_path` with `output_basename` prefix):
- `*.h5` - **Full results in one HDF5 file** (model setup, best-fit solution, full posterior, per-layer profiles, per-contribution spectra) same structure TauREx's own CLI produces via `taurex -o output.h5 --retrieval`. This is the authoritative, complete output; the tool's final message always calls out its path explicitly.
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
  - **Every parameter MUST be downloaded with its reference**: `pscomppars` composites each value from a different source paper, so e.g. `pl_radj` (planet radius) needs `pl_radj_reflink`, `pl_eqt` (equilibrium temp) needs `pl_eqt_reflink`, `st_teff` (star temp) needs `st_teff_reflink`, `st_rad` (star radius) needs `st_rad_reflink`, etc. `add_reflink_columns()` appends these automatically for any requested column that's a physical parameter (not an identifier like `pl_name`/`hostname`) see `skills/data_acquisition.md`. Do not drop the `_reflink` columns from a result before citing/saving it.

- `DownloadDataset` - Download and process spectra from NASA archive
  - **Three input methods** (provide only ONE):
    1. `wgets_file_path` - Path to file containing wget commands (user created)
    2. `wget_text` - Raw wget commands pasted directly into chat
    3. `wget_url` - URL to Firefly wget page (tool scrapes commands automatically) ⭐ EASIEST
  - Parameters: `output_dir` (default: "spectra")
  - **File organization**:
    - Working files: `workspace/download_dataset_tool/query{NNN}/` (for debugging)
    - Final spectra: `workspace/spectra/PLANET_NAME_3/DATASET_ID/spectrum.dat`
    - Each download gets unique query ID (query001, query002, etc.)
    - **Tool output shows full spectrum file paths** for use in retrievals
  - Firefly interface: https://exoplanetarchive.ipac.caltech.edu/cgi-bin/atmospheres/nph-firefly

**Key Functions** (for advanced use):
- `get_exoplanet_params_tap()` - Direct TAP query function
- `process_wgets_file()` - Download IPAC tables from URLs
- `process_downloads()` - Convert raw data to spectrum.dat format

## Common Workflows

### Running a Forward Model

1. Ensure line lists are downloaded (`download_linelists.py`)
2. Set TauREx paths using absolute paths
3. Call `RunTaurexTransmissionModelTool` with planet/star parameters
4. Output saved to `workspace/{filename}_spectrum.png`
5. Optionally bin with `BinSpectrum` for visualization or as synthetic retrieval input

### Binning a Spectrum

1. Obtain a high-res spectrum (forward model `.npy` pair, or observed `.dat`)
2. Call `BinSpectrum` with exactly one of `instrument`, `resolving_power`, or `n_bins`
3. Do **not** pass `constant_error` unless the user explicitly requests a constant uncertainty

### Running a Retrieval

1. **Read the skill file**: Agent must use ReadFileTool to read `skills/retrieval_best_practices.md` first
2. Ensure line lists downloaded and TauREx paths set (with `ls`/`pwd` to get absolute paths)
3. Obtain observed spectrum (via `DownloadDataset` tool or user-provided)
4. Choose retrieval mode and configure fit parameters/bounds (or use auto-generated defaults)
5. Call `SimulateTaurexRetrieval` with `optimizer="multinest"` if confirmed installed/working, else the default `optimizer="nestle"` (always works)
6. Review outputs: fit plot, corner plot, and posterior samples

**Important**: The agent should read the skill file before running ANY retrieval to understand optimizer selection and parameter bounds.

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
4. **CamelCase naming**: All tool names follow Python class conventions (e.g., `RunTaurexTransmissionModelTool`, not `run_taurex_model_tool`)

## Important Notes

- Always use **absolute paths** for TauREx opacity/CIA configuration
- Pressure units in TauREx are **Pascals** (Pa), not bars
- For retrieval, prefer `"multinest"` if confirmed installed/working (faster, publication standard), else the default `"nestle"` (multinest requires difficult installation); `"ultranest"` only if the user explicitly asks for it
- The `.env` file contains API keys for LLM backends - never commit this file
- Planet names in archive queries use format like `"WASP-39 b"` (space, lowercase designation)
