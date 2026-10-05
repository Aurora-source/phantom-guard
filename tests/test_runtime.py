"""Runtime caches and resource policy; no artifact construction."""
import os
from pathlib import Path
import pytest
from phantomguard.config import load_config
from phantomguard.web.runtime import numerical_policy, input_stamp, content_identity
from phantomguard.web.api import Application


def test_numerical_policy_is_explicit_and_respects_environment(monkeypatch):
    names=('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS')
    for name in names: monkeypatch.delenv(name,raising=False)
    monkeypatch.delenv('PHANTOMGUARD_RUNTIME_PROFILE',raising=False)
    assert numerical_policy()=='development'
    assert not any(name in os.environ for name in names)
    monkeypatch.setenv('PHANTOMGUARD_RUNTIME_PROFILE','server')
    monkeypatch.setenv('OMP_NUM_THREADS','3')
    assert numerical_policy()=='server'
    assert os.environ['OMP_NUM_THREADS']=='3'
    assert os.environ['OPENBLAS_NUM_THREADS']=='1'


def test_readiness_invalidates_missing_replaced_config_and_recovers(tmp_path,monkeypatch):
    cfg=load_config(root=tmp_path)
    raw=Path(cfg['data']['raw_dir']);raw.mkdir(parents=True)
    recording=raw/cfg['data']['files'][0];recording.write_text('original')
    calls=[]
    def doctor(cfg):
        calls.append(1)
        return {'ok':recording.exists(),'errors':[]}
    monkeypatch.setattr('phantomguard.web.api.doctor',doctor)
    monkeypatch.setattr('phantomguard.web.api.content_identity',lambda stamp:'verified-test-identity')
    app=Application(cfg,workers=1)
    try:
        assert app.readiness()['ok']
        assert app.readiness()['ok'] and len(calls)==1
        recording.unlink()
        assert not app.readiness()['ok'] and len(calls)==2
        recording.write_text('replacement')
        assert app.readiness()['ok'] and len(calls)==3
        app.readiness(force=True)
        assert len(calls)==4
        Path(cfg['_paths']['config']).parent.mkdir(parents=True,exist_ok=True)
        Path(cfg['_paths']['config']).write_text('changed')
        assert not app.readiness()['ok']
    finally: app.close()


def test_report_cache_filters_versions_and_bound(tmp_path,monkeypatch):
    app=Application(load_config(root=tmp_path),workers=1)
    count=[]
    def read(e,individual):
        count.append(1)
        return {'query':e.get('QUERY_STRING','')}
    monkeypatch.setattr(app,'_read_evaluation',read)
    try:
        assert app.evaluation({})==app.evaluation({}) and len(count)==1
        assert app.evaluation({'QUERY_STRING':'seed=12'})['query']=='seed=12'
        reports=tmp_path/'docs/results';reports.mkdir(parents=True)
        (reports/'attack_eval_manifest.json').write_text('{}')
        app.evaluation({})
        assert len(count)==3 and len(app._reports)==1
        for i in range(40):app.evaluation({'QUERY_STRING':str(i)})
        assert len(app._reports)<=16
    finally: app.close()


@pytest.mark.parametrize('path',['/assets/../api.py','/assets/a/../../api.py','/assets//app.js','/assets/a\\b','/assets/missing.js'])
def test_static_paths_are_confined(tmp_path,path):
    from phantomguard.web.jobs import RequestError
    app=Application(load_config(root=tmp_path),workers=1)
    try:
        with pytest.raises(RequestError) as exc:app.route({'REQUEST_METHOD':'GET','PATH_INFO':path})
        assert exc.value.status==404
    finally:app.close()


def _fake_warm_loop(connection,cfg):
    import json
    while True:
        request,directory=connection.recv()
        Path(directory,'result.json').write_text(json.dumps({'seed':request['seed']}))
        connection.send('done')


