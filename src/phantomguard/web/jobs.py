"""Bounded isolated spawned replay processes; each job owns all detector history."""
from __future__ import annotations
import json
import multiprocessing as mp
import os
import secrets
import re
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from phantomguard.config import bval, load_baseline, paths, raw_path
from phantomguard.eval.attack_adapter import ATTACK_TYPES, LEVEL_NAMES, support_reason


class RequestError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def validate_request(data, cfg, max_cycles=1200):
    if not isinstance(data, dict):
        raise RequestError('Expected a JSON object')
    unknown = set(data) - {'recording', 'attack', 'level', 'seed', 'cycles', 'motion', 'variant'}
    if unknown:
        raise RequestError('Unknown selections: '+', '.join(sorted(unknown)))
    rec = data.get('recording')
    if not isinstance(rec, str) or rec not in cfg['data']['files']:
        raise RequestError('Choose a server-approved recording identifier; file paths are not accepted')
    if any(k in data and data[k] is not None and not isinstance(data[k],str) for k in ('attack','level','motion','variant')):
        raise RequestError('attack, level, motion and variant must be string selections')
    out = {'recording': rec, 'attack': data.get('attack') or None, 'level': data.get('level') or None,
           'seed': data.get('seed', 11), 'cycles': data.get('cycles', min(600,max_cycles)),
           'motion': data.get('motion','moving'), 'variant': data.get('variant','exact')}
    if any(out[k] is not None and not isinstance(out[k],str) for k in ('attack','level','motion','variant')):
        raise RequestError('attack, level, motion and variant must be string selections')
    if type(out['seed']) is not int or not 0 <= out['seed'] <= 2**32-1:
        raise RequestError('seed must be an integer in 0..4294967295')
    if type(out['cycles']) is not int or not 1 <= out['cycles'] <= max_cycles:
        raise RequestError(f'cycles must be an integer in 1..{max_cycles}')
    if out['motion'] not in {'static','moving'} or out['variant'] not in {'exact','translated'}:
        raise RequestError('motion must be static/moving; variant must be exact/translated')
    if bool(out['attack']) != bool(out['level']):
        raise RequestError('attack and level must be supplied together')
    if out['attack']:
        reason = support_reason(out['attack'], out['level'], out['motion'])
        if reason:
            raise RequestError(reason)
    return out


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, allow_nan=False, separators=(',',':')),encoding='utf-8')
    # Windows can deny replacing a file while a polling reader briefly holds it
    # open. Keep complete old/new snapshots and retry that transient sharing race.
    for attempt in range(50):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt==49:
                raise
            time.sleep(.01)


