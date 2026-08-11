import os
import ast
from pathlib import Path

import numpy as np

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField


# Curated set of commonly-relevant species, used when the user doesn't name specific
# molecules and doesn't ask for a top-N ranking either - keeps results deterministic
# instead of dumping FastChem's full ~800-species gas-phase network.
DEFAULT_SPECIES = ['H2', 'He', 'H2O', 'CO', 'CO2', 'CH4', 'NH3', 'HCN', 'H2S', 'N2', 'Na', 'K', 'TiO', 'VO', 'FeH', 'SiO']

VALID_ELEMENT_ABUNDANCE_SOURCES = ['asplund_2021', 'asplund_2009', 'lodders_2003', 'lodders_2025']


def _fastchem_input_dir() -> Path:
    """
    Locate the fastchem_input/ directory shipped at the project root, next to workspace/.
    Computed relative to this file's own location (not base_directory/pwd) since this data
    is bundled with the project rather than downloaded per-machine like TauREx's line lists,
    so there's no need for the agent to locate or supply paths itself.
    """
    input_dir = Path(__file__).resolve().parent.parent.parent / 'fastchem_input'
    if not input_dir.is_dir():
        raise FileNotFoundError(
            f"fastchem_input/ directory not found at {input_dir}. "
            "Expected it at the project root, alongside workspace/."
        )
    return input_dir


def _normalize_profile(temperature, pressure):
    """Parse temperature/pressure (float, list, or string form of either) into equal-length lists."""
    def to_list(value, name):
        if isinstance(value, str):
            try:
                value = ast.literal_eval(value)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Failed to parse {name} string: {value}. Error: {e}")
        if isinstance(value, (int, float)):
            return [float(value)]
        return [float(v) for v in value]

    t_list = to_list(temperature, "temperature")
    p_list = to_list(pressure, "pressure")

    if len(t_list) == 1 and len(p_list) > 1:
        t_list = t_list * len(p_list)
    elif len(p_list) == 1 and len(t_list) > 1:
        p_list = p_list * len(t_list)
    elif len(t_list) != len(p_list):
        raise ValueError(
            f"temperature ({len(t_list)} value(s)) and pressure ({len(p_list)} value(s)) must be the "
            "same length, or one of them must be a single value."
        )

    return t_list, p_list


def _apply_element_abundances(fastchem, metallicity, co_ratio, custom_element_abundances):
    """Scale the default (solar) element abundances by metallicity/C-O ratio, matching the
    approach in FastChem's own example scripts (direct multiplication of the abundance array,
    not a log-space operation)."""
    import pyfastchem

    abundances = np.array(fastchem.getElementAbundances())

    index_H = fastchem.getElementIndex('H')
    index_He = fastchem.getElementIndex('He')
    index_e = fastchem.getElementIndex('e-')
    skip = {index_H, index_He, index_e}

    if metallicity != 1.0:
        for i in range(len(abundances)):
            if i not in skip:
                abundances[i] *= metallicity

    if co_ratio is not None:
        index_C = fastchem.getElementIndex('C')
        index_O = fastchem.getElementIndex('O')
        abundances[index_C] = abundances[index_O] * co_ratio

    if custom_element_abundances:
        for symbol, value in custom_element_abundances.items():
            idx = fastchem.getElementIndex(symbol)
            if idx == pyfastchem.FASTCHEM_UNKNOWN_SPECIES:
                raise ValueError(f"Unknown element symbol: {symbol!r}")
            abundances[idx] = value

    fastchem.setElementAbundances(abundances)


