import numpy as np
import matplotlib.pyplot as plt
import corner
import os
from pathlib import Path

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField


class PlotCornerPosteriors(BaseTool):
    """
    Create a corner plot from TauREx retrieval posterior samples.

    This tool generates publication-quality corner plots showing parameter posterior distributions
    and correlations from atmospheric retrieval results. It uses the posterior samples and weights
    saved by SimulateTaurexRetrieval.

    **Typical workflow**:
    1. Run SimulateTaurexRetrieval (this automatically creates a corner plot)
    2. Use this tool to create custom corner plots with:
       - Selected parameter subsets
       - Custom formatting options
       - Higher DPI for publications
       - Different quantile intervals

    **Input files** (created by SimulateTaurexRetrieval):
    - `{retrieval_basename}_samples.npy` - Posterior samples array (n_samples, n_params)
    - `{retrieval_basename}_weights.npy` - Nested sampling weights array (n_samples,)

    **Parameter labels**:
    - Must match the order of columns in the samples array
    - Should match the fit_params used in the retrieval
    - Example: ['planet_radius', 'T', 'H2O', 'CH4', 'CO2', 'CO', 'NH3']
    """

    # Required parameters
    retrieval_basename: str | None = RuntimeField(
        default=None,
        description="Base name of the retrieval output files (e.g., 'wasp39b_retrieval'). The tool will load {retrieval_basename}_samples.npy and {retrieval_basename}_weights.npy. Path should be relative to base_directory. REQUIRED."
    )

    labels: list | str | None = RuntimeField(
        default=None,
        description="List of parameter names corresponding to the columns in the samples array. Can be a Python list or string representation. Example: ['planet_radius', 'T', 'H2O', 'CH4', 'CO2'] or \"['planet_radius', 'T', 'H2O']\". REQUIRED - must match fit_params from retrieval."
    )

    # Optional parameters
    output_path: str = RuntimeField(
        default=None,
        description="Path where the corner plot will be saved (relative to base_directory). If None, saves as '{retrieval_basename}_corner_custom.png' in the same directory as the samples."
    )

    selected_params: list | str | None = RuntimeField(
        default=None,
        description="Optional subset of parameter names to plot. Must be a subset of 'labels'. If None, plots all parameters. Example: ['H2O', 'CH4', 'CO2'] to plot only molecular abundances."
    )

    quantiles: list | str = RuntimeField(
        default="[0.16, 0.5, 0.84]",
        description="Quantiles to display on histograms. Default [0.16, 0.5, 0.84] shows 1-sigma confidence intervals. Can be a Python list or string representation."
    )

    title_fmt: str = RuntimeField(
        default=".4g",
        description="Format string for parameter values in titles (e.g., '.3g', '.4f', '.2e'). Default '.4g' shows 4 significant figures."
    )

    bins: int = RuntimeField(
        default=60,
        description="Number of bins for histograms. Default 60. Increase for smoother plots, decrease for faster rendering."
    )

    dpi: int = RuntimeField(
        default=200,
        description="Resolution of saved figure in dots per inch. Default 200. Use 300-600 for publication quality."
    )

    show_titles: bool = RuntimeField(
        default=True,
        description="Whether to show parameter values and uncertainties in subplot titles. Default True."
    )

    # State fields (not exposed to LLM)
    base_directory: str = StateField(
        default=".",
        description="Base working directory for file operations"
    )

    def _run(self) -> str:
        """Execute the corner plot generation."""

        # Validate required parameters
        if self.retrieval_basename is None:
            return "Error: retrieval_basename is required. Please provide the base name of your retrieval output files."

        if self.labels is None:
            return "Error: labels is required. Please provide the list of parameter names that match the columns in your samples array."

        # Parse string representations to Python objects if needed
        labels = self._parse_list_parameter(self.labels, "labels")
        if isinstance(labels, str) and labels.startswith("Error:"):
            return labels

        quantiles = self._parse_list_parameter(self.quantiles, "quantiles")
        if isinstance(quantiles, str) and quantiles.startswith("Error:"):
            return quantiles

        selected_params = None
        if self.selected_params is not None:
            selected_params = self._parse_list_parameter(self.selected_params, "selected_params")
            if isinstance(selected_params, str) and selected_params.startswith("Error:"):
                return selected_params

        # Construct file paths
        samples_path = os.path.join(self.base_directory, f"{self.retrieval_basename}_samples.npy")
        weights_path = os.path.join(self.base_directory, f"{self.retrieval_basename}_weights.npy")

        # Load samples and weights
        try:
            samples = np.load(samples_path)
        except FileNotFoundError:
            return f"Error: Could not find samples file at {samples_path}. Please check that retrieval_basename is correct and the file exists."
        except Exception as e:
            return f"Error loading samples: {e}"

        try:
            weights = np.load(weights_path)
        except FileNotFoundError:
            return f"Error: Could not find weights file at {weights_path}. Please check that retrieval_basename is correct and the file exists."
        except Exception as e:
            return f"Error loading weights: {e}"

        # Validate data shapes
        if samples.ndim != 2:
            return f"Error: Samples array must be 2D (n_samples, n_params), but got shape {samples.shape}"

        if weights.ndim != 1:
            return f"Error: Weights array must be 1D (n_samples,), but got shape {weights.shape}"

        if samples.shape[0] != weights.shape[0]:
            return f"Error: Number of samples ({samples.shape[0]}) does not match number of weights ({weights.shape[0]})"

        if len(labels) != samples.shape[1]:
            return f"Error: Number of labels ({len(labels)}) does not match number of parameters in samples ({samples.shape[1]})"

        # Select subset of parameters if requested
        if selected_params is not None:
            try:
                # Find indices of selected parameters
                indices = [labels.index(p) for p in selected_params]
                plot_samples = samples[:, indices]
                plot_labels = selected_params
            except ValueError as e:
                return f"Error: Selected parameter not found in labels. {e}"
        else:
            plot_samples = samples
            plot_labels = labels

        # Determine output path
        if self.output_path is None:
            # Save in same directory as retrieval outputs
            output_path = os.path.join(
                self.base_directory,
                f"{self.retrieval_basename}_corner_custom.png"
            )
        else:
            output_path = os.path.join(self.base_directory, self.output_path)

        # Create output directory if needed
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        # Generate corner plot
        try:
            import matplotlib
            matplotlib.use('Agg')  # Use non-interactive backend

            fig = corner.corner(
                plot_samples,
                weights=weights,
                labels=plot_labels,
                quantiles=quantiles,
                show_titles=self.show_titles,
                title_fmt=self.title_fmt,
                bins=self.bins,
                plot_datapoints=False,  # Don't plot individual points (cleaner for large samples)
                smooth=True,  # Smooth contours
                smooth1d=True,  # Smooth 1D histograms
            )

            # Save figure
            plt.savefig(output_path, dpi=self.dpi, bbox_inches='tight')
            plt.close(fig)

        except Exception as e:
            return f"Error generating corner plot: {e}"

        # Generate summary report
        n_params = len(plot_labels)
        n_samples = samples.shape[0]

        result = [
            f"Successfully generated corner plot!",
            f"",
            f"Input files:",
            f"  - Samples: {samples_path}",
            f"  - Weights: {weights_path}",
            f"",
            f"Plot details:",
            f"  - Number of parameters: {n_params}",
            f"  - Number of samples: {n_samples}",
            f"  - Parameters plotted: {', '.join(plot_labels)}",
            f"  - Quantiles: {quantiles}",
            f"  - Bins: {self.bins}",
            f"  - DPI: {self.dpi}",
            f"",
            f"Output saved to: {output_path}",
        ]

        return "\n".join(result)

    def _parse_list_parameter(self, param, param_name: str):
        """Parse a parameter that can be either a list or string representation of a list."""
        if isinstance(param, list):
            return param
        elif isinstance(param, str):
            try:
                import ast
                parsed = ast.literal_eval(param)
                if not isinstance(parsed, list):
                    return f"Error: {param_name} must be a list, got {type(parsed).__name__}"
                return parsed
            except (ValueError, SyntaxError) as e:
                return f"Error parsing {param_name}: {e}. Please provide a valid list or string representation of a list."
        else:
            return f"Error: {param_name} must be a list or string, got {type(param).__name__}"
