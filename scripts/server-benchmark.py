"""Bounded real API workloads. Raw output omits session tokens."""
import argparse, concurrent.futures, hashlib, json, pathlib, time, urllib.request, urllib.error
p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8765');p.add_argument('--out',required=True);p.add_argument('--label',required=True);p.add_argument('--seconds',type=int,default=120);p.add_argument('--poll',type=float,default=.5);p.add_argument('--samples',required=True);a=p.parse_args()
out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
stagefile=pathlib.Path(a.samples)/'stage'
f=(out/(a.label+'-workload.jsonl')).open('a',buffering=1)
def log(d):f.write(json.dumps({'time':time.time(),**d})+'\n')
def api(path,method='GET',body=None,token=None):
    headers={};raw=None
    if token:headers['Authorization']='Bearer '+token
    if body is not None:headers['Content-Type']='application/json';raw=json.dumps(body).encode()
    t=time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(a.base+path,data=raw,headers=headers,method=method),timeout=20) as r: status=r.status;payload=r.read();h=dict(r.headers)
    except urllib.error.HTTPError as e:status=e.code;payload=e.read();h=dict(e.headers)
    log({'kind':'api','stage':stagefile.read_text().strip(),'path':path,'method':method,'status':status,'latency_ms':1000*(time.monotonic()-t),'bytes':len(payload),'cache_control':h.get('Cache-Control'),'cf_cache_status':h.get('CF-Cache-Status')})
    return status,json.loads(payload)
def stage(name):stagefile.write_text(a.label+'-'+name);log({'kind':'stage','name':a.label+'-'+name})
heartbeat_at=0
def safety():
    global heartbeat_at
    if (pathlib.Path(a.samples)/'pressure-alert').exists():raise RuntimeError('Host pressure reserve breached; stop Phantom Guard workload')
    if time.monotonic()>heartbeat_at:
        for token in tokens:api('/api/jobs/heartbeat','GET',token=token)
        heartbeat_at=time.monotonic()+30
requests=[{'recording':'onePersonMovingFrontAndBack.csv','cycles':600,'seed':11},
 {'recording':'onePersonMovingFrontAndBack.csv','cycles':600,'seed':11,'attack':'T1','level':'A2'},
 {'recording':'multiplePeopleChaotic.csv','cycles':600,'seed':22,'attack':'T2','level':'A4'},
 {'recording':'multiplePeopleChaotic.csv','cycles':1000,'seed':11,'attack':'T3','level':'A4','variant':'translated'},
 {'recording':'onePersonMovingSideToSide.csv','cycles':900,'seed':33,'attack':'T4','level':'A4'}]
tokens=[];active={}
def create(i,req):
    t=time.monotonic();status,d=api('/api/jobs','POST',req,tokens[i])
    if status==202:active[d['id']]={'client':i,'submitted':t,'first_running':None,'request':req};return d['id']
    log({'kind':'rejection','client':i,'status':status,'error':d});return None
def delete(jid):
    i=active[jid]['client'];api('/api/jobs/'+jid,'DELETE',token=tokens[i]);active.pop(jid,None)
def poll(jid):
    meta=active[jid];i=meta['client'];status,s=api('/api/jobs/'+jid,token=tokens[i]);now=time.monotonic()
    if status!=200:raise RuntimeError(s)
    if s['state']=='running' and meta['first_running'] is None:meta['first_running']=now
    if s['state'] in ['complete','failed','timed_out','cancelled']:
        d={};digest=None
        if s['state']=='complete':
            _,d=api('/api/jobs/'+jid+'/result',token=tokens[i])
            canonical=[{k:v for k,v in c.items() if k!='processing_ms'} for c in d['cycles']]
            digest=hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            (out/(a.label+'-'+hashlib.sha256(json.dumps(meta['request'],sort_keys=True).encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(d))
        log({'kind':'job','stage':stagefile.read_text().strip(),'client':i,'request':meta['request'],'state':s['state'],'error':s.get('error'),'end_to_end_seconds':now-meta['submitted'],'queue_observed_seconds':(meta['first_running']-meta['submitted']) if meta['first_running'] else None,'worker_elapsed_seconds':d.get('elapsed_seconds'),'processing':d.get('processing'),'assembly':d.get('assembly'),'timings':d.get('timings'),'summary':d.get('summary'),'verdict_sha256':digest})
        delete(jid);return True
    return False
try:
    catalog=api('/api/catalog')[1];log({'kind':'catalog','catalog':catalog})
    for _ in range(6):
        status,d=api('/api/sessions','POST');assert status==201,d;tokens.append(d['token'])
    stage('idle');end=time.monotonic()+a.seconds;n=0
    while time.monotonic()<end:
        safety();api('/healthz');api('/readyz')
        if n%6==0:api('/api/evaluation');api('/api/catalog')
        n+=1;time.sleep(5)
    stage('one-client');end=time.monotonic()+a.seconds;index=0
    while time.monotonic()<end or index<5:
        safety();req=requests[index%len(requests)];jid=create(0,req);assert jid
        assert api('/api/jobs/'+jid,token=tokens[1])[0]==404
        while not poll(jid):safety();time.sleep(a.poll)
        index+=1
    stage('two-clients');end=time.monotonic()+a.seconds;index=0
    while time.monotonic()<end:
        safety();jids=[create(i,requests[(index+i)%5]) for i in range(2)]
        while any(j in active for j in jids):
            safety()
            for j in jids:
                if j in active:poll(j)
            time.sleep(a.poll)
        index+=2
    stage('four-client-burst');end=time.monotonic()+a.seconds;index=0
    while time.monotonic()<end:
        safety();first=create(0,requests[3]);assert first
        # Start one expensive worker before the burst so the queue cap is tested
        # independently of the deliberate rest between jobs.
        while active[first]['first_running'] is None:
            assert not poll(first);safety();time.sleep(a.poll)
        jids=[first]+[create(i,requests[(index+i)%5]) for i in range(1,4)]
        api('/api/jobs','POST',requests[0],tokens[0]) # one-job/session conflict
        for i in [4,5]:create(i,requests[3]) # bounded queue overload
        for j in list(active):delete(j) # both running and queued jobs
        fresh=create(0,requests[0]);assert fresh and fresh!=first
        while not poll(fresh):safety();time.sleep(a.poll)
        index+=4
    stage('recovery');end=time.monotonic()+a.seconds
    while time.monotonic()<end:api('/healthz');api('/readyz');time.sleep(5)
finally:
    for j in list(active):
        try:delete(j)
        except Exception:pass
    f.close()
