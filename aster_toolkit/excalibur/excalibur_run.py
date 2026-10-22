"""
Runs Excalibur for the ASTER tools, inside the Excalibur Python environment (Python 3.12), not ASTER's.

The ASTER tools in excalibur_tools.py start this script as a separate process:
<excalibur_python> excalibur_run.py <command> <json-arguments> and read back the single line starting with RESULT_PREFIX. Everything else Excalibur prints is ignored. This file must not import orchestral or aster_toolkit.

Commands: find_target, cross_sections, equilibrium_model, free_model, ariel_simulation, lethe_light_curve.

Every command takes 'data_dir' (Excalibur DATA_BASEDIR) and optionally 'esp_dir', an esp checkout put first on sys.path so it is used instead of the installed Excalibur.
"""
import csv
import datetime
import difflib
import json
import os
import pickle
import re
import sys
import traceback

RESULT_PREFIX = "ASTER_EXCALIBUR_RESULT:"

ARIELRAD_VERSION = "29oct2024"
RJUP_M = 7.1492e7                           # IAU nominal equatorial radii
RSUN_M = 6.957e8
REARTH_OVER_RJUP = 6.3781e6 / RJUP_M
SOLAR_CTO = 0.54951                         # Excalibur's solar C/O reference

# Cerberus line lists (runtime defaults, see excalibur/runtime/example.xml)
DEFAULT_HITEMP = ['NO', 'OH', 'C2H2', 'N2', 'N2O', 'O3', 'O2']
DEFAULT_CIA = ['H2-H', 'H2-H2', 'H2-He', 'He-H']
DEFAULT_EXOMOL = ['TIO', 'H2O', 'H2CO', 'HCN', 'CO', 'CO2', 'NH3', 'CH4', 'H2S', 'SO2', 'PH3']
ATOMS = ['Na', 'K', 'Ca']                   # CERBERUS/ATOM_XSEC grids
NLEVELS, HSMAX, SOLRAD = 100, 20.0, 10.0
XSEC_FORMAT = 2                             # cross-section files made for the new crbmodel (hitemp/exomol/cia lists)

PICKLE_SUFFIX = '.9999.system.Finalize.parameters.pickle'


# helpers

def _setup(args):
    """Set DATA_BASEDIR (read by Excalibur at import time) and put esp_dir first on sys.path."""
    data_dir = os.path.expanduser(args.get('data_dir') or '')
    if not data_dir or not os.path.isdir(data_dir):
        raise RuntimeError(
            f"Excalibur data directory not found: '{data_dir}'. It must contain CERBERUS/, ariel/ and pickles/"
        )
    os.environ['DATA_BASEDIR'] = data_dir
    os.environ.setdefault('LDTK_ROOT', os.path.join(data_dir, 'ldtk'))
    if args.get('esp_dir'):
        esp_dir = os.path.expanduser(args['esp_dir'])
        if not os.path.isdir(os.path.join(esp_dir, 'excalibur')):
            raise RuntimeError(f"esp directory '{esp_dir}' has no excalibur/ folder.")
        sys.path.insert(0, esp_dir)
    return data_dir


class _Molecules:
    """Stand-in for a runtime MoleculeValue."""
    def __init__(self, molecules):
        self.molecules = list(molecules)


class _Value:
    """Stand-in for a runtime scalar/bool value."""
    def __init__(self, v):
        self.v = v

    def value(self):
        return self.v

    def __bool__(self):
        return bool(self.v)


def _runtime(hitemp=None, cia=None, exomol=None, atoms=None, tier=2):
    """The runtime entries myxsecs and the Ariel instrument model read."""
    return {
        'cerberus_crbmodel_HITEMPmolecules': _Molecules(DEFAULT_HITEMP if hitemp is None else hitemp),
        'cerberus_crbmodel_HITRANmolecules': _Molecules(DEFAULT_CIA if cia is None else cia),
        'cerberus_crbmodel_EXOMOLmolecules': _Molecules(DEFAULT_EXOMOL if exomol is None else exomol),
        'cerberus_crbmodel_atoms': _Molecules(atoms or []),
        'cerberus_crbmodel_nlevels': _Value(NLEVELS),
        'cerberus_crbmodel_Hsmax': _Value(HSMAX),
        'cerberus_crbmodel_solrad': _Value(SOLRAD),
        'ariel_simspectrum_tier': _Value(tier),
        'ariel_simspectrum_arielRad': _Value(ARIELRAD_VERSION),
        'ariel_simspectrum_thorngrenMassMetals': _Value(True),
        'ariel_simspectrum_chachanMassMetals': _Value(False),
    }


def _arielrad_files(data_dir, mmw_model="Thorngren"):
    files = [os.path.join(data_dir, 'ariel', f'arielRad_{ARIELRAD_VERSION}_mmw{mmw_model}-part{i}.h5') for i in (1, 2)]
    missing = [f for f in files if not os.path.isfile(f)]
    if missing:
        raise RuntimeError(f"ArielRad file(s) missing: {missing}")
    return files


def _normalise(name):
    return re.sub(r'[^a-z0-9]', '', name.lower())


def _uniform(spec, what):
    m = re.fullmatch(r'\s*([0-9.eE+-]+)\s*,\s*([0-9.eE+-]+)\s*,\s*(\d+)\s*', spec or '')
    if not m:
        raise RuntimeError(f"{what} must be 'min,max,n', got '{spec}'.")
    return float(m.group(1)), float(m.group(2)), int(m.group(3))