class RunFastChemEquilibriumTool(BaseTool):
    """
    Predict gas-phase chemical-equilibrium mixing ratios for a planetary atmosphere using FastChem.

    Independent of the TauREx tools - this only needs a temperature (and optionally pressure),
    not a full planet/star/opacity setup. Use this when the user asks something like
    "what molecules would I expect at equilibrium for a planet at 1500 K" - no line lists or
    SetTaurexPaths required.
    """

    temperature: float | list[float] | str = RuntimeField(
        description="Temperature in Kelvin. A single value (e.g. 1500) or a list of values (e.g. [500, 1000, 1500]) to compute a profile. Can be a string representation of a list."
    )
    pressure: float | list[float] | str = RuntimeField(
        default=1.0,
        description="Pressure in bar. A single value or a list matching temperature's length (if one of temperature/pressure is a single value and the other a list, the single value is broadcast to match)."
    )
    metallicity: float = RuntimeField(
        default=1.0,
        description="Metallicity relative to solar (1.0 = solar). Scales all element abundances except H and He. E.g. 10.0 = 10x solar metallicity."
    )
    co_ratio: float | None = RuntimeField(
        default=None,
        description="Carbon-to-oxygen ratio. If not given, uses the solar C/O from the abundance source. Solar value is ~0.55."
    )
    element_abundance_source: str = RuntimeField(
        default="asplund_2021",
        description="Which solar composition reference to use as the starting point before metallicity/co_ratio scaling: 'asplund_2021' (default, most recent), 'asplund_2009', 'lodders_2003', or 'lodders_2025'."
    )
    include_condensates: bool = RuntimeField(
        default=False,
        description="Whether to also account for condensation (rainout) chemistry. Only set this if the user explicitly asks about clouds/condensates - adds significant complexity and is off by default."
    )
    species: list[str] | str | None = RuntimeField(
        default=None,
        description="Specific molecule/species names to report mixing ratios for, e.g. ['H2O', 'CH4']. If not given, uses top_n if provided, otherwise a fixed default set of commonly-relevant species (so results are deterministic instead of the agent inventing which species to report)."
    )
    top_n: int | None = RuntimeField(
        default=None,
        description="If species is not given, report the N most abundant gas species instead of the fixed default set. Ignored if species is given."
    )
    filename: str = RuntimeField(
        default='',
        description="Used only when temperature/pressure is a profile (list, not a single value): saves a plot as '{filename}_fastchem_mixing_ratios.png' and the underlying arrays as '{filename}_fastchem_*.npy'. Not used for a single T/P point, which is returned as text."
    )
    base_directory: str = StateField()

    def _run(self):
        species = self.species
        if isinstance(species, str):
            try:
                species = ast.literal_eval(species)
            except (ValueError, SyntaxError):
                species = [species]  # bare single species name passed as a plain string

        return generate_fastchem_equilibrium(
            temperature=self.temperature,
            pressure=self.pressure,
            metallicity=self.metallicity,
            co_ratio=self.co_ratio,
            element_abundance_source=self.element_abundance_source,
            include_condensates=self.include_condensates,
            species=species,
            top_n=self.top_n,
            filename=self.filename,
            base_directory=self.base_directory,
        )


