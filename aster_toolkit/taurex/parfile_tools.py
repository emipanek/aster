import os
import ast

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField


# Fixed typical mixing ratios, used as seed values when the user names molecules without giving
# exact abundances, and for the 'basic' fixed chemistry (so the agent isn't inventing numbers).
DEFAULT_MOLECULE_ABUNDANCES = {
    'H2O': 0.02,
    'CH4': 0.001,
    'CO2': 0.0001,
    'CO': 0.001,
    'NH3': 0.0001,
    'HCN': 1e-5,
    'H2S': 1e-5,
    'SO2': 1e-6,
    'C2H2': 1e-6,
    'Na': 1e-6,
    'K': 1e-7,
    'TiO': 1e-7,
    'VO': 1e-8,
}

BASIC_MODEL_MOLECULES = ['H2O', 'CH4', 'CO2', 'CO', 'NH3']

# Retrieval jobs written to a .par file run on a cluster, which has far more compute available
# than an in-session local retrieval - so use a higher live-point floor than the local
# SimulateTaurexRetrieval tool default (100).
MIN_CLUSTER_LIVE_POINTS = 500
RECOMMENDED_CLUSTER_LIVE_POINTS = 1000


def resolve_free_gas_list(molecules=None, molecular_abundances=None):
    """
    Resolve which gases (and mixing ratios) to write for chemistry_type='free'.
    Precedence: explicit molecular_abundances > named molecules (looked up in the fixed
    default table) > fixed basic model (5 default molecules).
    """
    if molecular_abundances is not None:
        return list(molecular_abundances.items())
    if molecules is not None:
        unknown = [m for m in molecules if m not in DEFAULT_MOLECULE_ABUNDANCES]
        if unknown:
            raise ValueError(
                f"No default abundance available for molecule(s) {unknown}. "
                f"Known molecules: {sorted(DEFAULT_MOLECULE_ABUNDANCES)}. "
                "Specify an exact value via molecular_abundances instead."
            )
        return [(m, DEFAULT_MOLECULE_ABUNDANCES[m]) for m in molecules]
    return [(m, DEFAULT_MOLECULE_ABUNDANCES[m]) for m in BASIC_MODEL_MOLECULES]


def default_fit_params_for_chemistry(chemistry_type, molecules=None, molecular_abundances=None):
    """Auto-generate a fit_params list from the chemistry configuration, for retrieval parameter files."""
    if chemistry_type == "equilibrium":
        return ["planet_radius", "T", "metallicity", "C_O_ratio"]
    gas_list = (
        resolve_free_gas_list(molecules, molecular_abundances)
        if chemistry_type == "free"
        else [(m, DEFAULT_MOLECULE_ABUNDANCES[m]) for m in BASIC_MODEL_MOLECULES]
    )
    return ["planet_radius", "T"] + [name for name, _ in gas_list]


def default_bounds_for_fit_params(fit_params, planet_radius=1.0, planet_temp=1500.0):
    """Reasonable default [low, high] bounds per fit parameter, mirroring SimulateTaurexRetrieval."""
    bounds = {}
    for p in fit_params:
        if p == 'planet_radius':
            bounds[p] = [max(0.1, planet_radius - 0.5), planet_radius + 0.5]
        elif p == 'T':
            bounds[p] = [max(500, planet_temp - 500), planet_temp + 500]
        elif p == 'planet_mass':
            bounds[p] = [0.1, 5.0]
        elif p == 'metallicity':
            bounds[p] = [0.1, 10.0]
        elif p == 'C_O_ratio':
            bounds[p] = [0.1, 2.0]
        else:
            # any other fit param is assumed to be a molecule mixing ratio
            bounds[p] = [1e-12, 1e-2]
    return bounds