def _float(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN -> None


# target database (system pickles)

class _Dict(dict):
    def __setstate__(self, state):
        self.__dict__.update(state if isinstance(state, dict) else {})


class _List(list):
    def __setstate__(self, state):
        self.__dict__.update(state if isinstance(state, dict) else {})


class _Version:
    def __init__(self, *args, **kwargs):
        self.args = args

    def __setstate__(self, state):
        pass


class _PriorsUnpickler(pickle.Unpickler):
    """The system pickles hold dawgie state vectors; read them as plain dicts/lists without dawgie."""
    def find_class(self, module, name):
        if module.split('.')[0] in ('excalibur', 'dawgie'):
            if name == 'VERSION':
                return _Version
            return _List if 'List' in name else _Dict
        return super().find_class(module, name)


def _pickle_dir(data_dir):
    folder = os.path.join(data_dir, 'pickles')
    if not os.path.isdir(folder):
        raise RuntimeError(f"Target pickles not found: {folder} (expected <star>{PICKLE_SUFFIX} files).")
    return folder


def _stars(data_dir):
    return sorted(f[:-len(PICKLE_SUFFIX)] for f in os.listdir(_pickle_dir(data_dir)) if f.endswith(PICKLE_SUFFIX))


def _priors(data_dir, star):
    with open(os.path.join(_pickle_dir(data_dir), star + PICKLE_SUFFIX), 'rb') as f:
        return _PriorsUnpickler(f).load()['priors']


def _arielrad_info(data_dir, planet):
    """Whether ArielRad (the Ariel noise model) has this planet, and its Ariel tiers."""
    import h5py
    try:
        files = _arielrad_files(data_dir)
    except RuntimeError:
        return {'noise_model_available': False, 'ariel_tiers': []}
    for fn in files:
        with h5py.File(fn, 'r') as f:
            if planet in f['errorbars_evaluated']:
                tiers = [t for t in ('tier1', 'tier2', 'tier3') if planet in f['ariel_tiers'][t]]
                return {'noise_model_available': True, 'ariel_tiers': tiers}
    return {'noise_model_available': False, 'ariel_tiers': []}


def _target_record(data_dir, star, letter):
    pr = _priors(data_dir, star)
    pl = pr[letter]

    def entry(group, key):
        return {'value': _float(group.get(key)) if key not in ('spTyp',) else group.get(key),
                'units': group.get(key + '_units'), 'ref': group.get(key + '_ref')}

    star_keys = {'R*': 'radius_rsun', 'M*': 'mass_msun', 'T*': 'teff_K', 'LOGG*': 'logg_cgs', 'FEH*': 'feh_dex',
                 'L*': 'luminosity_lsun', 'RHO*': 'density_cgs', 'AGE*': 'age_gyr', 'dist': 'distance_pc',
                 'Kmag': 'K_mag', 'spTyp': 'spectral_type'}
    planet_keys = {'rp': 'radius_rjup', 'mass': 'mass_mjup', 'teq': 'teq_K', 'sma': 'semi_major_axis_au',
                   'period': 'period_days', 't0': 't0_bjd', 'inc': 'inclination_deg', 'ecc': 'eccentricity',
                   'omega': 'omega_deg', 'impact': 'impact_parameter', 'logg': 'logg_cgs',
                   'trandepth': 'transit_depth_percent', 'trandur': 'transit_duration_hours'}
    star_rec = {new: entry(pr, old)['value'] for old, new in star_keys.items()}
    planet_rec = {new: entry(pl, old)['value'] for old, new in planet_keys.items()}
    refs = {'star': {new: {'units': pr.get(old + '_units'), 'ref': pr.get(old + '_ref')} for old, new in star_keys.items()},
            'planet': {new: {'units': pl.get(old + '_units'), 'ref': pl.get(old + '_ref')} for old, new in planet_keys.items()}}
    if planet_rec['radius_rjup'] is not None:
        planet_rec['radius_rearth'] = planet_rec['radius_rjup'] / REARTH_OVER_RJUP
        if star_rec['radius_rsun']:
            planet_rec['rp_over_rstar'] = planet_rec['radius_rjup'] * RJUP_M / (star_rec['radius_rsun'] * RSUN_M)
    name = f'{star} {letter}'
    return {'name': name, 'star': star_rec, 'planet': planet_rec, 'units_and_references': refs,
            'other_planets_in_system': [p for p in pr['planets'] if p != letter],
            'ariel': _arielrad_info(data_dir, name)}


def find_target(args):
    data_dir = _setup(args)
    query = args['name'].strip()
    stars = _stars(data_dir)
    by_norm = {_normalise(s): s for s in stars}
    nq = _normalise(query)
    database = f'Excalibur system pickles ({len(stars)} systems)'

    # "<star><letter>", ignoring case/spaces/hyphens ("k2-12b" == "K2-12 b")
    if nq[:-1] in by_norm and nq[-1:].isalpha():
        star, letter = by_norm[nq[:-1]], nq[-1]
        planets = list(_priors(data_dir, star)['planets'])
        if letter in planets:
            return {'found': True, 'query': query, 'database': database, 'target': _target_record(data_dir, star, letter)}
        return {'found': False, 'query': query, 'database': database, 'planets_of_this_star': [f'{star} {p}' for p in planets], 'close_matches': []}
    # A star name: list its planets
    if nq in by_norm:
        star = by_norm[nq]
        return {'found': False, 'query': query, 'database': database, 'planets_of_this_star': [f'{star} {p}' for p in _priors(data_dir, star)['planets']],'close_matches': []}
    host = query.rsplit(' ', 1)[0] if re.search(r'\s[a-z]$', query) else query
    close = difflib.get_close_matches(host, stars, n=8, cutoff=0.6)
    return {'found': False, 'query': query, 'database': database, 'planets_of_this_star': [],'close_matches_star_names': close}


def _target_or_fail(data_dir, name):
    found = find_target({'data_dir': data_dir, 'name': name})
    if not found['found']:
        suggestions = found.get('planets_of_this_star') or found.get('close_matches_star_names') or []
        raise RuntimeError(f"Target '{name}' not in the Excalibur target list. Suggestions: {suggestions}")
    return found['target']


# cross_sections

def _wavelength_grid(spec, data_dir):
    import numpy as np
    import h5py
    spec = (spec or 'ariel').strip()
    if spec.lower() == 'ariel':
        fn = _arielrad_files(data_dir)[0]
        with h5py.File(fn, 'r') as f:
            first = next(iter(f['errorbars_evaluated'].keys()))
            wl = f['errorbars_evaluated'][first]['table_to_group']['Wavelength']['value'][()]
        return np.asarray(wl, dtype=float), 'ArielRad instrument channels (103)'
    m = re.fullmatch(r'\s*([0-9.eE+-]+)\s*,\s*([0-9.eE+-]+)\s*,\s*(\d+)\s*', spec)
    if m:
        lo, hi, n = float(m.group(1)), float(m.group(2)), int(m.group(3))
        return np.linspace(lo, hi, n), f'uniform {lo}-{hi} um, {n} points'
    path = os.path.expanduser(spec)
    if not os.path.isfile(path):
        raise RuntimeError(f"wavelength_grid '{spec}' is not 'ariel', 'min,max,n', or an existing file.")
    data = np.load(path) if path.endswith('.npy') else np.loadtxt(path)
    data = np.atleast_1d(data)
    wl = data[:, 0] if data.ndim == 2 else data
    return np.asarray(wl, dtype=float), f'from file {path} (first column)'


def cross_sections(args):
    import time
    import numpy as np
    data_dir = _setup(args)
    out_path = args['out_path']
    if os.path.exists(out_path) and not args.get('overwrite'):
        with open(out_path + '.json') as f:
            meta = json.load(f)
        if meta.get('format') == XSEC_FORMAT:
            return {'reused': True, 'cross_section_file': out_path, 'metadata': meta}

    wl, wl_desc = _wavelength_grid(args.get('wavelength_grid'), data_dir)
    if wl.size < 2 or np.any(np.diff(wl) <= 0):
        raise RuntimeError("The wavelength grid must have at least 2 strictly increasing values (in microns).")

    exomol = args.get('molecules') or DEFAULT_EXOMOL
    hitemp = list(DEFAULT_HITEMP)
    notes = []
    # Cerberus adds the O3 UV Hartley band from this file whenever O3 is used
    if not os.path.isfile(os.path.join(data_dir, 'CERBERUS', 'SUPPLEMENT', 'O3_VIS_UV.txt')):
        hitemp.remove('O3')
        notes.append('O3 left out: CERBERUS/SUPPLEMENT/O3_VIS_UV.txt is missing from the data folder.')
    # Atoms (Na, K, Ca) come from the CERBERUS/ATOM_XSEC grids at model time, not from this library:gettau skips any atom that is already in XSECS.
    from excalibur.cerberus.core import myxsecs
    rt = _runtime(hitemp=hitemp, exomol=exomol, atoms=[])
    xslib = {'data': {}, 'STATUS': [False]}
    start = time.time()
    myxsecs({'data': {'b': {'WB': wl}}}, rt, xslib)
    elapsed = time.time() - start
    if 'b' not in xslib['data'] or 'XSECS' not in xslib['data']['b']:
        raise RuntimeError("Excalibur did not produce cross sections (check the CERBERUS/ line-list folders).")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    payload = {'XSECS': xslib['data']['b']['XSECS'], 'QTGRID': xslib['data']['b']['QTGRID'], 'wavelength_um': wl}
    with open(out_path, 'wb') as f:
        pickle.dump(payload, f)
    meta = {
        'format': XSEC_FORMAT,
        'created': datetime.datetime.now().isoformat(timespec='seconds'),
        'wavelength_grid': wl_desc,
        'n_wavelengths': int(wl.size),
        'wavelength_range_um': [float(wl.min()), float(wl.max())],
        'exomol': list(exomol),
        'hitemp': hitemp,
        'cia': DEFAULT_CIA,
        'atoms_at_model_time': ATOMS,
        'species': sorted(payload['XSECS'].keys()),
        'notes': notes,
        'compute_seconds': round(elapsed, 1),
        'file_size_mb': round(os.path.getsize(out_path) / 1e6, 1),
        'data_dir': data_dir,
    }
    with open(out_path + '.json', 'w') as f:
        json.dump(meta, f, indent=2)
    return {'reused': False, 'cross_section_file': out_path, 'metadata': meta}


# forward models

def _tea_grid(data_dir, cache_dir):
    """CERBERUS/INTERP_TEA interpolators. Building them takes ~1.5 min, so they are cached as a pickle, rebuilt when the INTERP_TEA folder changes."""
    from excalibur.cerberus.teagrid import get_TEA_grid
    tea_dir = os.path.join(data_dir, 'CERBERUS', 'INTERP_TEA')
    if not os.path.isdir(tea_dir):
        raise RuntimeError(f"TEA equilibrium grid not found: {tea_dir}")
    stamp = max(os.path.getmtime(os.path.join(tea_dir, f)) for f in os.listdir(tea_dir))
    cache = os.path.join(cache_dir, 'tea_grid.pkl') if cache_dir else None
    if cache and os.path.isfile(cache):
        with open(cache, 'rb') as f:
            cached = pickle.load(f)
        if cached.get('stamp') == stamp and cached.get('tea_dir') == tea_dir:
            return cached['grid'], True
    grid = get_TEA_grid()
    if cache:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache, 'wb') as f:
            pickle.dump({'stamp': stamp, 'tea_dir': tea_dir, 'grid': grid}, f)
    return grid, False


