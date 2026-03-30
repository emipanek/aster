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
python run_app.py
```
