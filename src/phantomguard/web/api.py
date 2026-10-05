"""Small WSGI API served by Waitress. No arbitrary files or training endpoints."""
from __future__ import annotations
import csv
import json
import mimetypes
import threading
import time
from importlib.resources import files
from urllib.parse import parse_qs
from pathlib import Path

from phantomguard.config import paths
from phantomguard.eval.attack_adapter import ATTACK_TYPES, LEVEL_NAMES, support_reason
from phantomguard.web.jobs import JobManager, RequestError
from phantomguard.workspace import doctor
from phantomguard.web.runtime import input_stamp, file_stamp, content_identity


class Application:
    def __init__(self,cfg,**limits):
        self.cfg=cfg
        self.manager=JobManager(cfg,**limits)
        self._ready=None
        self._ready_at=0
        self._lock=threading.Lock()
        self._stamp=None
        self._config_stamp=file_stamp(paths(cfg).config)
        self._reports={}
        self._report_lock=threading.Lock()

    def readiness(self,force=False):
        with self._lock:
            stamp=input_stamp(self.cfg)
            if file_stamp(paths(self.cfg).config)!=self._config_stamp:
                return {'ok':False,'errors':['Configuration changed; restart to load and verify the new configuration']}
            if force or stamp!=self._stamp or self._ready is None or time.monotonic()-self._ready_at>300:
                started, cpu=time.perf_counter(),time.process_time()
                from phantomguard.web.cache import invalidate_input_caches
                invalidate_input_caches()
                self._ready=doctor(self.cfg)
                if self._ready['ok']:
                    try:
                        self._ready['runtime_identity']=content_identity(stamp)
                    except (OSError,RuntimeError) as exc:
                        self._ready['ok']=False
                        self._ready['errors'].append(str(exc))
                self._ready['verification_wall_seconds']=time.perf_counter()-started
                self._ready['verification_process_cpu_seconds']=time.process_time()-cpu
                self._ready_at=time.monotonic()
                self._stamp=stamp
            return self._ready

    def __call__(self,environ,start_response):
        try:
            status,data,content_type=self.route(environ)
        except RequestError as exc:
            status,data,content_type=exc.status,{'error':str(exc)},'application/json'
        except (OSError,ValueError,KeyError,TypeError) as exc:
            status,data,content_type=503,{'error':str(exc),'next':'Run phantomguard doctor; restore compatible data/artifacts'},'application/json'
        if content_type=='application/json' and not isinstance(data,bytes):
            body=json.dumps(data,allow_nan=False).encode('utf-8')
        else:
            body=data
        reasons={200:'OK',201:'Created',202:'Accepted',400:'Bad Request',401:'Unauthorized',404:'Not Found',409:'Conflict',413:'Payload Too Large',429:'Too Many Requests',503:'Service Unavailable'}
        start_response(f'{status} {reasons[status]}',[('Content-Type',content_type+'; charset=utf-8'),('Content-Length',str(len(body))),
            ('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
            ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")])
        return [body]

    def route(self,e):
        method,path=e['REQUEST_METHOD'],e.get('PATH_INFO','/')
        if method=='GET' and path in {'/','/app.js','/style.css'}:
            name='index.html' if path=='/' else path[1:]
            return 200,files('phantomguard.web').joinpath('static',name).read_bytes(),{'index.html':'text/html','app.js':'application/javascript','style.css':'text/css'}[name]
        if method=='GET' and path.startswith('/assets/'):
            relative=path.removeprefix('/assets/')
            parts=relative.split('/')
            if not relative or any(x in {'','.', '..'} for x in parts) or '\\' in relative:
                raise RequestError('Asset not found',404)
            root=Path(str(files('phantomguard.web').joinpath('static','assets'))).resolve()
            candidate=root.joinpath(*parts)
            if any(root.joinpath(*parts[:i]).is_symlink() for i in range(1,len(parts)+1)) or not candidate.resolve().is_relative_to(root) or not candidate.is_file():
                raise RequestError('Asset not found',404)
            mime=mimetypes.guess_type(candidate.name)[0] or 'application/octet-stream'
            return 200,candidate.read_bytes(),mime
        if method=='GET' and path=='/healthz':
            return 200,{'alive':True,'runtime':'CPU recorded-data prototype'},'application/json'
        if method=='GET' and path=='/readyz':
            state=self.readiness()
            return (200 if state['ok'] else 503),state,'application/json'
        if method=='GET' and path=='/api/catalog':
            options=[{'attack':t,'level':l,'supported':support_reason(t,l) is None,'reason':support_reason(t,l)} for t in ATTACK_TYPES for l in LEVEL_NAMES]
            return 200,{'recordings':self.cfg['data']['files'],'attacks':options,'max_cycles':self.manager.max_cycles,
                        'provenance':'Recorded sensor replay; simulated CAN attacks; green = not flagged',
                        'seek':'Playback seeks reuse immutable outputs computed causally from segment start; reset creates a fresh run'},'application/json'
        if method=='GET' and path in {'/api/evaluation','/api/evaluation/runs'}:
            return 200,self.evaluation(e,path.endswith('/runs')),'application/json'
        if method=='POST' and path=='/api/sessions':
            return 201,{'token':self.manager.session()},'application/json'
        auth=e.get('HTTP_AUTHORIZATION','')
        token=auth.removeprefix('Bearer ') if auth.startswith('Bearer ') else ''
        if method=='POST' and path=='/api/jobs':
            with self.manager.lock:
                self.manager.authenticate(token)
            if not self.readiness()['ok']:
                raise RequestError('Data/artifacts are unavailable or incompatible; inspect /readyz and run phantomguard doctor',503)
            try:
                size=int(e.get('CONTENT_LENGTH') or 0)
                if not 0<size<=2048:
                    raise RequestError('JSON request body required, maximum 2048 bytes',413)
                data=json.loads(e['wsgi.input'].read(size))
            except (ValueError,TypeError) as exc:
                raise RequestError('Malformed JSON request') from exc
            return 202,self.manager.create(token,data),'application/json'
        parts=path.strip('/').split('/')
        if len(parts) in {3,4} and parts[:2]==['api','jobs']:
            jid=parts[2]
            with self.manager.lock:
                job=self.manager.get(token,jid)
                if method=='DELETE' and len(parts)==3:
                    return 200,self.manager.cancel(token,jid,remove=True),'application/json'
                if method=='GET' and len(parts)==3:
                    return 200,self.manager.snapshot(job),'application/json'
                if method=='GET' and len(parts)==4 and parts[3]=='result':
                    if job.state!='complete':
                        raise RequestError(f'Result unavailable: {job.state}; {job.error}',409)
                    # Workers write finite, complete JSON atomically. Preserve those
                    # bytes instead of allocating and encoding the entire clip again.
                    return 200,(job.directory/'result.json').read_bytes(),'application/json'
        raise RequestError('Unknown endpoint or method',404)

    def evaluation(self,e,individual=False):
        p=paths(self.cfg).reports
        names=('attack_eval_manifest.json','attack_eval_runs.csv','attack_eval_layers.csv','attack_eval_clean.csv')
        stamp=tuple((name,file_stamp(p/name)) for name in names)
        key=(stamp,individual,e.get('QUERY_STRING',''))
        with self._report_lock:
            if key in self._reports:
                return self._reports[key]
            result=self._read_evaluation(e,individual)
            # Bound serialized cache to 8 MiB and 16 filter variants, discard old versions.
            if len(self._reports)>=16 or any(k[0]!=stamp for k in self._reports):
                self._reports.clear()
            size=len(json.dumps(result).encode())
            if size<=512*1024:
                self._reports[key]=result
            return result

    def _read_evaluation(self,e,individual=False):
        p=paths(self.cfg).reports
        manifest_path=p/'attack_eval_manifest.json'
        if not manifest_path.is_file():
            raise RequestError('Generated evaluation manifest missing; run attack-eval offline and select reports into docs/results',503)
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        provenance={k:manifest.get(k) for k in ('git_head','generated_utc','configuration_sha256','completed_attack_runs','run_status_counts','model_selection','historical_test_disclosure')}
        provenance['recordings']=[{k:r.get(k) for k in ('file','sha256')} for r in manifest.get('recordings',[])]
        provenance['artifact_ids']=[r.get('details',{}).get('metadata',{}).get('artifact_id') for r in manifest.get('artifacts',[])]
        params=parse_qs(e.get('QUERY_STRING',''))
        allowed={'recording','attack','level','seed'}
        if set(params)-allowed:
            raise RequestError('Unknown evaluation filters')
        rows=[]
        filename='attack_eval_runs.csv' if individual else 'attack_eval_layers.csv'
        with (p/filename).open(newline='',encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if individual:
                    mapping={'recording':'file','attack':'attack_type','level':'level','seed':'configured_seed'}
                    if any(row.get(mapping[k])!=v[0] for k,v in params.items()):
                        continue
                if row.get('layers')=='all':
                    # Do not publish absolute developer sidecar paths.
                    row.pop('labels_path',None)
                    rows.append(row)
                if len(rows)>=100:
                    break
        clean=[]
        with (p/'attack_eval_clean.csv').open(newline='',encoding='utf-8') as f:
            clean=[r for r in csv.DictReader(f) if r.get('layers')=='all']
        return {'provenance':provenance,'rows':rows,'clean':clean,'row_limit':100,
                'note':'Actual generated measurements; scene alarms and exact forged-object identification are distinct. These are matrix runs, not the current browser clip.'}

    def close(self):
        self.manager.close()


def serve(cfg,*,host='127.0.0.1',port=8765,**limits):
    if not 1<=port<=65535:
        raise ValueError('port must be 1..65535')
    from waitress import serve as waitress_serve
    app=Application(cfg,**limits)
    ready=app.readiness()
    print(f"Startup artifact verification: {'ready' if ready['ok'] else 'not ready; inspect /readyz'}",flush=True)
    print(f'Phantom Guard: http://{host}:{port}; readiness /readyz; recorded replay / simulated attacks',flush=True)
    try:
        waitress_serve(app,host=host,port=port,threads=8,connection_limit=32,channel_timeout=30,
                       max_request_body_size=2048,ident='PhantomGuard')
    finally:
        app.close()
