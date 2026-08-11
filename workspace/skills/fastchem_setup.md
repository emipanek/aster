# FastChem Equilibrium Chemistry

## Overview

`RunFastChemEquilibriumTool` predicts gas-phase chemical-equilibrium mixing ratios for a planetary
atmosphere using [FastChem](https://newstrangeworlds.github.io/FastChem/) (`pyfastchem`). It is
**independent of the TauREx tools** - no opacity/CIA line lists, `SetTaurexPaths`, or a full
planet/star model are needed. Use it when the user asks something like "what molecules would I
expect at equilibrium for a planet at 1500 K?" without wanting a full forward model or retrieval.

## Input Data

FastChem's reference data (element abundance compilations and thermochemical `logK` files) ships
in `fastchem_input/` at the project root (sibling of `workspace/`). Unlike TauREx's line lists,
**the agent never needs to locate or supply these paths** - the tool finds this directory itself
relative to its own file location, since this is static bundled data, not something downloaded
per-machine.

## Key Parameters

- `temperature` - **required**. A single value in Kelvin (e.g. `1500`), or a list (e.g. `[500, 1000, 1500, 2000]`) to compute a profile.
- `pressure` - optional, default `1.0` bar. Single value or a list. If one of `temperature`/`pressure` is a single value and the other a list, the single value is broadcast to match the list's length (e.g. `temperature=1500, pressure=[1e-6, 1e-3, 1, 100]` gives an isothermal pressure profile).
- `metallicity` - default `1.0` (solar). Scales all element abundances except H and He (e.g. `10.0` = 10x solar).
- `co_ratio` - default `None` (uses the solar C/O from the abundance source, ~0.55). Set explicitly to override, e.g. `0.9` for a carbon-rich atmosphere.
- `element_abundance_source` - default `"asplund_2021"` (most recent/recommended). Also accepts `"asplund_2009"`, `"lodders_2003"`, `"lodders_2025"`.
- `species` - specific molecules to report, e.g. `['H2O', 'CH4', 'CO']`. If omitted, falls back to `top_n` if given, otherwise a **fixed default set** (`H2, He, H2O, CO, CO2, CH4, NH3, HCN, H2S, N2, Na, K, TiO, VO, FeH, SiO`) - deterministic, so the agent never has to guess which species to report.
- `top_n` - report the N most abundant species instead of the default set. Ignored if `species` is given.
- `include_condensates` - default `False`. Only set `True` if the user explicitly asks about clouds/condensation/rainout - adds real complexity and changes convergence behavior.
- `filename` - only used for a profile (list-valued temperature/pressure); ignored for a single point.

## Output

- **Single (temperature, pressure) point**: returned directly as formatted text - mixing ratios (mole fractions) for each resolved species, plus a convergence note.
- **Profile** (list-valued temperature and/or pressure): a single text point isn't meaningful for a list of results, so instead the tool saves:
  - `{filename}_fastchem_mixing_ratios.png` - mixing ratio vs. pressure plot (log-log, pressure axis inverted so high pressure/deep atmosphere is at the bottom)
  - `{filename}_fastchem_temperature.npy`, `{filename}_fastchem_pressure.npy` - the input profile arrays
  - `{filename}_fastchem_mixing_ratios.npy` - shape `(n_points, n_resolved_species)`; the tool's response text lists the column order

## Common Workflow

1. Ask the user (or infer from context) for the planet's temperature - and pressure/metallicity/C-O ratio if relevant to their question.
2. Call `RunFastChemEquilibriumTool(temperature=...)` - defaults (solar metallicity/C-O, 1 bar, default species set) are reasonable for a quick "what should I expect" question.
3. For a "how does chemistry change with altitude/depth" question, pass a `pressure` list (e.g. `np.logspace(-6, 2, 50)` equivalent) at a fixed `temperature`, plus a `filename`, and look at the saved plot.
4. If specific molecules are named ("does this planet have HCN?"), pass them via `species` rather than relying on the default set or `top_n`.

## Troubleshooting

- **"fastchem_input/ directory not found"**: the bundled reference data directory is missing or was moved - it should sit at the project root, alongside `workspace/`.
- **"Unknown element_abundance_source"**: must be one of `asplund_2021`, `asplund_2009`, `lodders_2003`, `lodders_2025` (exact spelling, no `_extended`/`_full` suffix - the tool appends `_extended` itself).
- **Species reported as raw Hill notation** (e.g. `H2O1` instead of `H2O`) only happens via `top_n` ranking, where the tool pairs the symbol with FastChem's descriptive name (e.g. `H2O1 (Water)`) for readability.
- **"WARNING: element conservation failed"** in the convergence note: FastChem didn't fully converge for that point - consider whether the temperature/pressure is physically extreme, or whether `include_condensates` should be toggled.
