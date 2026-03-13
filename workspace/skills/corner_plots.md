# Creating Corner Plots from Retrieval Results

After running a retrieval with `SimulateTaurexRetrieval`, you can create corner plots from the saved posterior samples.

## Basic Corner Plot

```python
import numpy as np
import corner
import matplotlib.pyplot as plt

# Load posterior samples
samples = np.load('retrieval_output_samples.npy')
weights = np.load('retrieval_output_weights.npy')

# Define parameter labels (must match order in fit_params)
labels = ['planet_radius', 'T', 'H2O', 'CH4', 'CO2', 'CO', 'NH3']

# Create corner plot
fig = corner.corner(
    samples,
    weights=weights,
    labels=labels,
    quantiles=[0.16, 0.5, 0.84],
    show_titles=True,
    title_fmt=".3g",
    bins=60
)

plt.savefig('corner_plot.png', dpi=200, bbox_inches='tight')
plt.close()
```

## Plotting Subset of Parameters

For large retrievals, you may want to plot only selected parameters:

```python
# Select only chemistry parameters to plot
selected_params = ['H2O', 'CH4', 'CO2']
idx = [labels.index(p) for p in selected_params]

# Subset the samples
plot_samples = samples[:, idx]
plot_labels = [labels[i] for i in idx]

fig = corner.corner(
    plot_samples,
    weights=weights,
    labels=plot_labels,
    quantiles=[0.16, 0.5, 0.84],
    show_titles=True,
    title_fmt=".3g",
    bins=60
)

plt.savefig('chemistry_corner.png', dpi=200, bbox_inches='tight')
plt.close()
```

## Customization Options

### Quantiles
- **quantiles**: Confidence intervals to display
  - `[0.16, 0.5, 0.84]` = 1σ (68% confidence)
  - `[0.025, 0.5, 0.975]` = 2σ (95% confidence)

### Formatting
- **title_fmt**: Number format for titles (e.g., ".3g" for 3 sig figs, ".4g" for 4)
- **bins**: Number of bins for histograms (default: 60)
- **color**: Plot color (default: 'blue')
- **smooth**: Smoothing factor for 2D histograms (0-2, default: 1.0)

### Plot Appearance
- **label_kwargs**: Dictionary for axis label styling (e.g., `{'fontsize': 14}`)
- **title_kwargs**: Dictionary for title styling (e.g., `{'fontsize': 12}`)

## Publication-Quality Plot

```python
fig = corner.corner(
    samples,
    weights=weights,
    labels=labels,
    quantiles=[0.16, 0.5, 0.84],
    show_titles=True,
    title_fmt=".4g",
    bins=60,
    color='navy',
    smooth=1.0,
    label_kwargs={'fontsize': 14},
    title_kwargs={'fontsize': 12}
)

plt.savefig('corner_plot.png', dpi=300, bbox_inches='tight')
plt.close()
```

## Reusable Function Template

```python
def plot_corner(samples_file, weights_file, labels, output_file='corner.png',
                selected_params=None, dpi=200, bins=60):
    """
    Create corner plot from retrieval outputs.

    Parameters:
    - samples_file: Path to .npy file with samples
    - weights_file: Path to .npy file with weights
    - labels: List of parameter names (matching fit_params order)
    - output_file: Output filename
    - selected_params: Optional list of parameter names to plot (subset)
    - dpi: Image resolution (default: 200, use 300 for publication)
    - bins: Number of histogram bins
    """
    import numpy as np
    import corner
    import matplotlib.pyplot as plt

    # Load data
    samples = np.load(samples_file)
    weights = np.load(weights_file)

    # Validate inputs
    if samples.ndim != 2:
        raise ValueError("samples must be 2D array (nsamples, nparams)")
    if len(labels) != samples.shape[1]:
        raise ValueError("labels length must match number of parameters")

    # Optionally select subset
    if selected_params is not None:
        idx = [labels.index(p) for p in selected_params]
        samples = samples[:, idx]
        labels = [labels[i] for i in idx]

    # Create plot
    fig = corner.corner(
        samples,
        weights=weights,
        labels=labels,
        quantiles=[0.16, 0.5, 0.84],
        show_titles=True,
        title_fmt=".4g",
        bins=bins
    )

    plt.savefig(output_file, dpi=dpi, bbox_inches='tight')
    plt.close()

    return fig

# Example usage:
plot_corner(
    'retrieval_output_samples.npy',
    'retrieval_output_weights.npy',
    labels=['planet_radius', 'T', 'H2O', 'CH4', 'CO2', 'CO', 'NH3'],
    selected_params=['H2O', 'CH4', 'CO2'],  # Plot only chemistry
    output_file='chemistry_corner.png',
    dpi=300
)
```

## Important Notes

- **Label order**: The `labels` list must exactly match the order of `fit_params` used in the retrieval
- **Weights required**: Always use weights for nested sampling posteriors (ensures proper posterior representation)
- **DPI recommendations**:
  - 200 dpi: Standard figures
  - 300 dpi: Publication quality
  - 500+ dpi: Usually excessive, large file sizes
- **Always close plots**: Use `plt.close()` after saving to free memory
- **Validate inputs**: Check array shapes before plotting to catch errors early
