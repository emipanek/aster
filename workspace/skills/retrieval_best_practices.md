# Retrieval Best Practices

This skill provides guidance on setting up atmospheric retrievals for optimal performance and physically meaningful results.

## Outputs

`SimulateTaurexRetrieval` saves, to `output_path` with `output_basename` prefix:

- **`*.h5` is the full, authoritative result.** One HDF5 file containing everything: model setup (planet/star/chemistry/pressure), optimizer configuration, the best-fit solution, the full posterior (samples + weights + per-parameter statistics), per-layer atmospheric profiles, and the per-contribution spectral breakdown (absorption/Rayleigh/CIA, per molecule). This is the same structure TauREx's own CLI produces via `taurex -o output.h5 --retrieval`, if you need anything beyond what the individual `.png`/`.npy` files below give you, it's in this file. **The tool's final message always states this file's path explicitly** read it back to the user rather than assuming a fixed filename, since it depends on `output_basename`.
- `*_fit.png` observed vs. best-fit model comparison plot
- `*_corner.png` posterior corner plot
- `*_wavelength.npy`, `*_spectrum.npy` best-fit spectrum arrays (binned to the observation grid)
- `*_samples.npy`, `*_weights.npy` raw posterior samples/weights (same data as inside the `.h5`, provided separately for quick loading without needing `h5py`)

## Parameter Bounds

### General Principles

The bounds you set directly control:
1. **Runtime**: Wider bounds = longer retrieval time
2. **Physical plausibility**: Bounds should reflect realistic atmospheric conditions
3. **Convergence**: Overly wide bounds can make it harder to find solutions

### Molecular Abundance Bounds

**Chemical abundance limits (log₁₀ mixing ratio)**:
- **Lower limit**: `-12` (i.e., 10^⁻12)
  - Abundances below 10^⁻12 have essentially no measurable spectroscopic effect
  - Values below this are computationally wasteful and practically indistinguishable

- **Upper limit**: `-2` (i.e., 10⁻²)
  - Total mixing ratios of all species must sum to ≤ 1.0
  - If atmosphere is H₂/He dominated (typical for gas giants), leave room for background gases
  - **Use -1 (10⁻¹) at the very maximum** to ensure total doesn't exceed 1

**Default bounds**: `[1e-12, 1e-2]` or in log₁₀ space: `[-12, -2]`

### Temperature Bounds

**For quick tests** (when you know approximate T):
- Use tight bounds: `T_expected ± 500 K`
- Example: If T ≈ 1500 K, use `[1000, 2000]`
- **ALWAYS use bounds relative to the considered planet temperature**

**For genuine exploration**:
- Use wide physically plausible bounds
- Use: `T_expected ± 1000 K`
- Example: If T ≈ 1500 K, use `[500, 2500]`
- **ALWAYS use bounds relative to the considered planet temperature**

### Radius Bounds

**Planet radius** (Jupiter radii):
- Always use: `R_expected ± 0.5` RJup
- Consider: Radius changes with reference pressure and atmospheric scale height
- **ALWAYS use bounds relative to the radius of the considered planet**

### Equilibrium Chemistry Parameters

**Metallicity** (solar units):
- Quick test: `[0.5, 3.0]` (subsolar to 3× solar)
- Full exploration: `[0.1, 10.0]` (0.1× to 10× solar)

**C/O ratio**:
- Quick test: `[0.3, 1.0]` (typical range)
- Full exploration: `[0.1, 2.0]` (subsolar to supersolar)

## Optimizer Selection

**IMPORTANT**: num_live_points should NEVER be less than 50, the recommended value is 100! If the user specifies less than 50, please print a warning message.

**Decision procedure** (check in this order):
1. **Is `pymultinest` confirmed installed and working?** (e.g. `python -c "import pymultinest"` succeeds, or a prior retrieval in this session already ran multinest successfully) -> use **multinest**.
2. **Otherwise** -> use **nestle** (the safe option, always works, no installation to verify but slower than multinest).
3. **ultranest** -> only use this if the user explicitly asks for it by name. 

