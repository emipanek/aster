import os

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField


# Matches the output filenames written by RunTaurexTransmissionModelTool/RunTaurexEmissionModelTool in aster_toolkit/taurex/forward_model.py.
TRANSMISSION_SUFFIXES = ['_fm_wavelength.npy', '_fm_spectrum.npy', '_spectrum.png']
EMISSION_SUFFIXES = ['_em_wavelength.npy', '_em_spectrum.npy', '_emission_spectrum.png']


class CheckForwardModelRanTool(BaseTool):
    """Check whether a TauREx forward model (transmission and/or emission) has already been run for a given filename, by looking for its expected output files in the workspace."""

    filename: str = RuntimeField(
        default="",
        description="REQUIRED. The filename used when the forward model was run, e.g. 'wasp39b/forward_model_default_molecules'."
    )
    base_directory: str = StateField()

    def _run(self):
        if not self.filename:
            raise ValueError("filename is required.")

        def existing(suffixes):
            return [s for s in suffixes if os.path.isfile(os.path.join(self.base_directory, f"{self.filename}{s}"))]

        transmission_found = existing(TRANSMISSION_SUFFIXES)
        emission_found = existing(EMISSION_SUFFIXES)

        lines = [f"Forward model check for filename='{self.filename}':"]
        lines.append(f"  Transmission: {'RUN' if transmission_found else 'not run'}"
                     f"{f' (found: {transmission_found})' if transmission_found else ''}")
        lines.append(f"  Emission: {'RUN' if emission_found else 'not run'}"
                     f"{f' (found: {emission_found})' if emission_found else ''}")

        return "\n".join(lines)
