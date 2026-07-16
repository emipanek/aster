# TauREx Setup

## Initial Setup

Before running any TauREx models, you need to download opacity and CIA files.

### Download Line Lists

The `download_linelists.py` script is located one directory up from your workspace. Run it with:
```bash
cd ..; python download_linelists.py
```

Or run it directly:
```bash
python ../download_linelists.py
```

This downloads molecular cross-sections (H2O, CO2, NH3, CH4, CO) and CIA files (H2-H2, H2-He) to `linelists/` (which is `workspace/linelists/` from the project root).

**Note**: This only needs to be done once. The download may take several minutes.

### Set TauREx Paths

After downloading, you need to set the paths to the opacity and CIA files using **absolute paths**.

**Step-by-step process**:

1. **First, check if linelists exist in the workspace**:
   ```bash
   ls linelists/
   ```
   You should see `xsec/` and `cia/` subdirectories. The `xsec/` directory contains opacity cross-section files, and `cia/` contains collision-induced absorption files.

2. **Get the absolute path to workspace**:
   ```bash
   pwd
   ```
   This returns the full path (e.g., `/Users/username/project/workspace`)

3. **Set the TauREx paths using the full absolute path**:
   ```python
   SetTaurexPaths(
       opacity_path='/full/absolute/path/from/pwd/linelists/xsec',
       cia_path='/full/absolute/path/from/pwd/linelists/cia'
   )
   ```

**Example**:
- If `pwd` returns `/Users/username/project/workspace`
- Then use:
  - `opacity_path='/Users/username/project/workspace/linelists/xsec'`
  - `cia_path='/Users/username/project/workspace/linelists/cia'`

**Important**:
- **NEVER guess or hardcode paths** like `/app/linelists` - always use `pwd` first
- **ALWAYS point to the xsec/ and cia/ subdirectories**, not the parent `linelists/` directory
- Paths must be absolute (start with `/`), not relative

### Verify Setup

If you encounter errors about missing opacity files:
1. Check that line lists were downloaded successfully
2. Verify paths are absolute (not relative)
3. Ensure paths point to the directories containing the files, not parent directories

## Forward Model Chemistry

`RunTaurexModelTool` supports two chemistry modes via `chemistry_type` (default `'free'`):

**`chemistry_type='free'`** (default) - fixed mixing ratios via `TaurexChemistry`, chosen by one of (only one path is used, in this order):
1. `molecular_abundances` - exact mixing ratios given by the user, e.g. `{'H2O': 0.02, 'CH4': 0.001}`. Use only when the user specifies actual numbers.
2. `molecules` - just a list of molecule names, e.g. `['H2O', 'CH4']`. Each molecule's abundance is looked up from the fixed `DEFAULT_MOLECULE_ABUNDANCES` table in `forward_model.py` (covers H2O, CH4, CO2, CO, NH3, HCN, H2S, SO2, C2H2, Na, K, TiO, VO). Use this when the user names specific molecules without giving ratios - never invent abundance numbers yourself, this keeps results deterministic/reproducible.
3. If neither is given, the fixed **basic model** is used: H2O (0.02), CH4 (0.001), CO2 (0.0001), CO (0.001), NH3 (0.0001). Use this for a generic "basic model" request.

**`chemistry_type='equilibrium'`** only set this if the user explicitly asks for equilibrium/ACE chemistry. Uses `ACEChemistry` from `acepython.taurex3` to compute abundances from thermochemical equilibrium via `metallicity` (default 1.0, solar) and `co_ratio` (default 0.54, solar). 

## Forward Model Output Files

Each run of `RunTaurexModelTool` with a given `filename` saves, in `workspace/`:
- `{filename}_spectrum.png` - plot of the transmission spectrum
- `{filename}_fm_wavelength.npy` - wavelength array (µm, full line-list resolution)
- `{filename}_fm_spectrum.npy` - corresponding spectrum array

**Important**: These arrays are at full line-list resolution (~100k points) - bin them to observational resolution with numpy before any custom plotting, since unbinned spectra are too noisy to display meaningfully.

## Common Issues

- **"Opacity file not found"**: Paths not set or incorrect
- **Import errors**: TauREx not installed in environment
- **Slow model runs**: Normal for first run (caching opacities)