def replay_worker(cfg, request, directory):
    """Labels stay in a sidecar. All display data comes from real CycleResults."""
    worker_entry_wall=time.monotonic()
    worker_entry_cpu=time.process_time()
    from phantomguard.web.runtime import numerical_policy
    numerical_policy()
    from phantomguard.detect.pipeline import Detector, load_artifacts
    from phantomguard.eval.attack_adapter import attack_source
    from phantomguard.eval.metrics import assembly_stats, latency_stats, score
    from phantomguard.eval.report import portable_config
    from phantomguard.eval.splits import time_block, time_block_segments
    from phantomguard.io.replay import ReplaySource
    from phantomguard.workspace import digest
    d = Path(directory)
    progress = d/'progress.json'
    started = time.monotonic()
    cpu_started = time.process_time()
    try:
        atomic_json(progress, {'stage':'loading','completed':0})
        baseline = load_baseline(cfg=cfg)
        ae, library = load_artifacts(strict=True,cfg=cfg,baseline=baseline)
        artifacts_loaded = time.monotonic()
        artifacts_cpu = time.process_time()
        source = ReplaySource(raw_path(cfg,request['recording']))
        bounds = time_block(len(source.cycles),cfg['splits']['train_frac'],cfg['splits']['val_frac'])
        lo,hi = bounds['test']
        if hi <= lo:
            raise ValueError('Recording has an empty time-block test segment')
        source = ReplaySource(raw_path(cfg,request['recording']),(lo,hi))
        data_loaded = time.monotonic()
        data_cpu = time.process_time()
        if request['attack']:
            atomic_json(progress, {'stage':'planning simulated attack','completed':0})
            source = attack_source(source,cfg,baseline,attack_type=request['attack'],level=request['level'],seed=request['seed'],
                train_segments=time_block_segments(cfg)['train'],replay_provenance='training',
                motion_case=request['motion'],replay_variant=request['variant'],labels_path=d/'labels.csv')
        if request['attack']:
            # Finalize the offline emitted-stream sidecars before playback. These
            # ordinary frames still enter a causal iterator one at a time; neither
            # the detector nor presentation reads attack labels.
            source = list(source)
        attack_planned = time.monotonic()
        planned_cpu = time.process_time()
        detector = Detector(cfg,baseline,ae,library)
        results, display = [], []
        # Finishing the ordinary attacker iterator writes its complete final-index
        # sidecar. Detector processing is bounded by requested cycles. No labels
        # are read here; attack metrics remain an offline evaluator responsibility.
        for r in detector.run(source):
            results.append(r)
            display.append({'index':r.index,'header_timestamp_ticks':r.header_t,'closed_timestamp_ticks':r.closed_t,
                'header_frame_index':r.header_frame_index,'cycle_reasons':r.cycle_reasons,'cycle_alert':r.cycle_alert,
                'objects':[asdict(v) for v in r.objects], 'layer_status':r.layer_status,
                'processing_ms':r.latency_ms,'assembly_delay_ticks':r.assembly_delay_ticks})
            if len(results)%25==0:
                atomic_json(progress,{'stage':'detecting','completed':len(results),'total':min(hi-lo,request['cycles'])})
            if len(results) >= request['cycles']:
                break
        if not results:
            raise ValueError('Empty replay; no cycles emitted')
        detected = time.monotonic()
        detected_cpu = time.process_time()
        # Clip labels are not used for scene colors or detector behavior.
        # If the source iterator stopped early its sidecar is explicitly partial;
        # this browser job is never presented as an attack-evaluation run.
        generated_labels = d/'labels.csv'
        labels_status = 'complete emitted-test-stream sidecar; never read for playback' if generated_labels.is_file() else 'not applicable (clean)'
        nominal = float(bval(baseline,'cadence_median'))
        output = {'request':request,'provenance':{'source':'recorded sensor replay','attack':'simulated CAN attack' if request['attack'] else 'no injected attack',
                  'part':'test','segment_lo':lo,'segment_hi':hi,'shown_cycles':len(results),'seed_rule':'browser uses the selected seed directly; matrix repetitions use SeedSequence',
                  'recording_sha256':digest(raw_path(cfg,request['recording'])), 'model_artifact_id':ae.metadata['artifact_id'],
                  'baseline_sha256':digest(paths(cfg).baseline),'configuration':portable_config(cfg),'labels':labels_status},
                  'roi':cfg['roi']['max_range'],'tick_seconds':cfg['units']['tick_seconds'],'period_seconds':nominal*cfg['units']['tick_seconds'],
                  'cycles':display,'processing':latency_stats(results),'assembly':assembly_stats(results,cfg),
                  'summary':{'alerting_cycles':sum(r.cycle_alert or any(v.alert for v in r.objects) for r in results),
                  'object_cycles':sum(len(r.objects) for r in results),'green_means':'not flagged; authenticity is not established'},
                  'elapsed_seconds':time.monotonic()-started}
        output['timings'] = {'process_cpu_seconds':time.process_time()-worker_entry_cpu,
            'worker_entry_to_result_seconds':time.monotonic()-worker_entry_wall,
            'module_import_wall_seconds':started-worker_entry_wall,
            'stage_process_cpu_seconds':{'artifact_loading':artifacts_cpu-cpu_started,'data_loading':data_cpu-artifacts_cpu,
                'planning_and_materialization':planned_cpu-data_cpu,'detect_and_display':detected_cpu-planned_cpu,
                'presentation_and_metrics':time.process_time()-detected_cpu},
            'legacy_cpu_fields_clock':'perf_counter wall intervals, including scheduling/quota delays','artifact_loading_seconds':artifacts_loaded-started,
            'data_loading_seconds':data_loaded-artifacts_loaded,
            'attacker_planning_seconds':attack_planned-data_loaded,
            'detect_and_display_seconds':detected-attack_planned,
            'detector_cpu_seconds':sum(r.detector_cpu_ms for r in results)/1000,
            'assembly_cpu_seconds':sum(r.assembly_cpu_ms for r in results)/1000,
            'presentation_and_metrics_seconds':time.monotonic()-detected}
        write_started = time.monotonic()
        atomic_json(d/'result.json',output)
        atomic_json(progress,{'stage':'complete','completed':len(results),
                             'result_write_seconds':time.monotonic()-write_started})
    except Exception as exc:
        atomic_json(d/'error.json',{'error':str(exc),'type':type(exc).__name__})