### multinest (Publication Standard, preferred when available)
- **Pros**: State-of-the-art nested sampling, most widely used in exoplanet community, faster than nestle
- **Cons**: **Difficult to install** requires compiled Fortran libraries (MultiNest + pymultinest), and even when "installed" can fail at runtime from MPI-implementation mismatches or similar environment issues (see `skills/cluster_setup.md` for real examples encountered on this project's cluster)
- **Use for**: The default choice whenever it's confirmed installed and working, not just "final publication-quality results," any retrieval benefits from it being faster
- **Check availability**: Try importing pymultinest first, or note if a prior retrieval already failed with a missing-module/library error. if so, fall back to nestle without re-trying multinest

### nestle (default fallback)
- **Pros**: Pure Python, easy to install, reliable convergence, works out-of-the-box
- **Cons**: Slower than multinest for complex retrievals
- **Use for**: The default whenever multinest isn't confirmed installed/working, always works, no dependency risk
- **Installation**: Already included in ASTER dependencies
- **Status**: available

### ultranest (opt-in only)
- **Pros**: Supposedly faster than MultiNest, modern algorithm, pure Python
- **Cons**: Less extensively tested in exoplanet literature
- **Use for**: **Only when the user explicitly asks for ultranest by name**, never choose it automatically over multinest/nestle
- **Status**: available in ASTER dependencies


## Optimizer Tuning Arguments (`SimulateTaurexRetrieval`)

Beyond `num_live_points`, each optimizer has its own stopping criterion and speed/thoroughness knobs, exposed as separate `nestle_*`/`multinest_*` tool arguments so it's always clear which optimizer a given argument applies to (only the ones matching the active `optimizer` are used; the rest are silently ignored).

### Stopping criteria (the log-evidence tolerance)

This is the most important tuning knob after `num_live_points`: it directly controls how long the retrieval runs and how precise the final evidence/posterior is.

| Argument | Optimizer | Default | What it does |
|---|---|---|---|
| `nestle_tol` | nestle | `0.5` | Stops once the estimated remaining evidence contribution drops below this. **Lower = more thorough/slower** (e.g. `0.1`), **higher = stops sooner/faster but less precise** (e.g. `1.0`). |
| `multinest_evidence_tolerance` | multinest | `0.5` | Same idea, MultiNest's own name for it. `0.5` is the standard value used across exoplanet retrieval literature, don't casually raise it for a "final" science result. |

**Rule of thumb**: for a quick test, `tol`/`evidence_tolerance` of `1.0` finishes noticeably faster. For a real science result, keep the default `0.5` (or lower, e.g. `0.1`, if you specifically need a very precise evidence estimate for model comparison).

### Other speed/thoroughness knobs

| Argument | Optimizer | Default | What it does |
|---|---|---|---|
| `nestle_method` | nestle | `'multi'` | `'multi'` (default) uses MultiNest-style ellipsoidal decomposition: handles multimodal/curved posteriors correctly, use this unless you have a specific reason not to. `'single'` is faster per-iteration but only reliable for simple, unimodal posteriors. `'mcmc'` is a Metropolis random walk: slower, rarely worth choosing. |
| `multinest_sampling_efficiency` | multinest | `'parameter'` | `'parameter'` (default) is tuned for accurate posteriors, the right choice for essentially all retrievals. Don't change this unless the user specifically needs Bayesian evidence for model comparison. |
| `multinest_constant_efficiency_mode` | multinest | `False` | Set `True` to trade some sampling reliability for significantly faster runtime. Fine for a quick test, **not recommended for a final science result**. |
| `multinest_search_multi_modes` | multinest | `True` | Whether to search for and separately characterize multiple distinct posterior modes. Keep `True` (default) unless you're confident the posterior is unimodal and want a simpler/faster run. |
| `multinest_importance_sampling` | multinest | `False` | Enables MultiNest's importance nested sampling variant for more efficient evidence estimation. Off by default; only turn on if the user specifically asks for it. |
| `multinest_max_iterations` | multinest | `0` (unlimited) | Hard cap on iterations, useful for bounding worst-case runtime (e.g. before a time-limited cluster job). Setting this risks stopping before the `evidence_tolerance` criterion is actually met so prefer adjusting `evidence_tolerance` itself over capping iterations, unless runtime is the hard constraint. |

### Faster retrieval, in order of impact
1. Fewer fit parameters (`fit_params`), biggest lever, nested sampling cost grows quickly with dimensionality
2. Fewer `num_live_points` (never below 50)
3. Higher `nestle_tol`/`multinest_evidence_tolerance` (e.g. `1.0` instead of `0.5`)
4. `multinest_constant_efficiency_mode=True` (multinest only)
5. Tighter `bounds` (smaller prior volume to explore)

### More thorough/complex retrieval, in order of impact
1. More `num_live_points` (at least 500 for high-quality results)
2. Lower `nestle_tol`/`multinest_evidence_tolerance` (e.g. `0.1`)
3. `multinest_search_multi_modes=True` (multinest, default) if the posterior might be multimodal
4. Wider, more physically-exploratory `bounds`

## Strategy for Different Use Cases

### Quick Test Retrieval
**Goal**: Verify setup, check if retrieval converges, ~30 min runtime

```python
fit_params = ['planet_radius', 'T', 'H2O', 'CH4']  # Minimal set
bounds = {
    'planet_radius': [1.0, 1.5],  # Tight around expected value
    'T': [1000, 1500],            # ±250 K from expected
    'H2O': [1e-7, 1e-3],          # Narrower than default
    'CH4': [1e-8, 1e-4]
}
optimizer = 'nestle'
```

### Full Science Retrieval
**Goal**: Publication-quality results, explore full parameter space

```python
fit_params = ['planet_radius', 'T', 'H2O', 'CH4', 'CO2', 'CO', 'NH3']
bounds = {
    'planet_radius': [0.5, 2.5],
    'T': [500, 3000],
    'H2O': [1e-12, 1e-2],
    'CH4': [1e-12, 1e-2],
    'CO2': [1e-12, 1e-2],
    'CO': [1e-12, 1e-2],
    'NH3': [1e-12, 1e-2]
}
optimizer = 'multinest'  # if confirmed installed/working - otherwise 'nestle'
nlayers = 100  # Standard resolution
```

### Equilibrium Chemistry Retrieval
**Goal**: Fit thermochemical equilibrium parameters

```python
chemistry_type = 'equilibrium'
fit_params = ['planet_radius', 'T', 'metallicity', 'C_O_ratio']  # exact casing matters
bounds = {
    'planet_radius': [0.8, 1.8],
    'T': [1000, 2000],
    'metallicity': [0.1, 10.0],
    'C_O_ratio': [0.1, 2.0]
}
optimizer = 'multinest'  # if confirmed installed/working otherwise 'nestle'
```

Note: both `fit_params`/`bounds` above are optional, if omitted, `chemistry_type='equilibrium'` alone auto-generates this exact `fit_params` list and reasonable bounds. Seed the starting point via the tool's `metallicity`/`co_ratio` arguments (plain floats), which are distinct from the `C_O_ratio` fit-parameter name above.

### Free Chemistry with Specific Molecules
**Goal**: Fit a custom molecule list without hand-writing fit_params/bounds

```python
chemistry_type = 'free'
molecules = ['H2O', 'CH4', 'HCN']  # names only - seeded from a fixed lookup table
# or: molecular_abundances = {'H2O': 0.01, 'CH4': 0.0005, 'HCN': 1e-6}  # exact starting values
```
`fit_params` auto-resolves to `['planet_radius', 'T', 'H2O', 'CH4', 'HCN']` and bounds default to `[1e-9, 1e-2]` per molecule, override either explicitly if you need something different.

## Common Mistakes to Avoid

1. **Bounds too wide**: `'T': [100, 5000]` wastes time exploring unphysical regions
4. **Wrong pressure units**: TauREx uses Pa, not bar (1 bar = 1e5 Pa)
5. **Too many parameters**: Start simple, add complexity incrementally
6. **Inconsistent priors**: Fitting planet_mass but not fitting planet_radius rarely makes sense

## Background Gas Assumptions

TauREx automatically fills the remaining atmospheric composition with:
- **83% H₂** (molecular hydrogen)
- **17% He** (helium)

This is the solar H/He ratio and standard for gas giant atmospheres. If your molecular abundances sum to X, then H₂ + He = (1 - X).

**Example**:
- H₂O = 0.01 (1%)
- CH₄ = 0.001 (0.1%)
- Total specified = 0.011 (1.1%)
- Remaining = 0.989 (98.9%)
- → H₂ ≈ 0.821, He ≈ 0.168

## Runtime Estimates

Approximate retrieval times (order of magnitude):

- **Quick test** (4 params, nestle, tight bounds): ~30 min - 2 hours
- **Standard retrieval** (7 params, multinest, full bounds): ~6-24 hours
- **Complex retrieval** (10+ params, multinest): ~1-3 days
- **Equilibrium chemistry** (4-5 params, multinest): ~4-12 hours

Runtime depends on:
- Number of fit parameters (exponential scaling)
- Bound widths (wider = longer)
- Spectral data resolution (more wavelength points = longer)
- Number of atmospheric layers (default 100 is fine)
- Optimizer choice (multinest > nestle in speed)
