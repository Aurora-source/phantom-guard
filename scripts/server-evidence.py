"""Full emitted-stream/EOF-label equivalence probe, no learned artifact generation.

Run inside each image with the existing immutable workspace and bounded container
quota. stdout contains only seed/selection/hashes/counts/times, never tokens/paths.
"""
import hashlib,json,tempfile,time
from pathlib import Path
from phantomguard.config import load_config,load_baseline,raw_path
from phantomguard.io.replay import ReplaySource
from phantomguard.eval.splits import time_block,time_block_segments
from phantomguard.eval.attack_adapter import attack_source
try:
    from phantomguard.web.runtime import numerical_policy
    numerical_policy()
except ImportError:pass
cfg=load_config();baseline=load_baseline(cfg=cfg)
requests=[('onePersonMovingFrontAndBack.csv','T1','A2',11,'exact'),
 ('onePersonMovingFrontAndBack.csv','T1','A2',22,'exact'),
 ('multiplePeopleChaotic.csv','T2','A4',22,'exact'),
 ('multiplePeopleChaotic.csv','T3','A4',11,'translated'),
 ('onePersonMovingSideToSide.csv','T4','A4',33,'exact')]
for name,attack,level,seed,variant in requests:
    wall,cpu=time.perf_counter(),time.process_time()
    source=ReplaySource(raw_path(cfg,name));lo,hi=time_block(len(source.cycles),.6,.2)['test']
    source=ReplaySource(raw_path(cfg,name),(lo,hi))
    with tempfile.TemporaryDirectory(dir='/tmp') as directory:
        label=Path(directory)/'labels.csv'
        stream=attack_source(source,cfg,baseline,attack_type=attack,level=level,seed=seed,
            train_segments=time_block_segments(cfg)['train'],replay_provenance='training',
            replay_variant=variant,labels_path=label)
        digest=hashlib.sha256();count=0
        for frame in stream:
            digest.update(json.dumps([count,frame.can_id,frame.timestamp_ticks,frame.data.hex()],separators=(',',':')).encode()+b'\n')
            count+=1
        sidecars={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(directory).iterdir() if p.is_file()}
        # Lifecycle metadata contains local paths; compare labels CSV exactly,
        # plus sanitized JSON sidecars retaining all non-path scientific evidence.
        sanitized={}
        def clean(value):
            if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if not k.endswith('_path') and k not in {'labels_path'}}
            if isinstance(value,list):return [clean(v) for v in value]
            return value
        for p in Path(directory).glob('*.json'):
            sanitized[p.name]=hashlib.sha256(json.dumps(clean(json.loads(p.read_text())),sort_keys=True).encode()).hexdigest()
        print(json.dumps({'recording':name,'attack':attack,'level':level,'seed':seed,'variant':variant,
            'frames':count,'frames_sha256':digest.hexdigest(),'labels_sha256':hashlib.sha256(label.read_bytes()).hexdigest(),
            'sidecar_science_sha256':sanitized,'wall_seconds':time.perf_counter()-wall,'process_cpu_seconds':time.process_time()-cpu}),flush=True)
        try:
            from phantomguard.web.cache import freeze_pools
            from phantomguard.attack import pools
            freeze_pools(pools._cache)
        except ImportError:pass  # Original image has no runtime cache adapter.