def warm_loop(connection,cfg):
    """Retain only parsed tuples and immutable existing training pools per identity."""
    from phantomguard.web.runtime import input_stamp, content_identity, numerical_policy
    numerical_policy()
    from phantomguard.io.replay import load_recorded_cycles
    from phantomguard.attack import pools
    from phantomguard.web.cache import freeze_pools, invalidate_input_caches
    previous=None
    identity=None
    while True:
        request,directory=connection.recv()
        stamp=input_stamp(cfg)
        if stamp!=previous:
            invalidate_input_caches()
            identity=content_identity(stamp)
            previous=stamp
        replay_worker(cfg,request,directory)
        cache_info=freeze_pools(pools._cache)
        progress=Path(directory)/'progress.json'
        if progress.is_file():
            snapshot=json.loads(progress.read_text())
            snapshot['runtime_cache']={'identity':identity,**cache_info,'parsed_recordings':load_recorded_cycles.cache_info().currsize}
            atomic_json(progress,snapshot)
        # No model, Detector, RNG, attacker, labels or output lists are retained.
        import gc
        gc.collect()
        connection.send('done')


class WarmProcess:
    """One scheduler-owned spawned worker; killed on cancel/deadline, recycled after 16 jobs.

    RSS is additionally bounded at 384 MiB where /proc is available. Docker's
    memory/PID limits remain enforced on all platforms. No automatic retries.
    """
    def __init__(self,context,cfg):
        self.connection,child=context.Pipe()
        self.process=context.Process(target=warm_loop,args=(child,cfg),daemon=True)
        self.child=child
        self.jobs=0
        self.done=False
        self.pending=None
        self.idle_since=0

    def assign(self,request,directory):
        self.pending=(request,directory)
        self.done=False

    def start(self):
        if self.process.pid is None:
            self.process.start()
            self.child.close()
        self.connection.send(self.pending)
        self.pending=None
        self.jobs+=1

    def is_alive(self):
        if not self.process.is_alive():
            return False
        if not self.done and self.connection.poll():
            try:
                self.done=self.connection.recv()=='done'
            except EOFError:
                return False
        return not self.done

    @property
    def exitcode(self):
        return 0 if self.done else self.process.exitcode

    def reusable(self):
        if not self.done or self.jobs>=16:
            return False
        try:
            status=Path(f'/proc/{self.process.pid}/status').read_text()
            rss=int(next(l for l in status.splitlines() if l.startswith('VmRSS:')).split()[1])*1024
            return rss<=384*1024**2
        except (OSError,StopIteration):
            return True

    def terminate(self): self.process.terminate()
    def kill(self): self.process.kill()
    def join(self,timeout=None): self.process.join(timeout)
    def close(self):
        self.connection.close()
        self.process.close()


@dataclass
class Job:
    id: str
    owner: str
    request: dict
    directory: Path
    created: float
    state: str = 'queued'
    process: object = None
    started: float = 0
    error: str = ''


