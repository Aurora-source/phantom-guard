"""Idempotent workspace creation, immutable data import, and actionable preflight."""
from __future__ import annotations
import csv
import hashlib
import importlib.metadata
import json
import platform
import shutil
import tempfile
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
from phantomguard.config import load_baseline, paths, raw_path

CSV_COLUMNS = ('source_file cycle_num scan_counter meas_counter sync_timestamp sync_status obj_count_header '
               'obj_count_actual slot obj_timestamp raw_len raw_hex x_m y_m range_m azimuth_deg vx_mps vy_mps '
               'speed_mps is_moving dyn_prop rcs_dbsm').split()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def initialize(cfg: dict) -> dict:
    p = paths(cfg)
    for d in (p.raw, p.processed, p.models, p.output, p.config.parent, p.reports, p.root / 'deployment-bundles'):
        d.mkdir(parents=True, exist_ok=True)
    if not p.config.exists():
        p.config.write_text(files('phantomguard.resources').joinpath('default.yaml').read_text(encoding='utf-8'), encoding='utf-8')
    return {'ok': True, 'paths': {k: str(v) for k, v in asdict(p).items()}}


def inspect_csv(path: Path) -> dict:
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != CSV_COLUMNS:
            raise ValueError(f'{path.name}: expected decoder-v2 22-column schema {CSV_COLUMNS}; use phantomguard.decoder to decode captures')
        first = next(reader, None)
        if first is None:
            raise ValueError(f'{path.name}: empty recording')
    return {'file': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path), 'columns': CSV_COLUMNS}


def import_data(cfg: dict, source) -> dict:
    src, target = Path(source).expanduser().resolve(), paths(cfg).raw
    missing = [f for f in cfg['data']['files'] if not (src / f).is_file()]
    if missing:
        raise FileNotFoundError(f'{src}: missing {missing}; supply the exact case-sensitive filenames listed in docs/data.md')
    rows = [inspect_csv(src / f) for f in cfg['data']['files']]
    # Validate everything before mutation; never overwrite a differing recording.
    for r in rows:
        dest = target / r['file']
        if dest.exists() and digest(dest) != r['sha256']:
            raise ValueError(f'{dest} differs from the source; choose a different --data-dir')
    target.mkdir(parents=True, exist_ok=True)
    for r in rows:
        dest = target / r['file']
        if not dest.exists():
            shutil.copyfile(src / r['file'], dest)
        if digest(src / r['file']) != r['sha256'] or digest(dest) != r['sha256']:
            raise RuntimeError(f"{r['file']}: source changed during import or copy checksum mismatch")
    p = paths(cfg)
    p.output.mkdir(parents=True, exist_ok=True)
    (p.output / 'data-import.json').write_text(json.dumps({'files': rows}, indent=2) + '\n', encoding='utf-8')
    return {'ok': True, 'raw_dir': str(target), 'files': rows, 'source_modified': False}


def doctor(cfg: dict, full=False) -> dict:
    from phantomguard.detect.pipeline import load_artifacts
    from phantomguard.detect.autoencoder import load_iforest
    p = paths(cfg)
    errors, recordings, artifacts = [], [], []
    versions = {'python': platform.python_version(), 'platform': platform.platform()}
    for name in ('phantomguard', 'numpy', 'scipy', 'PyYAML', 'waitress', 'torch', 'pandas', 'scikit-learn', 'matplotlib'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = 'not installed (optional unless required by this command)'
    if platform.python_version_tuple()[:2] != ('3', '11'):
        errors.append('Python 3.11 required by the pinned artifact/runtime set; create a Python 3.11 venv or use Dockerfile')
    for name,wanted in {'numpy':'2.4.6','scipy':'1.17.1','PyYAML':'6.0.3','waitress':'3.0.2',**({'scikit-learn':'1.9.1'} if full else {})}.items():
        if versions[name]!=wanted:
            errors.append(f'{name} {wanted} required, found {versions[name]}; reinstall requirements/runtime.lock (offline.lock for full evaluation)')
    for f in cfg['data']['files']:
        try:
            recordings.append(inspect_csv(raw_path(cfg, f)))
        except (OSError, ValueError) as exc:
            errors.append(f'{exc}; next: phantomguard import-data --source DIRECTORY (or --data-dir DIRECTORY)')
    tags = ['timeblock'] + ([f'loso_{Path(f).stem}' for f in cfg['data']['files']] if full else [])
    for tag in tags:
        bp = p.baseline if tag == 'timeblock' else p.processed / f'baseline_{tag}.json'
        try:
            b = load_baseline(bp)
            for key in ('soft_quantile', 'fusion_mn'):
                if key not in b:
                    raise ValueError(f'{bp}: {key} calibration missing; next: phantomguard calibrate --loso')
            m,n=b['fusion_mn']['value']
            if type(m) is not int or type(n) is not int or not 1<=m<=n:
                raise ValueError(f'{bp}: invalid calibrated fusion M/N; restore or recalibrate on validation')
            ae, lib = load_artifacts(tag, strict=True, cfg=cfg, baseline=b)
            if full:
                load_iforest(tag, cfg=cfg, baseline=b, strict=True)
            artifacts.append({'tag': tag, 'ae_artifact_id': ae.metadata['artifact_id'], 'library_fingerprints': len(lib),
                              'baseline_sha256': digest(bp), 'fusion_mn': b['fusion_mn']['value']})
        except (OSError, ValueError, RuntimeError, ImportError, KeyError) as exc:
            errors.append(f'{tag}: {exc}; next: restore matching bundle, or offline baseline --loso -> train --loso -> calibrate --loso')
    for directory in ((p.output, p.processed) if full else (p.output,)):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory) as f:
                f.write(b'write probe')
        except OSError as exc:
            errors.append(f'Output directory unwritable {directory}: {exc}; choose --output-dir/--processed-dir or fix permissions')
    return {'ok': not errors, 'paths': {k: str(v) for k, v in asdict(p).items()}, 'versions': versions,
            'recordings': recordings, 'artifacts': artifacts, 'errors': errors,
            'trust': 'Only load trusted local trained NPZ/pickle artifacts; browser accepts no artifact uploads'}