def _tea_bounds(data_dir):
    import numpy as np
    p = os.path.join(data_dir, 'CERBERUS', 'INTERP_TEA', 'grid_parameters')
    return {k: (float(np.min(a)), float(np.max(a))) for k, a in
            (('temperature', np.load(os.path.join(p, 'temperature.npy'))),
             ('XtoH', np.load(os.path.join(p, 'XtoH.npy'))),
             ('CtoO', np.load(os.path.join(p, 'CtoO.npy'))))}


def _load_cross_sections(path):
    if not os.path.isfile(path):
        raise RuntimeError(f"Cross-section file not found: {path}. Run CalculateExcaliburCrossSections first.")
    with open(path + '.json') as f:
        meta = json.load(f)
    if meta.get('format') != XSEC_FORMAT:
        raise RuntimeError(f"{path} was made for the old Cerberus version. Recompute it with CalculateExcaliburCrossSections(overwrite=True).")
    with open(path, 'rb') as f:
        xs = pickle.load(f)
    return xs, meta


def _planet_parameters(args, data_dir):
    """Database values for the target, overridden by anything given explicitly."""
    target_record = _target_or_fail(data_dir, args['target']) if args.get('target') else None
    p = target_record['planet'] if target_record else {}
    s = target_record['star'] if target_record else {}

    def pick(key, fallback):
        return args.get(key) if args.get(key) is not None else fallback

    params = {
        'Teq_K': pick('teq_K', p.get('teq_K')),
        'Rp_Rjup': pick('rp_rjup', p.get('radius_rjup')),
        'Mp_Mjup': pick('mp_mjup', p.get('mass_mjup')),
        'Rstar_Rsun': pick('rstar_rsun', s.get('radius_rsun')),
        'Tstar_K': pick('tstar_K', s.get('teff_K')),
        'sma_AU': pick('sma_au', p.get('semi_major_axis_au')),
    }
    missing = [k for k, v in params.items() if v is None]
    if missing:
        raise RuntimeError(f"Missing planet/star parameters {missing}: give a database target or set them explicitly.")
    return {k: float(v) for k, v in params.items()}, target_record


