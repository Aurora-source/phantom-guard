"""Deterministic payload ZIP with verified safe restoration; archives are never tracked."""
from __future__ import annotations
import json
import os
import platform
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from phantomguard.config import paths, load_baseline
from phantomguard.workspace import digest, doctor

REQUIRED_REPORTS = ('summary.md', 'clean_eval.md', 'clean_eval.csv', 'attack_eval_manifest.json',
                    'attack_eval_runs.csv', 'attack_eval_instances.csv', 'attack_eval_clean.csv',
                    'attack_eval_exclusions.csv', 'attack_eval_matrix.csv', 'attack_eval_layers.csv')


def source_sha(root: Path) -> str:
    r = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    if r.returncode:
        raise ValueError('Bundle creation needs the matching Git checkout and tested implementation commit')
    return r.stdout.strip()


def build(cfg: dict, archive=None) -> dict:
    state = doctor(cfg, full=True)
    if not state['ok']:
        raise ValueError('Bundle preflight failed: ' + '; '.join(state['errors']))
    p, payload = paths(cfg), {}
    sha = source_sha(p.root)
    for name in cfg['data']['files']:
        payload['data/raw/' + name] = p.raw / name
    payload['configs/default.yaml'] = p.config
    payload['configs/baseline.json'] = p.baseline
    tags = ['timeblock'] + [f'loso_{Path(f).stem}' for f in cfg['data']['files']]
    for tag in tags:
        for stem, suffix in (('ae', '.npz'), ('iforest', '.pkl'), ('replay_library', '.pkl')):
            payload[f'models/{stem}_{tag}{suffix}'] = p.models / f'{stem}_{tag}{suffix}'
        if tag != 'timeblock':
            payload[f'data/processed/baseline_{tag}.json'] = p.processed / f'baseline_{tag}.json'
    for name in REQUIRED_REPORTS:
        if not (p.reports / name).is_file():
            raise FileNotFoundError(f'{p.reports/name}: required generated report missing; run clean-eval and attack-eval offline, then select evidence into docs/results')
        payload['docs/results/' + name] = p.reports / name
    for name in ('dataset_inventory.json','portable_validation.json','rcs_vs_range.md','README.md'):
        if (p.reports/name).is_file():
            payload['docs/results/'+name] = p.reports/name
    # Deploy configuration must contain only portable relative paths. Baselines
    # contain split names and calibration metadata, never developer data roots.
    from phantomguard.eval.report import portable_config
    import yaml
    config_bytes = yaml.safe_dump(portable_config(cfg), sort_keys=False).encode('utf-8')
    if yaml.safe_load(p.config.read_text(encoding='utf-8')) == portable_config(cfg):
        # Preserve the tracked portable YAML bytes/comments so restoration into a
        # matching clean clone is idempotent rather than a configuration overwrite.
        config_bytes = p.config.read_bytes()
    manifest = {'schema': 1, 'tested_code_sha': sha, 'python': '3.11', 'package_version': '0.2.0',
                'versions': state['versions'], 'configuration': portable_config(cfg),
                'artifact_ids': state['artifacts'], 'payload': []}
    import hashlib
    contents = {}
    for name, path in sorted(payload.items()):
        data = config_bytes if name == 'configs/default.yaml' else path.read_bytes()
        if name.startswith('configs/') or name.startswith('data/processed/') or (name.startswith('docs/results/') and path.suffix in {'.md','.json','.csv'}):
            data=data.replace(b'\r\n',b'\n')
        contents[name] = data
        manifest['payload'].append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    manifest['payload_bytes'] = sum(r['bytes'] for r in manifest['payload'])
    destination = Path(archive) if archive else p.root/'deployment-bundles'/f'phantomguard-{sha[:12]}.zip'
    if not destination.is_absolute():
        destination = p.root / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f'{destination}: archive exists; choose a new --archive (existing archives are preserved)')
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, data in {**contents, 'bundle-manifest.json': (json.dumps(manifest, indent=2, sort_keys=True)+'\n').encode()}.items():
            info = zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100444 << 16)
            z.writestr(info, data)
    checksum = digest(destination)
    destination.with_suffix(destination.suffix+'.sha256').write_text(f'{checksum}  {destination.name}\n', encoding='utf-8')
    verify(destination)
    # Confirm all source bytes still match, independently of the archived copy.
    for row in manifest['payload']:
        if row['path'].startswith('data/raw/') and digest(payload[row['path']]) != row['sha256']:
            raise RuntimeError('Original dataset changed during bundle build: '+row['path'])
    return {'path': str(destination), 'bytes': destination.stat().st_size, 'sha256': checksum,
            'payload_bytes': manifest['payload_bytes'], 'payload_files': len(contents), 'tested_code_sha': sha}


def verify(archive) -> dict:
    path = Path(archive)
    checksum = digest(path)
    external = path.with_suffix(path.suffix+'.sha256')
    if not external.is_file():
        raise FileNotFoundError(f'{external}: archive checksum required alongside ZIP')
    if external.read_text(encoding='utf-8').split()[0] != checksum:
        raise ValueError('Archive SHA256 mismatch')
    import hashlib
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Archive has duplicate paths')
        for info in z.infolist():
            p = PurePosixPath(info.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in info.filename or ':' in info.filename or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe archive path: '+info.filename)
        if sum(i.file_size for i in z.infolist()) > 512*1024*1024:
            raise ValueError('Archive expanded payload exceeds 512 MiB bound')
        manifest = json.loads(z.read('bundle-manifest.json'))
        if manifest.get('schema') != 1:
            raise ValueError('Unsupported bundle schema')
        if set(names) != {'bundle-manifest.json'} | {r['path'] for r in manifest['payload']}:
            raise ValueError('Archive payload differs from manifest')
        for row in manifest['payload']:
            data = z.read(row['path'])
            if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ValueError('Payload checksum mismatch: '+row['path'])
    return {'ok': True, 'sha256': checksum, 'tested_code_sha': manifest['tested_code_sha'],
            'payload_bytes': manifest['payload_bytes'], 'payload_files': len(manifest['payload'])}


def restore(cfg: dict, archive) -> dict:
    result, p = verify(archive), paths(cfg)
    if source_sha(p.root) != result['tested_code_sha']:
        # A documentation-only child commit may be checked out, but implementation
        # should be restored at the explicit tested SHA for a deterministic handoff.
        raise ValueError(f"Checkout must match bundle tested SHA {result['tested_code_sha']}; git checkout --detach SHA first")
    with zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read('bundle-manifest.json'))
        destinations = [(p.root/row['path'], row) for row in manifest['payload']]
        for dest, row in destinations:
            if dest.exists() and digest(dest) != row['sha256']:
                raise ValueError(f'{dest}: existing content differs; restoration never overwrites it')
        for dest, row in destinations:
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(row['path']))
        (p.root/'bundle-manifest.json').write_bytes(z.read('bundle-manifest.json'))
    return {**result, 'restored_to': str(p.root)}
