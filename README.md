<img width="434" height="363" alt="logo_no_background (1)" src="https://github.com/user-attachments/assets/0e6f11dd-0f08-4200-a8de-5823a97c7fdd" />

# ASTER 

## Overview

ASTER (Agnetic Science Toolkit for Exoplanet Research) is an agentic toolkit for exoplanet research, built-on the orchestral AI framework but also accessible via other LLM-agent structure providers. 

## Installation

```bash
git clone https://github.com/emipanek/aster.git
cd ./aster
pip install -r requirements.txt
```
You also need to configure a .env txt file with your API keys.

ASTER can also be downloaded via toolbase, a package manager for AI agent tools ([see the toolbase Github for more informations](https://github.com/alexr314/toolbase)). 

```bash
pip install toolbase        # provides the `tb` command
git clone https://github.com/emipanek/aster.git
cd ./aster
tb install .
```

## Usage

### From ASTER github, within the Orchestral framework

```bash
python run_aster.py
```

### From toolbase

The core toolbase workflow is **install → activate → connect**.

```bash
tb activate aster
tb connect claude-code      # writes this project's .mcp.json (-g for user-level)
claude                      # or codex / opencode
```


## Tools

| Area | Tools |
|---|---|
| TauREx modelling | `SetTaurexPaths`, `RunTaurexTransmissionModelTool`, `RunTaurexEmissionModelTool`, `SimulateTaurexRetrieval`, `WriteTaurexParameterFile`, `PlotCornerPosteriors` |
| Data acquisition | `GetExoplanetParameters`, `FindExoplanetsByCondition`, `DownloadDataset` |
| Published measurements (`aster_toolkit/measurements/`) | `ResolvePlanetNameTool`, `PublishedMeasurementsTool`, `MeasurementDisagreementTool`, `ExplainDisagreementTool`, `TransmissionSpectrumTool`, `AtmosphericSpectraTool`, `ExoplanetArchiveQueryTool` |
| Chemistry | `RunFastChemEquilibriumTool` |
| Binning | `BinSpectrum` |

The published-measurements tools read the NASA Exoplanet Archive over its keyless TAP service and compare what different papers report for one planet: every published parameter set (`ps` table, one row per reference), the worst disagreement in sigma, why the two papers differ (from their abstracts), and published spectra. `AtmosphericSpectraTool` lists a planet's spectra in the archive's Atmospheric Spectroscopy table and returns the same wget script the archive website generates, so `DownloadDataset(wget_text=...)` can fetch JWST and other spectra without anyone clicking through the website.

Offline checks for that bundle (stubbed archive, no network):

```bash
python aster_toolkit/measurements/test_measurements.py
```

## Citations

If you use ASTER in your research, please cite:

- [ASTER: Agentic Science Toolkit for Exoplanet Research (Panek et al., 2026)](https://arxiv.org/abs/2603.26953)

```bibtex
@misc{panek2026asteragenticscience,
      title={ASTER -- Agentic Science Toolkit for Exoplanet Research}, 
      author={Emilie Panek and Alexander Roman and Gaurav Shukla and Leonardo Pagliaro and Katia Matcheva and Konstantin Matchev},
      year={2026},
      eprint={2603.26953},
      archivePrefix={arXiv},
      primaryClass={astro-ph.EP},
      url={https://arxiv.org/abs/2603.26953}, 
}
```
- [Orchestral AI: A Framework for Agent Orchestration (Roman & Roman, 2026)](https://arxiv.org/abs/2601.02577)

```bibtex
@misc{roman2026orchestralaiframeworkagent,
      title={Orchestral AI: A Framework for Agent Orchestration}, 
      author={Alexander Roman and Jacob Roman},
      year={2026},
      eprint={2601.02577},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2601.02577}, 
}
```

Other applications of Orchestral-AI to science are detailed here: 

- [HEPTAPOD: Orchestrating High Energy Physics Workflows Towards Autonomous Agency (Menzo et al., 2025)](https://arxiv.org/abs/2512.15867)
- [Agentic Diagrammatica: Towards Autonomous Symbolic Computation in High Energy Physics (Menzo et al., 2026)](https://arxiv.org/abs/2603.26990)
- [AI Agents for Variational Quantum Circuit Design (Knipfer et al., 2026)](https://arxiv.org/abs/2602.19387)

