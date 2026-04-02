<img width="434" height="363" alt="logo_no_background (1)" src="https://github.com/user-attachments/assets/0e6f11dd-0f08-4200-a8de-5823a97c7fdd" />

# ASTER - **Agentic Science Toolkit for Exoplanet Research**

This is the refactored version of ASTER using the `orchestral-ai` package from PyPI.

## Key Changes

1. **Uses orchestral-ai package** - Instead of local orchestral/ directory
2. **CamelCase tool names** - All tools follow Python class naming conventions
3. **Improved base_directory handling** - Using StateField pattern
4. **Lazy imports** - Faster startup time
5. **Cleaner architecture** - Separated concerns and better organization

## Installation

```bash
git clone https://github.com/emipanek/aster.git
cd ./aster
pip install -r requirements.txt
```
You also need to configure a .env txt file with your API keys.

## Structure

```
new_aster/
├── tools/              # All ASTER-specific tools
│   ├── __init__.py
│   ├── taurex_tools.py       # Taurex simulation tools
│   ├── exoplanet_tools.py    # NASA Archive tools
│   └── data_tools.py         # Data download and processing
├── run_app.py          # Main application entry point
└── requirements.txt    # Dependencies
```

## Usage

```bash
python run_aster.py
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