def _chemistry_section(chemistry_type, molecules, molecular_abundances, metallicity, co_ratio):
    if chemistry_type == "equilibrium":
        if molecules is not None or molecular_abundances is not None:
            raise ValueError(
                "chemistry_type='equilibrium' ignores 'molecules'/'molecular_abundances'; "
                "remove them or set chemistry_type='free' to specify molecules directly."
            )
        # chemistry_type=equilibrium resolves to acepython.taurex3.ACEChemistry (its
        # input_keywords() are 'ace'/'acepython'/'equilibrium' - verified against the installed
        # package, this is TauREx's ACE thermochemical equilibrium chemistry, not FastChem).
        # ACEChemistry.__init__ takes 'metallicity' directly, but the C/O ratio is set via a
        # **kwargs entry named 'C_ratio' (= f"{element}_{ratio_element}_ratio" with the default
        # ratio_element='O') - a bare 'co_ratio' key is silently dropped (accepted by TauREx's
        # config factory since the class has **kwargs, then ignored inside __init__ because it
        # doesn't match the internal ratio-setter name), so it must be written as C_ratio here.
        lines = [
            "[Chemistry]",
            "chemistry_type = equilibrium",
            f"metallicity = {metallicity}",
            f"C_ratio = {co_ratio}",
        ]
        return "\n".join(lines)

    if chemistry_type == "basic":
        if molecules is not None or molecular_abundances is not None:
            raise ValueError(
                "chemistry_type='basic' uses the fixed default molecule set and ignores "
                "'molecules'/'molecular_abundances'; set chemistry_type='free' to customize it."
            )
        gas_list = [(m, DEFAULT_MOLECULE_ABUNDANCES[m]) for m in BASIC_MODEL_MOLECULES]
    elif chemistry_type == "free":
        gas_list = resolve_free_gas_list(molecules, molecular_abundances)
    else:
        raise ValueError(f"Unknown chemistry_type: {chemistry_type!r}. Must be 'basic', 'free', or 'equilibrium'.")

    lines = ["[Chemistry]", "chemistry_type = taurex", "fill_gases = H2,He", "ratio = 0.17", ""]
    for name, mix_ratio in gas_list:
        lines += [f"    [[{name}]]", "    gas_type = constant", f"    mix_ratio = {mix_ratio}", ""]
    return "\n".join(lines).rstrip()


def _fitting_section(fit_params, bounds):
    lines = ["[Fitting]"]
    for p in fit_params:
        lines.append(f"{p}:fit = True")
        if p in bounds:
            low, high = bounds[p]
            lines.append(f"{p}:bounds = {low}, {high}")
        lines.append("")
    return "\n".join(lines).rstrip()


