"""Installed CLI/path/data/archive checks, independent of developer ignored files."""
from __future__ import annotations
import csv
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
import pytest
from phantomguard.config import REPO_ROOT, load_config, paths, raw_path
from phantomguard.workspace import CSV_COLUMNS, digest, initialize, import_data, doctor
from phantomguard.eval.report import provenance


def test_precedence_and_cwd_independence(tmp_path,monkeypatch):
    root=tmp_path/'workspace with spaces'
    cfg=load_config(root=root)
    initialize(cfg)
    monkeypatch.setenv('PHANTOMGUARD_DATA_DIR','external data')
    monkeypatch.setenv('PHANTOMGUARD_MODELS_DIR','trained models')
    monkeypatch.chdir(tmp_path)
    selected=load_config(root=root,overrides={'data_dir':'chosen raw'})
    assert paths(selected).raw==root/'chosen raw'
    assert paths(selected).models==root/'trained models'
    assert raw_path(selected,'emptyRoom.csv')==root/'chosen raw/emptyRoom.csv'
    assert paths(selected).baseline==root/'configs/baseline.json'
    initialize(selected);initialize(selected)
    assert (root/'runs').is_dir()
    with pytest.raises(ValueError,match='absolute'):
        load_config(root='relative')


def test_fixed_splits_cannot_be_reconfigured_into_training_data(tmp_path):
    import yaml
    cfg=load_config(root=tmp_path)
    initialize(cfg)
    path=paths(cfg).config
    content=yaml.safe_load(path.read_text())
    content['splits']['train_frac']=0.1
    path.write_text(yaml.safe_dump(content))
    with pytest.raises(ValueError,match='fixed at 60/20/20'):
        load_config(root=tmp_path)


def test_absolute_baseline_does_not_require_an_implicit_workspace(tmp_path,monkeypatch):
    import phantomguard.paths as p
    def no_default(*args,**kw):
        raise AssertionError('Absolute paths must not resolve an unrelated workspace')
    monkeypatch.setattr(p,'load_config',no_default)
    path=tmp_path/'baseline.json'
    p.save_baseline({'fixture':True},path)
    assert p.load_baseline(path)=={'fixture':True}


def test_import_preserves_source_bytes_and_refuses_collisions(tmp_path):
    cfg=load_config(root=tmp_path/'target')
    source=tmp_path/'external source';source.mkdir()
    for f in cfg['data']['files']:
        with (source/f).open('w',newline='',encoding='utf-8') as stream:
            w=csv.DictWriter(stream,fieldnames=CSV_COLUMNS);w.writeheader();w.writerow({c:'0' for c in CSV_COLUMNS})
    before={p.name:digest(p) for p in source.iterdir()}
    import_data(cfg,source);import_data(cfg,source)
    assert before=={p.name:digest(p) for p in source.iterdir()}
    assert before=={f:digest(raw_path(cfg,f)) for f in cfg['data']['files']}
    raw_path(cfg,cfg['data']['files'][0]).write_text('different')
    with pytest.raises(ValueError,match='differs'):
        import_data(cfg,source)
    assert before=={p.name:digest(p) for p in source.iterdir()}


def test_missing_and_case_sensitive_data_are_actionable(tmp_path):
    cfg=load_config(root=tmp_path)
    initialize(cfg)
    (paths(cfg).raw/'emptyroom.csv').write_text('wrong case')
    state=doctor(cfg)
    assert not state['ok']
    assert any('emptyRoom.csv' in e for e in state['errors'])
    assert any('import-data' in e for e in state['errors'])
    assert any('trained artifacts missing' in e or 'Baseline missing' in e for e in state['errors'])


def test_reporting_without_rtk_or_git_and_no_absolute_paths(monkeypatch):
    def missing(*a,**kw):
        raise FileNotFoundError('git')
    monkeypatch.setattr(subprocess,'run',missing)
    cfg=load_config()
    result=provenance(cfg,[paths(cfg).models/'missing.npz'])
    assert result['git_head'] is None
    assert result['artifacts'][0]['path']=='models/missing.npz'
    assert result['configuration']['data']['raw_dir']=='data/raw'
    assert '_paths' not in result['configuration']
    assert all('\\' not in k for k in result['implementation_sha256'])


def test_installed_entrypoint_from_another_directory(tmp_path):
    root=tmp_path/'clean root with spaces'
    r=subprocess.run([sys.executable,'-m','phantomguard','init','--root',str(root)],cwd=tmp_path,capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    assert (root/'configs/default.yaml').is_file()
    r=subprocess.run([sys.executable,'-m','phantomguard','doctor','--root',str(root)],cwd=tmp_path,capture_output=True,text=True)
    assert r.returncode==2 and not json.loads(r.stdout)['ok']


def test_provenance_uses_explicit_root_without_environment(tmp_path,monkeypatch):
    from phantomguard.eval import report
    root=tmp_path/'explicit workspace'
    cfg=load_config(root=root)
    initialize(cfg)
    module=root/'src/phantomguard/example.py'
    module.parent.mkdir(parents=True)
    module.write_text('# source from selected checkout\n',encoding='utf-8')
    monkeypatch.setattr(report,'REPO_ROOT',tmp_path/'unrelated installed wheel')
    result=report.provenance(cfg,[])
    assert result['implementation_sha256']=={'src/phantomguard/example.py':digest(module)}
    assert report.portable_path(paths(cfg).output/'attack_eval/x_labels.csv',cfg)=='runs/attack_eval/x_labels.csv'


def test_decoder_csv_export_is_explicit_utf8(tmp_path,monkeypatch):
    from phantomguard.decoder import write_csv, ScanCycle, decode_object
    from phantomguard.io.replay import ReplaySource
    import builtins
    real_open=builtins.open
    def legacy_locale(*args,**kwargs):
        if 'b' not in (args[1] if len(args)>1 else kwargs.get('mode','r')):
            kwargs.setdefault('encoding','cp1252')
        return real_open(*args,**kwargs)
    monkeypatch.setattr(builtins,'open',legacy_locale)
    path=tmp_path/'export.csv'
    cycle=ScanCycle(1,scan_counter=1,meas_counter=1,obj_count_hdr=1,
                    sync_status=1,sync_timestamp=700,objects=[decode_object([0]*8,720)])
    write_csv([cycle],path,'\u6d4b\u91cf\u2713',0.1)
    with path.open(encoding='utf-8',newline='') as stream:
        assert next(csv.DictReader(stream))['source_file']=='\u6d4b\u91cf\u2713'
    frames=list(ReplaySource(path))
    assert len(frames)==2 and frames[1].data==bytes(8)


def test_bundle_safe_paths_and_payload_checksums(tmp_path):
    from phantomguard.bundle import verify
    import hashlib
    path=tmp_path/'fixture.zip'
    def write(name):
        payload=b'bytes'
        manifest={'schema':1,'tested_code_sha':'fixture','payload_bytes':5,'payload':[{'path':name,'bytes':5,'sha256':hashlib.sha256(payload).hexdigest()}]}
        with zipfile.ZipFile(path,'w') as z:
            z.writestr(name,payload);z.writestr('bundle-manifest.json',json.dumps(manifest))
        path.with_suffix('.zip.sha256').write_text(digest(path)+'  fixture.zip\n')
    write('data/raw/recording.csv')
    assert verify(path)['payload_files']==1
    write('../escaped')
    with pytest.raises(ValueError,match='Unsafe'):
        verify(path)
    write('data/raw/recording.csv')
    path.with_suffix('.zip.sha256').write_text('0'*64)
    with pytest.raises(ValueError,match='Archive SHA256'):
        verify(path)