def _run_cerberus(args, chemistry_mode):
    import numpy as np
    data_dir = _setup(args)
    import excalibur
    xs_path = args['cross_section_file']
    xs, xs_meta = _load_cross_sections(xs_path)
    wl = np.asarray(xs['wavelength_um'])
    params, target_record = _planet_parameters(args, data_dir)

    import excalibur.system.core as syscore
    from excalibur.cerberus.core import hazelib
    from excalibur.cerberus.forward_model import crbFM
    from excalibur.util.logg import calculate_logg
    ssc_mks = syscore.ssconstants(mks=True)
    sscmks = syscore.ssconstants(cgs=True)
    logg = float(calculate_logg(params['Mp_Mjup'], params['Rp_Rjup'], sscmks, units='Jupiter'))
    params['logg_cgs'] = logg

    clouds = {
        'CTP': float(args.get('cloud_top_log10_bar', 3.0)),
        'HScale': float(args.get('haze_log10_scale', -10.0)),
        'HLoc': float(args.get('haze_location', 0.0)),
        'HThick': float(args.get('haze_thickness', 0.0)),
    }

    atoms_used = []
    tea_data = atom_data = cheq = mixratio = None
    if chemistry_mode == 'equilibrium':
        metallicity = float(args.get('metallicity_dex', 0.0))
        c_to_o = float(args.get('c_to_o', SOLAR_CTO))
        if c_to_o <= 0:
            raise RuntimeError("c_to_o must be > 0 (linear C/O ratio, solar ~0.55).")
        model = (args.get('equilibrium_model') or 'TEA').upper()
        cheq = {'XtoH': metallicity, 'CtoO': float(np.log10(c_to_o / SOLAR_CTO)), 'NtoO': 0.0, 'StoO': 0.0}
        chem_label = model
        chemistry_record = {'mode': f'equilibrium ({model})', 'metallicity_dex': metallicity, 'c_to_o': c_to_o,
                            'cerberus_cheq': cheq}
        if model == 'TEA':
            bounds = _tea_bounds(data_dir)
            checks = [('temperature (K)', params['Teq_K'], bounds['temperature']),
                      ('metallicity_dex', metallicity, bounds['XtoH']),
                      ('log10(C/O / solar)', cheq['CtoO'], bounds['CtoO'])]
            outside = [f"{n}={v:.3g} outside [{lo:g}, {hi:g}]" for n, v, (lo, hi) in checks if not lo <= v <= hi]
            if outside:
                raise RuntimeError("Outside the TEA equilibrium grid: " + "; ".join(outside))
            tea_data, reused = _tea_grid(data_dir, args.get('cache_dir'))
            chemistry_record['tea_grid'] = {'source': 'CERBERUS/INTERP_TEA', 'from_cache': reused, 'bounds': bounds}
        elif model != 'TEC':
            raise RuntimeError("equilibrium_model must be 'TEA' (interpolated TEA grid) or 'TEC' (analytic, older).")
    else:
        vmr = args.get('log10_vmr') or {}
        if not vmr:
            raise RuntimeError("Free chemistry needs log10_vmr, e.g. {'H2O': -3, 'CH4': -5, 'Na': -6}.")
        # Cerberus expects log10(ppm): log10(VMR) + 6
        mixratio = {mol: float(v) + 6.0 for mol, v in vmr.items()}
        atoms_used = [m for m in mixratio if m in ATOMS]
        unknown = [m for m in mixratio if m not in xs['XSECS'] and m not in ATOMS]
        if unknown:
            raise RuntimeError(f"No cross sections for {unknown}. Available: {sorted(xs['XSECS'].keys()) + ATOMS}")
        if atoms_used:
            from excalibur.cerberus.atom_xsec import get_atom_xsec
            adir = os.path.join(data_dir, 'CERBERUS', 'ATOM_XSEC')
            t_atom, w_atom = np.load(os.path.join(adir, 'temp.npy')), np.load(os.path.join(adir, 'wgrid.npy'))
            if not t_atom.min() <= params['Teq_K'] <= t_atom.max():
                raise RuntimeError(f"Atom cross sections ({atoms_used}) cover {t_atom.min():g}-{t_atom.max():g} K, "
                                   f"not Teq={params['Teq_K']:g} K.")
            if wl.min() < w_atom.min() or wl.max() > w_atom.max():
                raise RuntimeError(f"Atom cross sections cover {w_atom.min():g}-{w_atom.max():g} um, the "
                                   f"wavelength grid is {wl.min():g}-{wl.max():g} um.")
            atom_data = get_atom_xsec(atoms_used)
        chem_label = 'TEA'  # ignored when mixratio is given (constant-with-pressure profiles)
        # (a copy: crbmodel turns the mixratio values into per-layer arrays in place)
        chemistry_record = {'mode': 'free', 'log10_vmr': vmr, 'cerberus_log10_ppm': dict(mixratio),
                            'atoms_from_ATOM_XSEC': atoms_used}

    orbp = {'T*': params['Tstar_K'], 'R*': params['Rstar_Rsun'], 'Rp': params['Rp_Rjup'], 'Mp': params['Mp_Mjup'],
            'logg': logg, 'sma': params['sma_AU'], 'Teq': params['Teq_K'], **clouds, 'b': {'logg': logg}}
    hz = {'PROFILE': []}
    hazelib(hz)
    fm = crbFM().crbmodel(
        params['Teq_K'], clouds['CTP'],
        hazescale=clouds['HScale'], hazeloc=clouds['HLoc'], hazethick=clouds['HThick'], hzlib=hz,
        chemistry=chem_label, cheq=cheq, mixratio=mixratio, tea_data=tea_data, atom_data=atom_data,
        rp0=params['Rp_Rjup'] * ssc_mks['Rjup'],
        xsecs=xs['XSECS'], wgrid=wl, planet='b', orbp=orbp,
        hitemplist=xs_meta['hitemp'], cialist=xs_meta['cia'], xmollist=xs_meta['exomol'], atomlist=atoms_used,
        nlevels=NLEVELS, Hsmax=HSMAX, solrad=SOLRAD,
        break_down_by_molecule=True,
    )
    depth = np.asarray(fm.spectrum, dtype=float)
    by_mol = {str(k): np.asarray(v, dtype=float) for k, v in fm.breakdown_by_molecule.items()}
    if depth.shape != wl.shape or not np.all(np.isfinite(depth)):
        raise RuntimeError("Cerberus returned an invalid spectrum (wrong length or NaN) check the inputs.")

    out_dir = args['out_dir']
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, 'spectrum.csv')
    contrib_cols = [f'contribution_{k}' for k in by_mol]
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['wavelength_um', 'transit_depth'] + contrib_cols)
        for i in range(wl.size):
            w.writerow([wl[i], depth[i]] + [by_mol[k][i] for k in by_mol])

    # Mixing-ratio profiles (log10 VMR vs pressure), most abundant first
    pressure = np.asarray(fm.pressureGrid, dtype=float)
    profiles = {str(k): np.asarray(v, dtype=float) - 6.0 for k, v in fm.moleculeProfiles.items()}
    order = sorted(profiles, key=lambda k: -float(np.median(profiles[k])))
    vmr_path = os.path.join(out_dir, 'mixing_ratios.csv')
    with open(vmr_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['pressure_bar'] + [f'log10_vmr_{k}' for k in order])
        for i in range(pressure.size):
            w.writerow([pressure[i]] + [profiles[k][i] for k in order])

    # Which contributions matter (1% of the spectral range)
    base = min(float(np.min(v)) for v in by_mol.values())
    top = max(float(np.max(v)) for v in by_mol.values())
    span = (top - base) or 1.0
    strength = {k: (float(np.max(v)) - base) / span for k, v in by_mol.items()}
    significant = sorted([k for k, v in strength.items() if v >= 0.01], key=lambda k: -strength[k])

    title = f"{target_record['name'] if target_record else 'Custom planet'} Excalibur/Cerberus transmission model"
    plot_path = os.path.join(out_dir, 'spectrum.png')
    _plot_model(wl, depth, title, plot_path)

    record = {
        'created': datetime.datetime.now().isoformat(timespec='seconds'),
        'excalibur': os.path.dirname(excalibur.__file__),
        'target': target_record['name'] if target_record else None,
        'parameters': params,
        'parameter_source': 'Excalibur target list, with explicit overrides' if target_record else 'explicit',
        'parameter_references': target_record['units_and_references'] if target_record else None,
        'chemistry': chemistry_record,
        'clouds': {'cloud_top_log10_bar': clouds['CTP'], 'haze_log10_scale': clouds['HScale'], 'haze_location': clouds['HLoc'], 'haze_thickness': clouds['HThick']},
        'temperature_profile': 'isothermal at Teq',
        'cross_section_file': xs_path,
        'wavelength_grid': xs_meta.get('wavelength_grid'),
        'outputs': {
            'spectrum_csv': csv_path, 'mixing_ratios_csv': vmr_path, 'plot': plot_path,
            'columns': ['wavelength_um', 'transit_depth'] + contrib_cols,
            'units': 'wavelength in microns; transit depth = (Rp/R*)^2 as a fraction (x1e6 for ppm)',
        },
        'transit_depth_range_ppm': [float(depth.min() * 1e6), float(depth.max() * 1e6)],
        'significant_contributions': significant,
        'negligible_contributions': sorted(k for k in by_mol if k not in significant),
        'most_abundant_species_median_log10_vmr': {k: round(float(np.median(profiles[k])), 2) for k in order[:8]},
    }
    with open(os.path.join(out_dir, 'parameters.json'), 'w') as f:
        json.dump(record, f, indent=2, default=float)
    return record