def generate_taurex_parfile(
    xsec_path,
    cia_path,
    model_type="transmission",
    chemistry_type="basic",
    molecules=None,
    molecular_abundances=None,
    metallicity=1.0,
    co_ratio=0.54,
    star_radius=1.0,
    star_temp=5500.0,
    planet_radius=1.0,
    planet_mass=1.0,
    planet_temp=1500.0,
    atm_min_pressure=1e-1,
    atm_max_pressure=1e6,
    nlayers=100,
    observation_path=None,
    retrieval=False,
    fit_params=None,
    bounds=None,
    optimizer="nestle",
    num_live_points=RECOMMENDED_CLUSTER_LIVE_POINTS,
    multinest_path=None,
    multinest_search_multi_modes=True,
    multinest_resume=False,
    multinest_importance_sampling=False,
    filename="taurex_model",
    base_directory=""):
    """
    Generate a TauREx CLI parameter (.par) file - the recommended way to launch a TauREx job
    (forward model or retrieval) on a cluster via `taurex -i {filename}.par`, rather than through
    ASTER's own Python tools which run locally.

    Chemistry configuration (chemistry_type):
    - 'basic': fixed default 5-molecule set (H2O, CH4, CO2, CO, NH3).
    - 'free': custom molecules (names looked up in a fixed seed-abundance table) or
      molecular_abundances (exact values) - same precedence as the other ASTER TauREx tools.
    - 'equilibrium': ACE thermochemical equilibrium chemistry (acepython.taurex3.ACEChemistry,
      built into taurex/acepython - no extra plugin needed), seeded from metallicity/co_ratio.

    If retrieval=True, an [Optimizer]/[Fitting] block is added (with observation_path required),
    turning this into a retrieval job rather than a one-off forward model. If optimizer='multinest',
    multinest_path/multinest_search_multi_modes/multinest_resume/multinest_importance_sampling are
    also written into [Optimizer] (multinest_path is required in that case).
    """
    if retrieval and not observation_path:
        raise ValueError("retrieval=True requires observation_path (the spectrum to fit against).")

    sections = []

    sections.append(f"[Global]\nxsec_path = {xsec_path}\ncia_path = {cia_path}")

    sections.append(_chemistry_section(chemistry_type, molecules, molecular_abundances, metallicity, co_ratio))

    sections.append(f"[Temperature]\nprofile_type = isothermal\nT = {planet_temp}")

    sections.append(
        "[Pressure]\nprofile_type = Simple\n"
        f"atm_min_pressure = {atm_min_pressure}\natm_max_pressure = {atm_max_pressure}\nnlayers = {nlayers}"
    )

    sections.append(f"[Planet]\nplanet_type = simple\nplanet_radius = {planet_radius}\nplanet_mass = {planet_mass}")

    sections.append(f"[Star]\nstar_type = blackbody\ntemperature = {star_temp}\nradius = {star_radius}")

    if model_type not in ("transmission", "emission"):
        raise ValueError(f"Unknown model_type: {model_type!r}. Must be 'transmission' or 'emission'.")
    sections.append(
        f"[Model]\nmodel_type = {model_type}\n\n"
        "    [[Absorption]]\n\n"
        "    [[CIA]]\n    cia_pairs = H2-H2,H2-He\n\n"
        "    [[Rayleigh]]"
    )

    if observation_path:
        sections.append(f"[Observation]\nobserved_spectrum = {observation_path}")

    if retrieval:
        if fit_params is None:
            fit_params = default_fit_params_for_chemistry(chemistry_type, molecules, molecular_abundances)
        if bounds is None:
            bounds = default_bounds_for_fit_params(fit_params, planet_radius=planet_radius, planet_temp=planet_temp)
        else:
            merged = default_bounds_for_fit_params(fit_params, planet_radius=planet_radius, planet_temp=planet_temp)
            merged.update(bounds)
            bounds = merged

        # Retrieval jobs written via this tool run on a cluster, which has far more compute
        # available than an in-session local retrieval - enforce a higher live-point floor.
        if num_live_points < MIN_CLUSTER_LIVE_POINTS:
            num_live_points = MIN_CLUSTER_LIVE_POINTS

        optimizer_lines = ["[Optimizer]", f"optimizer = {optimizer}", f"num_live_points = {num_live_points}"]
        if optimizer == "multinest":
            if not multinest_path:
                raise ValueError(
                    "optimizer='multinest' requires multinest_path (MultiNest's own working-file "
                    "directory ON THE CLUSTER) - either pass it explicitly or set CLUSTER_MULTINEST_PATH in .env."
                )
            optimizer_lines += [
                f"multi_nest_path = {multinest_path}",
                f"search_multi_modes = {multinest_search_multi_modes}",
                f"resume = {multinest_resume}",
                f"importance_sampling = {multinest_importance_sampling}",
            ]
        sections.append("\n".join(optimizer_lines))
        sections.append(_fitting_section(fit_params, bounds))

    parfile_text = "\n\n".join(sections) + "\n"

    parfile_dir = os.path.join(base_directory, 'parfiles')
    os.makedirs(parfile_dir, exist_ok=True)
    parfile_path = os.path.join(parfile_dir, f'{filename}.par')
    with open(parfile_path, 'w') as f:
        f.write(parfile_text)

    rel_path = os.path.relpath(parfile_path, base_directory)
    job_kind = "retrieval" if retrieval else "forward model"
    return f"TauREx {job_kind} parameter file written to {rel_path}:\n\n{parfile_text}"


