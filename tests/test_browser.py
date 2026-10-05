"""API validation, isolation, limits, reset, and actual recorded replay workflows."""
from __future__ import annotations
import io
import json
import time
from copy import deepcopy
import pytest
from phantomguard.config import load_config,paths
from phantomguard.web.api import Application
from phantomguard.web.jobs import JobManager,RequestError,validate_request
from conftest import needs_data


def call(app,path,method='GET',body=None,token=None):
    raw=json.dumps(body).encode() if body is not None else b''
    e={'REQUEST_METHOD':method,'PATH_INFO':path,'CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw),'QUERY_STRING':''}
    if token:e['HTTP_AUTHORIZATION']='Bearer '+token
    response=[]
    payload=b''.join(app(e,lambda status,headers:response.append((status,headers))))
    return int(response[0][0].split()[0]),json.loads(payload)


@pytest.mark.parametrize('bad',[{'recording':'../emptyRoom.csv'},{'seed':-1},{'seed':True},{'seed':'11'},{'cycles':0},{'cycles':1201},{'attack':'T3','level':'A2'},{'attack':'T4','level':'A3','motion':'static'},{'attack':'T1'},{'ground_truth':True},{'motion':[]},{'level':{}}])
def test_bad_browser_selections(bad):
    with pytest.raises(RequestError):
        validate_request({'recording':'emptyRoom.csv',**bad},load_config())


def test_not_ready_is_visible_and_no_training_is_requested(tmp_path):
    cfg=load_config(root=tmp_path)
    app=Application(cfg,workers=1)
    try:
        assert call(app,'/healthz')[0]==200
        status,ready=call(app,'/readyz')
        assert status==503 and not ready['ok']
        assert 'torch' not in ready['artifacts']
        assert call(app,'/api/jobs','POST',{'recording':'emptyRoom.csv'})[0]==401
        token=call(app,'/api/sessions','POST')[1]['token']
        assert call(app,'/api/jobs','POST',{'recording':'emptyRoom.csv'},token)[0]==503
        assert call(app,'/api/train','POST',{},token)[0]==404
        catalog=call(app,'/api/catalog')[1]
        assert all(not c['supported'] for c in catalog['attacks'] if c['attack']=='T3' and c['level'] in ['A0','A1','A2'])
    finally:app.close()


def test_windows_snapshot_sharing_violation_is_retried(tmp_path,monkeypatch):
    from pathlib import Path
    from phantomguard.web.jobs import atomic_json
    original=Path.replace
    attempts=[]
    def busy(self,target):
        attempts.append(1)
        if len(attempts)<4:
            raise PermissionError('Windows polling handle still open')
        return original(self,target)
    monkeypatch.setattr(Path,'replace',busy)
    atomic_json(tmp_path/'progress.json',{'completed':25})
    assert len(attempts)==4
    assert json.loads((tmp_path/'progress.json').read_text())['completed']==25


def test_job_ownership_cancel_cleanup_and_capacity(tmp_path):
    cfg=load_config(root=tmp_path)
    manager=JobManager(cfg,workers=1,max_jobs=2,max_sessions=2)
    # Hold the manager lock so jobs cannot begin while queue/cancellation is tested.
    try:
        with manager.lock:
            a,b=manager.session(),manager.session()
            with pytest.raises(RequestError,match='Session capacity'):manager.session()
            job=manager.create(a,{'recording':'emptyRoom.csv'})
            with pytest.raises(RequestError,match='already has'):manager.create(a,{'recording':'emptyRoom.csv'})
            with pytest.raises(RequestError,match='not found'):manager.get(b,job['id'])
            directory=manager.get(a,job['id']).directory
            manager.cancel(a,job['id'],remove=True)
            assert not directory.exists()
            fresh=manager.create(a,{'recording':'emptyRoom.csv','seed':22})
            assert fresh['id']!=job['id']
            manager.cancel(a,fresh['id'],remove=True)
    finally:manager.close()


def test_configured_queue_bound(tmp_path):
    manager=JobManager(load_config(root=tmp_path),workers=1,max_queued=1)
    try:
        with manager.lock:
            a,b=manager.session(),manager.session()
            job=manager.create(a,{'recording':'emptyRoom.csv'})
            with pytest.raises(RequestError,match='capacity'):
                manager.create(b,{'recording':'emptyRoom.csv'})
            manager.cancel(a,job['id'],remove=True)
            assert manager.create(b,{'recording':'emptyRoom.csv'})['state']=='queued'
    finally:manager.close()


def test_result_preserves_bytes_and_private_ownership(tmp_path):
    app=Application(load_config(root=tmp_path),workers=1)
    try:
        with app.manager.lock:
            a,b=app.manager.session(),app.manager.session()
            created=app.manager.create(a,{'recording':'emptyRoom.csv'})
            job=app.manager.get(a,created['id'])
            original=b'{"cycles":[],"score":0.12345678901234567}\n'
            (job.directory/'result.json').write_bytes(original)
            job.state='complete'
            e={'REQUEST_METHOD':'GET','PATH_INFO':'/api/jobs/'+job.id+'/result','HTTP_AUTHORIZATION':'Bearer '+a}
            responses=[]
            assert b''.join(app(e,lambda status,headers:responses.append((status,dict(headers)))))==original
            assert responses[0][1]['Cache-Control']=='no-store'
            assert call(app,e['PATH_INFO'],token=b)[0]==404
            assert call(app,'/api/jobs/'+job.id,'DELETE',token=a)[0]==200
            assert call(app,e['PATH_INFO'],token=a)[0]==404
    finally:app.close()


def test_restart_orphans_expire_after_startup(tmp_path,monkeypatch):
    import os
    import uuid
    cfg=load_config(root=tmp_path)
    directory=paths(cfg).output/'browser'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    (directory/'progress.json').write_text('{"stage":"loading"}')
    manager=JobManager(cfg,workers=1,ttl=30)
    try:
        with manager.lock:
            assert directory.exists() # fresh crash output survives startup
            os.utime(directory,(time.time()-31,time.time()-31))
            clock=time.monotonic()
            monkeypatch.setattr(time,'monotonic',lambda:clock+31)
            manager._expire()
            assert not directory.exists()
    finally:manager.close()


def test_cooldown_queues_next_job(tmp_path,monkeypatch):
    manager=JobManager(load_config(root=tmp_path),workers=1,job_cooldown=30)
    started=[]
    class Process:
        def __init__(self,**kw):self.alive=False
        def start(self):self.alive=True;started.append(True)
        def is_alive(self):return self.alive
        def terminate(self):self.alive=False
        def join(self,timeout=None):pass
        def close(self):pass
    monkeypatch.setattr(manager.context,'Process',Process)
    try:
        with manager.lock:
            token=manager.session()
            manager._next_start=time.monotonic()+30
            jid=manager.create(token,{'recording':'emptyRoom.csv'})['id']
        time.sleep(.3)
        assert not started and manager.get(token,jid).state=='queued'
        with manager.lock:manager._next_start=0
        time.sleep(.3)
        assert started and manager.get(token,jid).state=='running'
        manager.cancel(token,jid,remove=True)
        assert manager._next_start>time.monotonic()+25
    finally:manager.close()


def wait_complete(app,token,jid,deadline=90):
    until=time.monotonic()+deadline
    while time.monotonic()<until:
        status,state=call(app,'/api/jobs/'+jid,token=token)
        assert status==200
        if state['state']=='complete':return call(app,'/api/jobs/'+jid+'/result',token=token)[1]
        assert state['state'] not in {'failed','timed_out'},state.get('error',state)
        time.sleep(.2)
    pytest.fail('Actual replay timed out')


@needs_data
def test_real_api_clean_attack_independent_sessions_and_reset(tmp_path):
    cfg=load_config(overrides={'output':tmp_path})
    app=Application(cfg,workers=2)
    try:
        a=call(app,'/api/sessions','POST')[1]['token'];b=call(app,'/api/sessions','POST')[1]['token']
        request={'recording':'onePersonMovingFrontAndBack.csv','cycles':150,'seed':11}
        clean=call(app,'/api/jobs','POST',request,a)[1]
        attack=call(app,'/api/jobs','POST',{**request,'attack':'T1','level':'A2'},b)[1]
        assert call(app,'/api/jobs/'+clean['id'],token=b)[0]==404
        clean_result=wait_complete(app,a,clean['id']);attack_result=wait_complete(app,b,attack['id'])
        assert len(clean_result['cycles'])==len(attack_result['cycles'])==150
        assert clean_result['provenance']['model_artifact_id']==attack_result['provenance']['model_artifact_id']
        assert all('is_attack' not in v for c in attack_result['cycles'] for v in c['objects'])
        labels=app.manager.get(b,attack['id']).directory/'labels.csv'
        assert labels.is_file()
        assert any(c['cycle_alert'] or any(v['flagged'] for v in c['objects']) for c in attack_result['cycles'])
        call(app,'/api/jobs/'+clean['id'],'DELETE',token=a)
        again=call(app,'/api/jobs','POST',request,a)[1]
        repeated=wait_complete(app,a,again['id'])
        def verdicts(result):
            return [(c['index'],c['cycle_reasons'],[(v['frame_index'],v['track_id'],v['reasons'],v['scores'],v['flagged'],v['alert']) for v in c['objects']]) for c in result['cycles']]
        assert verdicts(clean_result)==verdicts(repeated)
        assert call(app,'/api/evaluation')[0]==200
    finally:app.close()


@needs_data
@pytest.mark.parametrize("warm_workers", [False, True])
def test_actual_timeout_releases_worker(tmp_path,warm_workers):
    app=Application(load_config(overrides={'output':tmp_path}),workers=1,job_seconds=1,warm_workers=warm_workers)
    try:
        token=call(app,'/api/sessions','POST')[1]['token']
        job=call(app,'/api/jobs','POST',{'recording':'multiplePeopleChaotic.csv','attack':'T3','level':'A4','cycles':1100},token)[1]
        end=time.monotonic()+10
        while time.monotonic()<end:
            state=call(app,'/api/jobs/'+job['id'],token=token)[1]
            if state['state']=='timed_out':break
            time.sleep(.2)
        assert state['state']=='timed_out',state
        assert app.manager.get(token,job['id']).process is None
        call(app,'/api/jobs/'+job['id'],'DELETE',token=token)
    finally:app.close()