def equilibrium_model(args):
    return _run_cerberus(args, 'equilibrium')


def free_model(args):
    return _run_cerberus(args, 'free')


def _plot_model(wl, depth, title, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(wl, depth * 1e6, color='#3a76b8', lw=2.5, label='Forward model')
    ax.set_xlabel('Wavelength (µm)', fontsize=13)
    ax.set_ylabel('Transit depth (ppm)', fontsize=13)
    ax.set_title(title, fontsize=15)
    ax.tick_params(labelsize=12)
    ax.legend(frameon=False, fontsize=13, loc='upper right')
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ariel_simulation

def ariel_simulation(args):
    import numpy as np
    data_dir = _setup(args)
    fm_dir = args['forward_model_dir']
    try:
        with open(os.path.join(fm_dir, 'parameters.json')) as f:
            fm_record = json.load(f)
        spec = np.genfromtxt(os.path.join(fm_dir, 'spectrum.csv'), delimiter=',', names=True)
    except OSError:
        raise RuntimeError(f"No forward model in {fm_dir}: run a Cerberus forward model first.")
    wl_model, depth_model = np.asarray(spec['wavelength_um']), np.asarray(spec['transit_depth'])

    target = args.get('target') or fm_record.get('target')
    if not target:
        raise RuntimeError("The Ariel noise model is per planet: give `target` (the forward model had no target).")
    target = _target_or_fail(data_dir, target)['name']
    tier = int(args.get('tier') or 2)
    if tier not in (1, 2, 3):
        raise RuntimeError("tier must be 1, 2 or 3.")

    from excalibur.ariel.ariel_instrument_model import load_ariel_instrument
    inst = load_ariel_instrument(target, _runtime(tier=tier))
    if inst is None:
        raise RuntimeError(f"No ArielRad noise model for {target} (ArielRad {ARIELRAD_VERSION} covers ~1770 planets).")
    wave = np.asarray(inst['wavelength'], dtype=float)
    low, high = np.asarray(inst['wavelow'], dtype=float), np.asarray(inst['wavehigh'], dtype=float)
    noise_single = np.asarray(inst['noise'], dtype=float)
    n_visits = int(args.get('n_transits') or inst['nVisits'])
    uncertainty = noise_single / np.sqrt(n_visits)

    # Rebin the model to the Ariel channels (mean of the model points in each channel, as in ariel/core.py);
    # a channel without model points is interpolated at its centre, if the model covers it
    binned = np.empty(wave.size)
    for i, (lo, hi) in enumerate(zip(low, high)):
        inside = (wl_model >= lo) & (wl_model <= hi)
        if inside.any():
            binned[i] = depth_model[inside].mean()
        elif wl_model.min() <= wave[i] <= wl_model.max():
            binned[i] = np.interp(wave[i], wl_model, depth_model)
        else:
            raise RuntimeError(f"The forward model covers {wl_model.min():.3g}-{wl_model.max():.3g} um, not the Ariel channel at {wave[i]:.3g} um. Use the 'ariel' cross-section grid.")

    seed = int(args.get('seed', 1234567))
    observed = binned + np.random.default_rng(seed).normal(scale=uncertainty)

    out_dir = args['out_dir']
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, 'observation.csv')
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['wavelength_um', 'wavelength_low_um', 'wavelength_high_um', 'model_transit_depth', 'observed_transit_depth', 'uncertainty'])
        for row in zip(wave, low, high, binned, observed, uncertainty):
            w.writerow(row)
    # Same 4-column format as DownloadDataset spectra, usable by SimulateTaurexRetrieval
    dat_path = os.path.join(out_dir, 'spectrum.dat')
    np.savetxt(dat_path, np.column_stack((wave, observed, uncertainty, high - low)),
               header='wavelength_um transit_depth uncertainty bin_width_um')

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(wave, binned * 1e6, color='#3a76b8', lw=2.5, label='Forward model', zorder=1)
    ax.errorbar(wave, observed * 1e6, yerr=uncertainty * 1e6, fmt='o', ms=4, color='#f0a04b', ecolor='#f0a04b', elinewidth=2, alpha=0.85, label=f'Simulated Ariel Tier {tier}', zorder=2)
    ax.set_xlabel('Wavelength (µm)', fontsize=13)
    ax.set_ylabel('Transit depth (ppm)', fontsize=13)
    ax.set_title(f'{target} Excalibur/Cerberus transmission model', fontsize=15)
    ax.tick_params(labelsize=12)
    ax.legend(frameon=False, fontsize=13, loc='upper right')
    fig.tight_layout()
    plot_path = os.path.join(out_dir, f'ariel_tier{tier}.png')
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    residual = (observed - binned) / uncertainty
    record = {
        'created': datetime.datetime.now().isoformat(timespec='seconds'),
        'target': target,
        'forward_model_dir': fm_dir,
        'forward_model_chemistry': fm_record.get('chemistry', {}).get('mode'),
        'tier': tier,
        'n_transits': n_visits,
        'n_transits_source': 'given' if args.get('n_transits') else f'ArielRad {ARIELRAD_VERSION}, tier {tier}',
        'seed': seed,
        'noise_model': f'ArielRad {ARIELRAD_VERSION} (mmw Thorngren) single-transit noise / sqrt(n_transits)',
        'n_channels': int(wave.size),
        'median_uncertainty_ppm': float(np.median(uncertainty) * 1e6),
        'model_depth_range_ppm': [float(binned.min() * 1e6), float(binned.max() * 1e6)],
        'chi2_per_channel_of_noise': float(np.mean(residual**2)),
        'outputs': {'observation_csv': csv_path, 'spectrum_dat': dat_path, 'plot': plot_path,
                    'units': 'wavelength in microns; transit depth as a fraction (x1e6 for ppm)'},
    }
    with open(os.path.join(out_dir, 'simulation.json'), 'w') as f:
        json.dump(record, f, indent=2, default=float)
    return record