class WriteTaurexParameterFile(BaseTool):
    """
    Generate a TauREx CLI parameter (.par) file for a forward model or retrieval job.

    Use this instead of the Python-based TauREx tools when the user wants to launch the job
    on a cluster rather than run it locally in this session. The real CLI invocation (verified
    against the installed taurex package's argparse) is:

        taurex -i model.par -o output.h5 --retrieval

    (`-i`/`--input`, `-o`/`--output_file`, `-R`/`--retrieval` - omit `--retrieval` for a forward
    model run instead of a fit). Read skills/cluster_setup.md for the full recipe chaining this
    with WriteSlurmScript/SubmitSlurmJob, including the flat-remote-directory gotcha for
    `observation_path`.
    """

    xsec_path: str = RuntimeField(
        default="",
        description="Absolute path to opacity cross-section files ON THE MACHINE THAT WILL RUN THIS FILE (e.g. the cluster). Leave this as an empty string (do NOT guess or reuse this session's local SetTaurexPaths value) - it defaults to CLUSTER_XSEC_PATH from the environment, which is always the correct cluster-side path. Only pass this explicitly if the user gives you a different path themselves."
    )
    cia_path: str = RuntimeField(
        default="",
        description="Absolute path to CIA files ON THE MACHINE THAT WILL RUN THIS FILE (e.g. the cluster). Leave this as an empty string (do NOT guess or reuse this session's local SetTaurexPaths value) - it defaults to CLUSTER_CIA_PATH from the environment, which is always the correct cluster-side path. Only pass this explicitly if the user gives you a different path themselves."
    )
    model_type: str = RuntimeField(default="transmission", description="'transmission' or 'emission'.")
    chemistry_type: str = RuntimeField(
        default="basic",
        description="'basic' (fixed default 5-molecule set), 'free' (custom molecules/molecular_abundances), or 'equilibrium' (ACE thermochemical equilibrium chemistry via metallicity/co_ratio - built into taurex/acepython, no extra plugin needed)."
    )
    molecules: list[str] | str = RuntimeField(
        default="",
        description="List of molecule names, e.g. ['H2O', 'CH4']. Each gets a seed abundance from a fixed lookup table. Only used when chemistry_type='free'. Ignored if molecular_abundances is given. Leave as an empty string if not specifying molecules."
    )
    molecular_abundances: dict | str = RuntimeField(
        default="",
        description="Dict of molecule name to exact mixing ratio, e.g. {'H2O': 0.02, 'CH4': 0.001}. Only used when chemistry_type='free'. Takes precedence over 'molecules'. Leave as an empty string if not specifying exact abundances."
    )
    metallicity: float = RuntimeField(default=1.0, description="Metallicity relative to solar. Only used when chemistry_type='equilibrium'.")
    co_ratio: float = RuntimeField(default=0.54, description="Carbon-to-oxygen ratio. Only used when chemistry_type='equilibrium'. Solar value is ~0.54.")
    star_radius: float = RuntimeField(default=1.0, description="Radius of the star in solar radii.")
    star_temp: float = RuntimeField(default=5500.0, description="Temperature of the star in Kelvin.")
    planet_radius: float = RuntimeField(default=1.0, description="Radius of the planet in Jupiter radii.")
    planet_mass: float = RuntimeField(default=1.0, description="Mass of the planet in Jupiter masses.")
    planet_temp: float = RuntimeField(default=1500.0, description="Temperature of the planet in Kelvin.")
    atm_min_pressure: float = RuntimeField(default=1e-1, description="Minimum atmospheric pressure in Pascal. Standard value is 1e-1 Pa.")
    atm_max_pressure: float = RuntimeField(default=1e6, description="Maximum atmospheric pressure in Pascal. Standard value is 1e6 Pa.")
    nlayers: int = RuntimeField(default=100, description="Number of atmospheric layers.")
    observation_path: str = RuntimeField(
        default="",
        description="Path to the observed spectrum file, written into [Observation] as observed_spectrum. Required if retrieval=True."
    )
    retrieval: bool = RuntimeField(
        default=False,
        description="If True, adds [Optimizer]/[Fitting] sections to turn this into a retrieval job (requires observation_path). If False, this is a one-off forward model job."
    )
    fit_params: list | str = RuntimeField(
        default="",
        description="List of parameters to fit. Only used if retrieval=True. Optional - if left as an empty string, auto-generated from the chemistry configuration."
    )
    bounds: dict | str = RuntimeField(
        default="",
        description="Dict of {param: [low, high]} bounds. Only used if retrieval=True. Optional - if left as an empty string, auto-generated."
    )
    optimizer: str = RuntimeField(default="nestle", description="Optimizer for retrieval jobs: 'nestle', 'multinest', or 'polychord'.")
    num_live_points: int = RuntimeField(
        default=RECOMMENDED_CLUSTER_LIVE_POINTS,
        description=(
            "Number of live points for nested sampling (retrieval jobs only). Since jobs written "
            f"via this tool run on a cluster (not locally), the minimum enforced here is "
            f"{MIN_CLUSTER_LIVE_POINTS} - {RECOMMENDED_CLUSTER_LIVE_POINTS} is recommended for "
            "production-quality results. Values below the minimum are silently raised to it."
        ),
    )
    multinest_path: str = RuntimeField(
        default="",
        description="Absolute path ON THE CLUSTER for MultiNest's own working files (the 'multi_nest_path' config key) - REQUIRED when optimizer='multinest', ignored otherwise. Leave as an empty string to default to CLUSTER_MULTINEST_PATH from the environment; do not guess a path."
    )
    multinest_search_multi_modes: bool = RuntimeField(default=True, description="MultiNest's search_multi_modes option. Only used when optimizer='multinest'.")
    multinest_resume: bool = RuntimeField(default=False, description="MultiNest's resume option (resume from a previous incomplete run's checkpoint at multinest_path). Only used when optimizer='multinest'.")
    multinest_importance_sampling: bool = RuntimeField(default=False, description="MultiNest's importance_sampling option. Only used when optimizer='multinest'.")
    filename: str = RuntimeField(default="taurex_model", description="Output file will be saved as 'parfiles/{filename}.par'.")
    base_directory: str = StateField()

    def _run(self):
        def _parse(value):
            # "" is this tool's not-given sentinel (RuntimeField can't reliably default to None -
            # see other ASTER cluster tools for the same convention), so treat it as None here.
            if value == "":
                return None
            if isinstance(value, str):
                try:
                    return ast.literal_eval(value)
                except (ValueError, SyntaxError) as e:
                    raise ValueError(f"Failed to parse string value: {value}. Error: {e}")
            return value

        xsec_path = self.xsec_path or os.environ.get('CLUSTER_XSEC_PATH')
        cia_path = self.cia_path or os.environ.get('CLUSTER_CIA_PATH')
        if not xsec_path or not cia_path:
            raise ValueError(
                "xsec_path/cia_path not given and CLUSTER_XSEC_PATH/CLUSTER_CIA_PATH are not "
                "both set in .env. Ask the user for the absolute opacity/CIA paths on the "
                "machine that will run this .par file."
            )
        multinest_path = self.multinest_path or os.environ.get('CLUSTER_MULTINEST_PATH')

        return generate_taurex_parfile(
            xsec_path=xsec_path,
            cia_path=cia_path,
            model_type=self.model_type,
            chemistry_type=self.chemistry_type,
            molecules=_parse(self.molecules),
            molecular_abundances=_parse(self.molecular_abundances),
            metallicity=self.metallicity,
            co_ratio=self.co_ratio,
            star_radius=self.star_radius,
            star_temp=self.star_temp,
            planet_radius=self.planet_radius,
            planet_mass=self.planet_mass,
            planet_temp=self.planet_temp,
            atm_min_pressure=self.atm_min_pressure,
            atm_max_pressure=self.atm_max_pressure,
            nlayers=self.nlayers,
            observation_path=self.observation_path,
            retrieval=self.retrieval,
            fit_params=_parse(self.fit_params),
            bounds=_parse(self.bounds),
            optimizer=self.optimizer,
            num_live_points=self.num_live_points,
            multinest_path=multinest_path,
            multinest_search_multi_modes=self.multinest_search_multi_modes,
            multinest_resume=self.multinest_resume,
            multinest_importance_sampling=self.multinest_importance_sampling,
            filename=self.filename,
            base_directory=self.base_directory,
        )
