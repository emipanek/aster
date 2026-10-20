import numpy as np

from taurex.chemistry import AutoChemistry
from taurex.core import fitparam


class FastChemChemistry(AutoChemistry):
    """
    TauREx chemistry model that computes layer-by-layer gas-phase equilibrium abundances via FastChem (`pyfastchem`), as an alternative to ACEChemistry ('equilibrium' chemistry_type).

    Exposes 'metallicity' and 'C_O_ratio' as fittable parameters exactly like ACEChemistry, so it works as a retrieval chemistry too: TauREx calls initialize_chemistry() fresh on every model evaluation (see taurex.model.simplemodel.SimpleModel.initialize_profiles(), called from model()), so each nested-sampling sample gets its own FastChem run at the sampled metallicity/C_O_ratio.

    Reuses the same FastChem setup logic (element/logK data files, metallicity/C-O scaling) as aster_toolkit.chemistry.fastchem_tools.RunFastChemEquilibriumTool, so results from the two tools are directly comparable.
    """

    def __init__(self, molecules, metallicity=1.0, co_ratio=0.54, element_abundance_source="asplund_2021"):
        """
        Parameters
        molecules : list of str
            Molecule names to solve for and include in the opacity model. H2/He are always included automatically as the dominant background gases.
        metallicity, co_ratio : float
            Initial/seed values; both are re-applied (and, for retrieval, overwritten by the optimizer) on every initialize_chemistry() call.
        element_abundance_source : str
            Which solar composition reference FastChem starts from, see aster_toolkit.chemistry.fastchem_tools.VALID_ELEMENT_ABUNDANCE_SOURCES.
        """
        super().__init__(self.__class__.__name__)

        import pyfastchem
        from aster_toolkit.chemistry.fastchem_tools import _fastchem_input_dir, VALID_ELEMENT_ABUNDANCE_SOURCES

        if element_abundance_source not in VALID_ELEMENT_ABUNDANCE_SOURCES:
            raise ValueError(
                f"Unknown element_abundance_source: {element_abundance_source!r}. "
                f"Must be one of {VALID_ELEMENT_ABUNDANCE_SOURCES}."
            )

        self._metallicity = metallicity
        self._co_ratio = co_ratio
        self.element_abundance_source = element_abundance_source

        requested = [m for m in molecules if m not in ('H2', 'He')]
        self._gases = ['H2', 'He'] + requested
        self._mix_profile = None

        # Build the FastChem instance once (reads element/logK data files from disk) only element abundances (metallicity/C-O) are re-applied per initialize_chemistry() call, so retrieval doesn't re-read these files on every nested-sampling iteration.
        input_dir = _fastchem_input_dir()
        element_file = input_dir / 'element_abundances' / f'{element_abundance_source}_extended.dat'
        logk_file = input_dir / 'logK' / 'logK_extended.dat'
        self._fastchem = pyfastchem.FastChem(str(element_file), str(logk_file), 0)

        # Resolve+cache each requested gas's FastChem species index once, so an unknown species name (typo, or not in FastChem's network) fails immediately rather than partway through a multi-hour retrieval.
        self._gas_indices = []
        for name in self._gases:
            hill_name = self._fastchem.convertToHillNotation(name)
            idx = self._fastchem.getGasSpeciesIndex(hill_name)
            if idx == pyfastchem.FASTCHEM_UNKNOWN_SPECIES:
                raise ValueError(f"FastChem does not have species {name!r} in its gas-phase network.")
            self._gas_indices.append(idx)

        self.determine_active_inactive()

        def _get_co_ratio(self):
            return self._co_ratio

        def _set_co_ratio(self, value):
            self._co_ratio = value

        self.add_fittable_param(
            "C_O_ratio", "C/O", _get_co_ratio, _set_co_ratio, "linear", False, [0.1, 2.0]
        )

    @property
    def gases(self):
        """All gases solved for (H2/He fill gases + requested molecules), in mixProfile row order."""
        return self._gases

    @property
    def mixProfile(self):
        """Mixing profile (VMR), shape (len(gases), nlayers)."""
        return self._mix_profile

    def initialize_chemistry(self, nlayers=100, temperature_profile=None, pressure_profile=None, altitude_profile=None):
        """
        Recompute equilibrium abundances for the current temperature/pressure profile and the current metallicity/C_O_ratio. Called fresh by TauREx on every model evaluation, so retrieval sampling of metallicity/C_O_ratio works as expected.
        """
        import pyfastchem
        from aster_toolkit.chemistry.fastchem_tools import _apply_element_abundances

        _apply_element_abundances(self._fastchem, self._metallicity, self._co_ratio, None)

        input_data = pyfastchem.FastChemInput()
        output_data = pyfastchem.FastChemOutput()
        input_data.temperature = list(np.atleast_1d(temperature_profile))
        input_data.pressure = list(np.atleast_1d(pressure_profile) / 1e5)  # Pa -> bar
        self._fastchem.calcDensities(input_data, output_data)

        number_densities = np.atleast_2d(np.array(output_data.number_densities))
        total_density = number_densities.sum(axis=1)
        mixing_ratios = number_densities / total_density[:, None]  # (nlayers, n_fastchem_species)

        self._mix_profile = mixing_ratios[:, self._gas_indices].T  # (n_gases, nlayers)

    @fitparam(param_name="metallicity", param_latex="Z", default_bounds=[0.1, 10.0], default_fit=False)
    def metallicity(self):
        """Metallicity relative to solar."""
        return self._metallicity

    @metallicity.setter
    def metallicity(self, value):
        self._metallicity = value

    @classmethod
    def input_keywords(cls):
        """Input keywords for this class (TauREx .par-file factory lookup)."""
        return ("fastchem",)
