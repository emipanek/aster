# ASTER - Refactored Version

**Agentic Science Toolkit for Exoplanet Research**

This is the refactored version of ASTER using the `orchestral-ai` package from PyPI.

## Key Changes

1. **Uses orchestral-ai package** - Instead of local orchestral/ directory
2. **CamelCase tool names** - All tools follow Python class naming conventions
3. **Improved base_directory handling** - Using StateField pattern
4. **Lazy imports** - Faster startup time
5. **Cleaner architecture** - Separated concerns and better organization

## Installation

```bash
pip install orchestral-ai
```

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
python new_aster/run_app.py
```