# lethe_light_curve (C. Bernardin)

# LETHE grids, in the order excalibur.transit.core.occultation expects (g1..g8)
LETHE_GRIDS = ['grid_G/0.25.npy', 'grid_G/0.5.npy', 'grid_G/0.75.npy', 'grid_G/1.0.npy', 'grid_F/0.5.npy', 'grid_F/1.0.npy', 'grid_F/1.5.npy', 'grid_F/2.0.npy']


def lethe_light_curve(args):
    import numpy as np
    from scipy.interpolate import RectBivariateSpline
    _setup(args)
    import excalibur
    import excalibur.transit.core as tc
    from excalibur.util import time as tm
    excalibur_dir = os.path.dirname(excalibur.__file__)
    if not hasattr(tc, 'occultation'):
        raise RuntimeError(f"The Excalibur in {excalibur_dir} has no LETHE light-curve model, set lethe_esp_directory to an esp checkout that has it.")
    if not hasattr(np, 'acos'):
        raise RuntimeError(f"LETHE needs numpy >= 2 (np.acos), this environment has numpy {np.__version__}. Set lethe_python to an environment with numpy 2.")
    from excalibur.transit.fmcontext import dctxupdt

    # LETHE grids: default is <DATA_BASEDIR>/LETHE, as in excalibur.transit.core
    lethe_dir = os.path.expanduser(args.get('lethe_dir') or tc.LETHE_dir)
    needed = ['parameters/z_grid.npy', 'parameters/rprs_grid.npy'] + LETHE_GRIDS
    missing = [n for n in needed if not os.path.isfile(os.path.join(lethe_dir, n))]
    if missing:
        raise RuntimeError(f"LETHE grid folder '{lethe_dir}' is missing {missing}.")
    z_grid = np.load(os.path.join(lethe_dir, 'parameters/z_grid.npy'))
    rprs_grid = np.load(os.path.join(lethe_dir, 'parameters/rprs_grid.npy'))
    interpolators = [RectBivariateSpline(z_grid, rprs_grid, np.load(os.path.join(lethe_dir, n))) for n in LETHE_GRIDS]
    dctxupdt()
    dctxupdt(dct={'LETHE': interpolators}, freeze=True)

    rprs = float(args['rprs'])
    if not rprs_grid.min() < rprs <= rprs_grid.max():
        raise RuntimeError(f"rprs={rprs} is outside the LETHE grid ({rprs_grid.min()} < rprs <= {rprs_grid.max()}).")
    ld = [float(g) for g in (args.get('limb_darkening') or [])]
    if len(ld) > 8:
        raise RuntimeError(f"limb_darkening takes at most 8 coefficients (g1..g8), got {len(ld)}.")
    g = ld + [0.0] * (8 - len(ld))

    # Geometry: a separation grid, or times converted to separations with Excalibur's time2z
    time = phase = orbit = None
    if args.get('period_days'):
        period, sma, inc = float(args['period_days']), args.get('sma_rstar'), args.get('inclination_deg')
        if not sma or inc is None:
            raise RuntimeError("The time mode (period_days) also needs sma_rstar and inclination_deg.")
        sma, inc = float(sma), float(inc)
        ecc, tmid = float(args.get('ecc') or 0.0), float(args.get('tmid_days') or 0.0)
        impact = sma * np.cos(np.radians(inc))
        if impact >= 1.0 + rprs:
            raise RuntimeError(f"Impact parameter b = a cos(i) = {impact:.3f} >= 1 + rprs: the planet does not transit.")
        # circular-orbit T14, only used for the default time window
        t14 = period / np.pi * np.arcsin(np.sqrt((1 + rprs)**2 - impact**2) / (sma * np.sin(np.radians(inc))))
        if args.get('time_grid'):
            lo, hi, n = _uniform(args['time_grid'], 'time_grid')
        else:
            lo, hi, n = tmid - t14, tmid + t14, 1000
        time = np.linspace(lo, hi, n)
        z, phase = tm.time2z(time.copy(), inc, tmid, sma, period, ecc)
        orbit = {'period_days': period, 'sma_rstar': sma, 'inclination_deg': inc, 'ecc': ecc, 'tmid_days': tmid,'impact_parameter': float(impact), 't14_days_circular': float(t14)}
    else:
        lo, hi, n = _uniform(args.get('z_grid') or '-1.5,1.5,601', 'z_grid')
        z = np.linspace(lo, hi, n)
    z = np.asarray(z, dtype=float)
    # Near secondary eclipse (|phase| > 0.25) the planet is behind the star: no transit
    front = np.ones(z.size, dtype=bool) if phase is None else np.abs(phase) <= 0.25

    flux = np.ones(z.size)
    flux[front] = tc.tldlc(np.abs(z[front]), rprs, *g)
    if not np.all(np.isfinite(flux)):
        raise RuntimeError("LETHE returned NaN/inf; check the limb-darkening coefficients.")
    numerical = None
    if args.get('compare_numerical', True):
        numerical = np.ones(z.size)
        numerical[front] = tc.tldlc(np.abs(z[front]), rprs, *g, model='numerical')

    out_dir = args['out_dir']
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, 'light_curve.csv')
    columns = (['time_days', 'phase'] if time is not None else []) + ['z_rstar', 'flux_lethe']
    if numerical is not None:
        columns.append('flux_numerical')
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(columns)
        for i in range(z.size):
            row = ([time[i], phase[i]] if time is not None else []) + [z[i], flux[i]]
            if numerical is not None:
                row.append(numerical[i])
            w.writerow(row)

    record = {
        'created': datetime.datetime.now().isoformat(timespec='seconds'),
        'model': 'LETHE (excalibur.transit.core.tldlc, interpolated occulted flux)',
        'excalibur': excalibur_dir,
        'lethe_grids': lethe_dir,
        'rprs': rprs,
        'limb_darkening': {'g1..g4 (Claret 4NL: mu^0.5, mu, mu^1.5, mu^2)': g[:4],
                           'g5..g8 (extended: mu^k ln(mu), k=1..4)': g[4:]},
        'geometry': ({'mode': 'time', **orbit, 'time_grid_days': [float(time[0]), float(time[-1]), int(time.size)]}
                     if time is not None else
                     {'mode': 'separation', 'z_grid_rstar': [float(z[0]), float(z[-1]), int(z.size)]}),
        'outputs': {'light_curve_csv': csv_path, 'columns': columns, 'units': 'flux normalised to 1 out of transit; z = planet-star separation in stellar radii'},
        'transit_depth_ppm': float((1.0 - flux.min()) * 1e6),
        'rprs_squared_ppm': rprs**2 * 1e6,
        'n_points': int(z.size),
        'n_points_in_transit': int((flux < 1.0).sum()),
    }
    if numerical is not None:
        record['max_abs_diff_lethe_vs_numerical_ppm'] = float(np.max(np.abs(flux - numerical)) * 1e6)
    with open(os.path.join(out_dir, 'parameters.json'), 'w') as f:
        json.dump(record, f, indent=2, default=float)
    return record


COMMANDS = {'find_target': find_target, 'cross_sections': cross_sections, 'equilibrium_model': equilibrium_model, 'free_model': free_model, 'ariel_simulation': ariel_simulation, 'lethe_light_curve': lethe_light_curve}

if __name__ == '__main__':
    command, raw = sys.argv[1], sys.argv[2]
    try:
        result = {'ok': True, 'result': COMMANDS[command](json.loads(raw))}
    except Exception as e:
        result = {'ok': False, 'error': f'{type(e).__name__}: {e}',
                  'traceback': traceback.format_exc().splitlines()[-6:]}
    sys.stdout.write('\n' + RESULT_PREFIX + json.dumps(result, default=float) + '\n')
