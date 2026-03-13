# Retrieval Best Practices

This skill provides guidance on setting up atmospheric retrievals for optimal performance and physically meaningful results.

## Parameter Bounds

### General Principles

The bounds you set directly control:
1. **Runtime**: Wider bounds = longer retrieval time
2. **Physical plausibility**: Bounds should reflect realistic atmospheric conditions
3. **Convergence**: Overly wide bounds can make it harder to find solutions

### Molecular Abundance Bounds

**Chemical abundance limits (log₁₀ mixing ratio)**:
- **Lower limit**: `-9` (i.e., 10⁻⁹)
  - Abundances below 10⁻⁹ have essentially no measurable spectroscopic effect
  - Values below this are computationally wasteful and practically indistinguishable
  - **Never use bounds lower than -9**

- **Upper limit**: `-1` or `-2` (i.e., 10⁻¹ or 10⁻²)
  - Total mixing ratios of all species must sum to ≤ 1.0
  - If atmosphere is H₂/He dominated (typical for gas giants), leave room for background gases
  - TauREx default: 83% H₂ + 17% He fills remaining fraction
  - **Use -2 (10⁻²) for H₂/He atmospheres** to ensure total doesn't exceed 1
  - **Use -1 (10⁻¹) only if** you have few absorbers or non-H₂/He atmosphere

**Default bounds**: `[1e-9, 1e-2]` or in log₁₀ space: `[-9, -2]`

### Temperature Bounds

**For quick tests** (when you know approximate T):
- Use tight bounds: `T_expected ± 500 K`
- Example: If T ≈ 1500 K, use `[1000, 2000]`

**For genuine exploration**:
- Use wide physically plausible bounds: `[500, 3000]` K
- Lower bound: ~500 K (cooler planets, condensation limits)
- Upper bound: ~3000 K (ultra-hot Jupiters)

### Radius Bounds

**Planet radius** (Jupiter radii):
- Quick test: `R_expected ± 0.5` RJup
- Full exploration: `[0.5, 2.5]` RJup
- Consider: Radius changes with reference pressure and atmospheric scale height

**Star radius** (Solar radii):
- Usually well-constrained from stellar characterization
- Tight bounds: `R_star ± 0.2` Rsun
- Only fit if stellar parameters are uncertain

### Equilibrium Chemistry Parameters

**Metallicity** (solar units):
- Quick test: `[0.5, 3.0]` (subsolar to 3× solar)
- Full exploration: `[0.1, 10.0]` (0.1× to 10× solar)

**C/O ratio**:
- Quick test: `[0.3, 1.0]` (typical range)
- Full exploration: `[0.1, 2.0]` (subsolar to supersolar)

## Optimizer Selection

**IMPORTANT**: PyMultiNest requires compiled Fortran libraries that can be difficult to install. If you encounter `"No module named 'pymultinest'"` errors, use `nestle` instead, which works out-of-the-box.

### nestle (**RECOMMENDED FOR MOST USERS**)
- **Pros**: Pure Python, easy to install, reliable convergence, works out-of-the-box
- **Cons**: Slower than multinest for complex retrievals
- **Use for**: **All retrievals unless multinest is confirmed working**
- **Installation**: Already included in ASTER dependencies
- **Status**: **Default choice** - always available

### multinest (Publication Standard)
- **Pros**: State-of-the-art nested sampling, most widely used in exoplanet community, faster than nestle
- **Cons**: **Difficult to install** - requires compiled Fortran libraries (MultiNest + pymultinest)
- **Use for**: Final publication-quality results **only if successfully installed**
- **Installation**: Requires system-level MultiNest library + `pymultinest` Python package
- **Check availability**: Try running a test retrieval first - if it fails with module errors, use nestle

### ultranest
- **Pros**: Faster than MultiNest, modern algorithm, pure Python
- **Cons**: Less extensively tested in exoplanet literature, not currently in ASTER dependencies
- **Use for**: When speed is important and you can install additional packages
- **Status**: Gaining adoption but not yet standard

### polychord / dipolychord
- **Pros**: Efficient for high-dimensional parameter spaces
- **Cons**: Less commonly used for exoplanet retrievals, requires additional installation
- **Use for**: Very high-dimensional problems (>20 parameters)

**Recommendation order**:
1. **nestle** - **Use this by default** (always works)
2. **multinest** - Only if you've confirmed it's installed and working
3. **ultranest** - Only if you need speed and are willing to install extra packages

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
optimizer = 'nestle'  # Fast, easy
```

### Full Science Retrieval
**Goal**: Publication-quality results, explore full parameter space

```python
fit_params = ['planet_radius', 'T', 'H2O', 'CH4', 'CO2', 'CO', 'NH3']
bounds = {
    'planet_radius': [0.5, 2.5],
    'T': [500, 3000],
    'H2O': [1e-9, 1e-2],
    'CH4': [1e-9, 1e-2],
    'CO2': [1e-9, 1e-2],
    'CO': [1e-9, 1e-2],
    'NH3': [1e-9, 1e-2]
}
optimizer = 'multinest'  # Best sampling
nlayers = 100  # Standard resolution
```

### Equilibrium Chemistry Retrieval
**Goal**: Fit thermochemical equilibrium parameters

```python
retrieval_mode = 'equilibrium'
fit_params = ['planet_radius', 'T', 'metallicity', 'c_o_ratio']
bounds = {
    'planet_radius': [0.8, 1.8],
    'T': [1000, 2000],
    'metallicity': [0.1, 10.0],
    'c_o_ratio': [0.1, 2.0]
}
optimizer = 'multinest'
```

## Common Mistakes to Avoid

1. **Bounds too wide**: `'T': [100, 5000]` wastes time exploring unphysical regions
2. **Abundances too low**: `'H2O': [1e-12, 1e-2]` - below 1e-9 is meaningless
3. **Abundances too high**: `'H2O': [1e-9, 1e0]` - might exceed 100% total
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

## Tips for Faster Convergence

1. Start with tight bounds based on literature values
2. Use fewer molecules initially (just H₂O, CH₄ for hot Jupiters)
3. Reduce `nlayers` to 50 for testing (100 for science runs)
4. Use `nestle` for initial tests before switching to `multinest`
5. Check convergence plots - if exploring empty parameter space, tighten bounds
