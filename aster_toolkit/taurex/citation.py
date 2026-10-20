"""Shared TauREx citation text, appended to the result of every agent-facing tool that actually produces a TauREx result (forward models, retrievals, cluster .par files) see skills/taurex_setup.md for why this is required and where it's enforced."""

TAUREX_CITATION = (
    "Al-Refaie, Changeat, Waldmann & Tinetti (2021), \"TauREx 3: A Fast, Dynamic, and Extendable Framework for Retrievals\", ApJ, 917, 37, doi:10.3847/1538-4357/ac0252"
)

TAUREX_CITATION_NOTICE = (
    f"\n\nCitation required: this result was produced with TauREx. Any publication, report, or write-up using it MUST cite {TAUREX_CITATION}."
)

TAUREX_CITATION_BIBTEX = r"""@ARTICLE{2021ApJ...917...37A,
    author = {{Al-Refaie}, A.~F. and {Changeat}, Q. and {Waldmann}, I.~P. and {Tinetti}, G.},
        title = "{TauREx 3: A Fast, Dynamic, and Extendable Framework for Retrievals}",
    journal = {\apj},
    keywords = {Open source software, Astronomy software, Exoplanet atmospheres, Radiative transfer, Bayesian statistics, Planetary atmospheres, Planetary science, 1866, 1855, 487, 1335, 1900, 1244, 1255, Astrophysics - Instrumentation and Methods for Astrophysics, Astrophysics - Earth and Planetary Astrophysics},
        year = 2021,
        month = aug,
    volume = {917},
    number = {1},
        eid = {37},
        pages = {37},
        doi = {10.3847/1538-4357/ac0252},
archivePrefix = {arXiv},
    eprint = {1912.07759},
primaryClass = {astro-ph.IM},
    adsurl = {https://ui.adsabs.harvard.edu/abs/2021ApJ...917...37A},
    adsnote = {Provided by the SAO/NASA Astrophysics Data System}
}
"""