def test_warm_process_reuses_pid_and_can_be_terminated(tmp_path,monkeypatch):
    import multiprocessing as mp
    import time
    from phantomguard.web import jobs
    monkeypatch.setattr(jobs,'warm_loop',_fake_warm_loop)
    worker=jobs.WarmProcess(mp.get_context('spawn'),{})
    try:
        pid=None
        for seed in (11,22):
            worker.assign({'seed':seed},str(tmp_path))
            worker.start()
            pid=pid or worker.process.pid
            assert worker.process.pid==pid
            end=time.monotonic()+10
            while worker.is_alive() and time.monotonic()<end:time.sleep(.01)
            assert worker.done and worker.reusable()
            assert __import__('json').loads((tmp_path/'result.json').read_text())['seed']==seed
        worker.jobs=15
        assert worker.reusable()
        worker.jobs=16
        assert not worker.reusable()
    finally:
        worker.terminate();worker.join(timeout=3)
        assert not worker.process.is_alive()
        worker.close()


def test_input_content_identity_and_metadata_replacement(tmp_path):
    cfg=load_config(root=tmp_path)
    for d in ('models','data/processed','data/raw','configs'):(tmp_path/d).mkdir(parents=True,exist_ok=True)
    for f in cfg['data']['files']:(tmp_path/'data/raw'/f).write_text('csv')
    (tmp_path/'configs/default.yaml').write_text('cfg')
    (tmp_path/'configs/baseline.json').write_text('{}')
    original=input_stamp(cfg)
    identity=content_identity(original)
    rec=tmp_path/'data/raw'/cfg['data']['files'][0]
    replaced=rec.with_suffix('.new');replaced.write_text('CSV');replaced.replace(rec)
    changed=input_stamp(cfg)
    assert original!=changed and identity!=content_identity(changed)
    rec.unlink()
    with pytest.raises(FileNotFoundError):content_identity(input_stamp(cfg))


def test_pool_adapter_freezes_and_bounds_existing_material():
    import numpy as np
    from dataclasses import dataclass
    from phantomguard.web.cache import freeze_pools
    @dataclass
    class Pool:
        points: object
        tracks: object
    pool=Pool(np.ones((2,2)),[np.ones((3,3))])
    cache={'identity-config-segments':pool}
    info=freeze_pools(cache)
    assert info['retained_pool_bytes']==13*8
    assert isinstance(pool.tracks,tuple)
    with pytest.raises(ValueError):pool.points[0,0]=2
    with pytest.raises(ValueError):pool.tracks[0][0,0]=2
    cache.update(a=pool,b=pool)
    assert freeze_pools(cache)['evicted'] and not cache


def test_additional_asset_mime_and_symlink_rejection(tmp_path,monkeypatch):
    from phantomguard.web.jobs import RequestError
    package=tmp_path/'package';assets=package/'static/assets';assets.mkdir(parents=True)
    (assets/'model.svg').write_text('<svg/>')
    (assets/'outside.svg').symlink_to(tmp_path/'secret.svg')
    (tmp_path/'secret.svg').write_text('secret')
    monkeypatch.setattr('phantomguard.web.api.files',lambda name:package)
    app=Application(load_config(root=tmp_path),workers=1)
    try:
        status,body,mime=app.route({'REQUEST_METHOD':'GET','PATH_INFO':'/assets/model.svg'})
        assert (status,body,mime)==(200,b'<svg/>','image/svg+xml')
        with pytest.raises(RequestError):app.route({'REQUEST_METHOD':'GET','PATH_INFO':'/assets/outside.svg'})
    finally:app.close()


def test_plain_cli_development_has_no_server_policy(tmp_path,monkeypatch):
    from phantomguard.cli import main
    for key in ('PHANTOMGUARD_RUNTIME_PROFILE','PHANTOMGUARD_JOB_COOLDOWN','PHANTOMGUARD_WORKERS','PHANTOMGUARD_WARM_WORKERS','OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
        monkeypatch.delenv(key,raising=False)
    captured=[]
    monkeypatch.setattr('phantomguard.web.api.serve',lambda cfg,**limits:captured.append(limits))
    assert main(['serve','--root',str(tmp_path)])==0
    assert captured[0]['job_cooldown']==0 and captured[0]['workers']==2
    assert not captured[0]['warm_workers']
    assert 'OPENBLAS_NUM_THREADS' not in os.environ