class JobManager:
    def __init__(self,cfg,*,workers=2,max_cycles=1200,job_seconds=120,ttl=600,max_jobs=8,max_sessions=16,max_queued=4,output_mib=128,job_cooldown=0,warm_workers=False):
        if not 1<=workers<=4 or not 1<=max_cycles<=1200 or not 1<=job_seconds<=300:
            raise ValueError('workers 1..4, max_cycles 1..1200 and job_seconds 1..300 required')
        if not 1<=max_queued<=4 or not 1<=max_jobs<=8 or not 1<=max_sessions<=16 or not 30<=ttl<=600 or not 8<=output_mib<=128:
            raise ValueError('max_queued 1..4, max_jobs 1..8, max_sessions 1..16, ttl 30..600 and output_mib 8..128 required')
        if not 0<=job_cooldown<=60:
            raise ValueError('job_cooldown 0..60 seconds required')
        self.cfg,self.workers,self.max_cycles,self.job_seconds = cfg,workers,max_cycles,job_seconds
        self.ttl,self.max_jobs,self.max_sessions = ttl,max_jobs,max_sessions
        self.max_queued,self.output_mib = max_queued,output_mib
        self.job_cooldown,self._next_start = job_cooldown,0
        self.directory=paths(cfg).output/'browser'
        self.directory.mkdir(parents=True,exist_ok=True)
        # Reclaim only expired directories created by this scheduler after a
        # previous process crash. Never follow links or touch unrelated outputs.
        for child in self.directory.iterdir():
            if (re.fullmatch(r'[0-9a-f]{32}',child.name) and child.is_dir()
                and not child.is_symlink() and child.resolve().parent==self.directory.resolve()
                and time.time()-child.stat().st_mtime>ttl):
                shutil.rmtree(child)
        self.jobs,self.sessions={},{}
        self._orphan_scan_at=0
        self.lock=threading.RLock()
        self.stop=threading.Event()
        self.warm_workers=warm_workers
        self._idle=[]
        self.context=mp.get_context('spawn')
        self.thread=threading.Thread(target=self._monitor,daemon=True)
        self.thread.start()

    def session(self):
        with self.lock:
            self._expire()
            if len(self.sessions)>=self.max_sessions:
                raise RequestError('Session capacity reached; retry after idle sessions expire',429)
            token=secrets.token_hex(32)
            self.sessions[token]=time.monotonic()
            return token

    def authenticate(self,token):
        if token not in self.sessions:
            raise RequestError('Session expired or unknown; create a new session',401)
        self.sessions[token]=time.monotonic()

    def create(self,token,request):
        selection=validate_request(request,self.cfg,self.max_cycles)
        with self.lock:
            self.authenticate(token)
            self._expire()
            if any(j.owner==token and j.state in {'queued','running'} for j in self.jobs.values()):
                raise RequestError('This session already has a job; cancel/reset before restarting',409)
            if len(self.jobs)>=self.max_jobs or sum(j.state=='queued' for j in self.jobs.values())>=self.max_queued:
                raise RequestError('Replay queue/storage capacity reached; reset completed jobs or retry later',429)
            if sum(p.stat().st_size for p in self.directory.rglob('*') if p.is_file())>self.output_mib*1024*1024:
                raise RequestError('Browser output quota reached; reset jobs or wait for cleanup',429)
            jid=uuid.uuid4().hex
            directory=self.directory/jid
            directory.mkdir()
            job=Job(jid,token,selection,directory,time.monotonic())
            self.jobs[jid]=job
            return self.snapshot(job)

    def get(self,token,jid):
        with self.lock:
            self.authenticate(token)
            job=self.jobs.get(jid)
            if job is None or job.owner!=token:
                raise RequestError('Job not found in this session',404)
            return job

    def snapshot(self,job):
        progress={}
        try:
            progress=json.loads((job.directory/'progress.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):
            pass
        return {'runtime':{'queue_seconds':(job.started or time.monotonic())-job.created,'cooldown_remaining_seconds':max(0,self._next_start-time.monotonic()) if job.state=='queued' else 0},'id':job.id,'state':job.state,'request':job.request,'progress':progress,'error':job.error}

    def cancel(self,token,jid,*,remove=False):
        with self.lock:
            job=self.get(token,jid)
            self._terminate(job)
            job.state='cancelled'
            result=self.snapshot(job)
            if remove:
                self._remove(job)
            return result

    def _terminate(self,job):
        if job.process is not None:
            if job.state=='running' and self.job_cooldown:
                self._next_start=time.monotonic()+self.job_cooldown
            actual=job.process.process if isinstance(job.process,WarmProcess) else job.process
            if actual.is_alive():
                job.process.terminate()
            job.process.join(timeout=3)
            if actual.is_alive():
                job.process.kill()
                job.process.join(timeout=3)
            job.process.close()
            job.process=None

    def _remove(self,job):
        self._terminate(job)
        if job.directory.parent.resolve()!=self.directory.resolve() or job.directory.name!=job.id:
            raise RuntimeError('Job cleanup target outside managed directory')
        shutil.rmtree(job.directory)
        self.jobs.pop(job.id,None)

    def _expire(self):
        now=time.monotonic()
        if now>=self._orphan_scan_at:
            self._orphan_scan_at=now+30
            # A crash can leave young directories that were not expired when
            # the new scheduler started. Revisit those after their TTL, while
            # protecting every job owned by the current scheduler and all links.
            for child in self.directory.iterdir():
                if (child.name not in self.jobs and re.fullmatch(r'[0-9a-f]{32}',child.name)
                    and child.is_dir() and not child.is_symlink()
                    and child.resolve().parent==self.directory.resolve()
                    and time.time()-child.stat().st_mtime>self.ttl):
                    shutil.rmtree(child)
        for token,seen in list(self.sessions.items()):
            if now-seen>self.ttl:
                for job in list(self.jobs.values()):
                    if job.owner==token:
                        self._remove(job)
                self.sessions.pop(token,None)
        for job in list(self.jobs.values()):
            if job.state not in {'running','queued'} and now-job.created>self.ttl:
                self._remove(job)

    def _monitor(self):
        while not self.stop.wait(.2):
            with self.lock:
                self._expire()
                now=time.monotonic()
                for worker in list(self._idle):
                    if not worker.process.is_alive() or now-worker.idle_since>self.ttl:
                        if worker.process.is_alive():worker.terminate()
                        worker.join(timeout=3)
                        if worker.process.is_alive():
                            worker.kill();worker.join(timeout=3)
                        worker.close()
                        self._idle.remove(worker)
                for job in self.jobs.values():
                    if job.state=='running':
                        if now-job.started>self.job_seconds:
                            self._terminate(job)
                            job.state,job.error='timed_out',f'Processing exceeded {self.job_seconds}s; shorten the clip or reduce concurrency'
                        elif not job.process.is_alive():
                            code=job.process.exitcode
                            if isinstance(job.process, WarmProcess) and job.process.process.is_alive() and job.process.reusable():
                                job.process.idle_since=now
                                self._idle.append(job.process)
                                job.process=None
                                self._next_start=now+self.job_cooldown
                            else:
                                self._terminate(job)
                            if (job.directory/'result.json').is_file():
                                job.state='complete'
                            else:
                                job.state='failed'
                                try:
                                    job.error=json.loads((job.directory/'error.json').read_text())['error']
                                except (OSError,ValueError):
                                    job.error=f'Replay worker exited with code {code}'
                    elif job.state=='queued' and now-job.created>self.job_seconds*2:
                        job.state,job.error='timed_out','Queue wait expired; retry later'
                running=sum(j.state=='running' for j in self.jobs.values())
                for job in self.jobs.values():
                    if job.state=='queued' and running<self.workers and now>=self._next_start:
                        if self.warm_workers:
                            job.process=self._idle.pop() if self._idle else WarmProcess(self.context,self.cfg)
                            job.process.assign(job.request,str(job.directory))
                        else:
                            job.process=self.context.Process(target=replay_worker,args=(self.cfg,job.request,str(job.directory)),daemon=True)
                        try:
                            job.process.start()
                            job.started=time.monotonic()
                            job.state='running'
                            running+=1
                        except OSError as exc:
                            self._terminate(job)
                            job.process=None
                            job.state,job.error='failed',f'Cannot start replay worker: {exc}; reduce concurrency/resources'

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        with self.lock:
            for job in list(self.jobs.values()):
                self._remove(job)
            for worker in self._idle:
                worker.terminate()
                worker.join(timeout=3)
                if worker.process.is_alive():
                    worker.kill()
                    worker.join(timeout=3)
                worker.close()
            self._idle.clear()
