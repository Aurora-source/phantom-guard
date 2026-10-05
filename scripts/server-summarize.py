"""Summarize recorded /proc/cgroup samples and API workloads, with explicit units."""
import argparse, collections, json, pathlib, statistics
p=argparse.ArgumentParser();p.add_argument('--samples',required=True);p.add_argument('--workloads',nargs='+',required=True);p.add_argument('--out',required=True);a=p.parse_args()
stages=collections.defaultdict(list);previous=None
def devices(lines):
    return {v[2]:(int(v[5])*512,int(v[9])*512) for line in lines if len(v:=line.split())>9 and v[2] in ['sda','sdb','sdc','sdd','nvme0n1']}
with pathlib.Path(a.samples).open() as f:
 for line in f:
    r=json.loads(line);s={'time':r['time'],'host_cpu_pct':r.get('host_cpu_pct'),'iowait_pct':r.get('host_iowait_pct'),
       'available_GiB':r['memory']['MemAvailable']/2**30,'swap_used_MiB':(r['memory']['SwapTotal']-r['memory']['SwapFree'])/2**20,
       'temperature_C':max(r.get('temperature_C',{}).values(),default=None),'temperature_180s_mean_C':r.get('temperature_180s_mean_C'),
       'load1':float(r['load'].split()[0]),'runnable':int(r['load'].split()[3].split('/')[0]),'services':r['services'],
       'oom_kill':r['vm']['oom_kill'],'output_bytes':r.get('output_usage',{}).get('bytes',0),'containers':{},'cloudflared':{}}
    dt=r['time']-previous['time'] if previous else 0
    if previous and r['stage']==previous['stage'] and 0<dt<30:
       for key in ['swap_in_Bps','swap_out_Bps']:s[key.replace('_Bps','_MiB')]=r.get(key,0)*dt/2**20
       old,new=devices(previous['disks']),devices(r['disks']);s['read_MiBps']=sum(max(0,new[k][0]-old[k][0]) for k in new.keys()&old.keys())/dt/2**20;s['write_MiBps']=sum(max(0,new[k][1]-old[k][1]) for k in new.keys()&old.keys())/dt/2**20
    for name,c in r['containers'].items():
       g=c['cgroup']
       if not all(k in g for k in ['memory.current','cpu','events']): continue
       d={'memory_MiB':int(g.get('memory.current') or 0)/2**20,'peak_MiB':int(g.get('memory.peak') or 0)/2**20,'pids':int(g.get('pids.current') or 0),'processes':g.get('process_count'),'health':c['health'],'restarts':c['restarts'],'memory_events':g['events']}
       old=previous['containers'].get(name) if previous else None
       if old and 'cpu' in old['cgroup'] and c['id']==old['id'] and previous['stage']==r['stage'] and dt>0:
          d['cpu_pct_one_core']=100*(g['cpu']['usage_usec']-old['cgroup']['cpu']['usage_usec'])/dt/1e6
          d['throttled_seconds']=(g['cpu'].get('throttled_usec',0)-old['cgroup']['cpu'].get('throttled_usec',0))/1e6
       s['containers'][name]=d
    c=r.get('cloudflared',{});g=c.get('cgroup',{});old=previous.get('cloudflared',{}) if previous else {}
    if c.get('status'):
       s['cloudflared']['rss_MiB']=int(c['status'].get('VmRSS','0 kB').split()[0])/1024
       if old.get('stat') and c.get('stat') and old['stat'].split()[0]==c['stat'].split()[0] and previous['stage']==r['stage'] and dt>0:
          s['cloudflared']['cpu_pct_one_core']=100*(g['cpu']['usage_usec']-old['cgroup']['cpu']['usage_usec'])/dt/1e6
    stages[r['stage']].append(s);previous=r
def summary(values):
    v=[x for x in values if x is not None]
    return {'n':len(v),'mean':statistics.mean(v),'median':statistics.median(v),'min':min(v),'max':max(v)} if v else None
result={'stages':{},'workloads':{}}
for name,rows in stages.items():
    d={'n':len(rows),'span_seconds':rows[-1]['time']-rows[0]['time'],'metrics':{key:summary(r.get(key) for r in rows) for key in ['host_cpu_pct','iowait_pct','available_GiB','swap_used_MiB','temperature_C','temperature_180s_mean_C','load1','runnable','read_MiBps','write_MiBps','output_bytes']},'swap_in_MiB':sum(r.get('swap_in_MiB',0) for r in rows),'swap_out_MiB':sum(r.get('swap_out_MiB',0) for r in rows),'services':{},'containers':{},'cloudflared':{key:summary(r['cloudflared'].get(key) for r in rows) for key in ['rss_MiB','cpu_pct_one_core']}}
    for service in rows[0]['services']:
       d['services'][service]={'statuses':dict(collections.Counter(str(r['services'][service]['code']) for r in rows)),'latency_ms':summary(r['services'][service]['latency_ms'] for r in rows)}
    for container in {k for r in rows for k in r['containers']}:
       values=[r['containers'][container] for r in rows if container in r['containers']]
       if not values: continue
       d['containers'][container]={key:summary(v.get(key) for v in values) for key in ['cpu_pct_one_core','memory_MiB','peak_MiB','pids','processes']}
       d['containers'][container].update(throttled_seconds=sum(v.get('throttled_seconds',0) for v in values),restarts=[v['restarts'] for v in values][::max(1,len(values)-1)],health=dict(collections.Counter(str(v['health']) for v in values)),oom_kill_max=max(v['memory_events'].get('oom_kill',0) for v in values))
    result['stages'][name]=d
for file in a.workloads:
    rows=[json.loads(x) for x in pathlib.Path(file).read_text().splitlines()];jobs=[x for x in rows if x['kind']=='job'];api=[x for x in rows if x['kind']=='api']
    result['workloads'][pathlib.Path(file).stem]={'jobs':jobs,'job_states':dict(collections.Counter(x['state'] for x in jobs)),'status_counts':dict(collections.Counter(str(x['status']) for x in api)),
      'submit_to_completion_seconds':summary(x['end_to_end_seconds'] for x in jobs),'observed_queue_seconds':summary(x['queue_observed_seconds'] for x in jobs),
      'status_get_ms':summary(x['latency_ms'] for x in api if x['method']=='GET' and '/api/jobs/' in x['path'] and not x['path'].endswith('/result') and not x['path'].endswith('/heartbeat') and x['status']==200),
      'result_get_ms':summary(x['latency_ms'] for x in api if x['path'].endswith('/result'))}
pathlib.Path(a.out).write_text(json.dumps(result,indent=2))
print(json.dumps({'stages':{k:v['n'] for k,v in result['stages'].items()},'jobs':{k:v['job_states'] for k,v in result['workloads'].items()}}))
