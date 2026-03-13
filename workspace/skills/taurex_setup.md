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

## Common Issues

- **"Opacity file not found"**: Paths not set or incorrect
- **Import errors**: TauREx not installed in environment
- **Slow model runs**: Normal for first run (caching opacities)