def generate_fastchem_equilibrium(
    temperature,
    pressure=1.0,
    metallicity=1.0,
    co_ratio=None,
    custom_element_abundances=None,
    element_abundance_source="asplund_2021",
    include_condensates=False,
    species=None,
    top_n=None,
    filename='',
    base_directory=''):
    """
    Run a FastChem gas-phase chemical-equilibrium calculation and report mixing ratios.

    For a single (temperature, pressure) point, returns a formatted text summary.
    For a profile (list-valued temperature and/or pressure), also saves a mixing-ratio-vs-pressure
    plot and the underlying arrays, since a full profile isn't meaningfully readable as text.
    Please redirect to https://newstrangeworlds.github.io/FastChem/ if an error occurs.
    """
    import pyfastchem

    if element_abundance_source not in VALID_ELEMENT_ABUNDANCE_SOURCES:
        raise ValueError(
            f"Unknown element_abundance_source: {element_abundance_source!r}. "
            f"Must be one of {VALID_ELEMENT_ABUNDANCE_SOURCES}."
        )

    t_list, p_list = _normalize_profile(temperature, pressure)

    input_dir = _fastchem_input_dir()
    element_file = input_dir / 'element_abundances' / f'{element_abundance_source}_extended.dat'
    logk_file = input_dir / 'logK' / 'logK_extended.dat'

    if include_condensates:
        cond_file = input_dir / 'logK' / 'logK_condensates_extended.dat'
        fastchem = pyfastchem.FastChem(str(element_file), str(logk_file), str(cond_file), 0)
    else:
        fastchem = pyfastchem.FastChem(str(element_file), str(logk_file), 0)

    _apply_element_abundances(fastchem, metallicity, co_ratio, custom_element_abundances)

    input_data = pyfastchem.FastChemInput()
    output_data = pyfastchem.FastChemOutput()
    input_data.temperature = t_list
    input_data.pressure = p_list
    if include_condensates:
        input_data.equilibrium_condensation = True

    flag = fastchem.calcDensities(input_data, output_data)

    number_densities = np.array(output_data.number_densities)  # shape (n_points, n_gas_species)
    total_density = number_densities.sum(axis=1)
    mixing_ratios = number_densities / total_density[:, None]

    convergence_msg = pyfastchem.FASTCHEM_MSG[max(output_data.fastchem_flag)]
    all_elements_conserved = bool(np.min(output_data.element_conserved))
    convergence_note = convergence_msg + ("" if all_elements_conserved else " (WARNING: element conservation failed)")

    def _resolve_by_name(names):
        found, missing = [], []
        for name in names:
            hill_name = fastchem.convertToHillNotation(name)
            idx = fastchem.getGasSpeciesIndex(hill_name)
            if idx == pyfastchem.FASTCHEM_UNKNOWN_SPECIES:
                missing.append(name)
            else:
                found.append((name, idx))
        return found, missing

    # Resolve which species to report: explicit request > top_n ranking > fixed default set
    if species is not None:
        resolved, not_found = _resolve_by_name(species)
    elif top_n:
        max_mix = mixing_ratios.max(axis=0)
        top_indices = [int(i) for i in np.argsort(max_mix)[::-1][:top_n]]
        # Already have indices from ranking - pair symbol with the descriptive name for
        # readability (raw Hill-notation symbols alone, e.g. "H2O1", read poorly).
        resolved = [
            (f"{fastchem.getGasSpeciesSymbol(i)} ({fastchem.getGasSpeciesName(i)})", i)
            for i in top_indices
        ]
        not_found = []
    else:
        resolved, not_found = _resolve_by_name(DEFAULT_SPECIES)

    n_points = len(t_list)

    if n_points == 1:
        lines = [f"FastChem equilibrium at T={t_list[0]:.1f} K, P={p_list[0]:.4g} bar"]
        lines.append(f"Convergence: {convergence_note}")
        lines.append("")
        lines.append("Mixing ratios (mole fraction):")
        for sp, idx in resolved:
            lines.append(f"  {sp:8s}: {mixing_ratios[0, idx]:.4e}")
        if not_found:
            lines.append("")
            lines.append(f"Not found in FastChem's species list: {', '.join(not_found)}")
        return "\n".join(lines)

    # Profile case: save a plot + the raw arrays, since a list of points isn't readable as text
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    species_names = [sp for sp, idx in resolved]
    profile_mixing_ratios = np.array([mixing_ratios[:, idx] for sp, idx in resolved]).T  # (n_points, n_species)

    plt.figure(figsize=(8, 6))
    for sp, idx in resolved:
        plt.plot(mixing_ratios[:, idx], p_list, label=sp)
    plt.yscale('log')
    plt.xscale('log')
    plt.gca().invert_yaxis()
    plt.xlabel('Mixing ratio')
    plt.ylabel('Pressure (bar)')
    plt.title('FastChem Equilibrium Chemistry')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(base_directory, f'{filename}_fastchem_mixing_ratios.png'))
    plt.close()

    np.save(os.path.join(base_directory, f'{filename}_fastchem_temperature.npy'), np.array(t_list))
    np.save(os.path.join(base_directory, f'{filename}_fastchem_pressure.npy'), np.array(p_list))
    np.save(os.path.join(base_directory, f'{filename}_fastchem_mixing_ratios.npy'), profile_mixing_ratios)

    result = f"FastChem equilibrium profile computed for {n_points} points.\n"
    result += f"Convergence: {convergence_note}\n\n"
    result += f"Plot saved to {filename}_fastchem_mixing_ratios.png\n"
    result += f"Data saved to {filename}_fastchem_temperature.npy, {filename}_fastchem_pressure.npy, "
    result += f"{filename}_fastchem_mixing_ratios.npy\n"
    result += f"Mixing ratios array columns, in order: {', '.join(species_names)}\n"
    if not_found:
        result += f"\nNot found in FastChem's species list: {', '.join(not_found)}\n"
    return result
