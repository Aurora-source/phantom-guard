import json, os, pathlib, subprocess, time, urllib.request, sys

out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
duration = float(sys.argv[2]) if len(sys.argv)>2 else 7200
def read(path):
    try: return pathlib.Path(path).read_text()
    except OSError: return ''
def kv(path):
    return {p[0]:int(p[1]) for l in read(path).splitlines() if len(p:=l.split())==2 and p[1].isdigit()}
def cg(pid):
    s=read(f'/proc/{pid}/cgroup').strip().split(':')
    return pathlib.Path('/sys/fs/cgroup'+s[-1]) if len(s)>1 else None
def pressure(root):
    return {x:read(str(root)+'/'+x+'.pressure') for x in ['cpu','memory','io']}
def group(root):
    if not root:return {}
    d={x:read(root/x).strip() for x in ['memory.current','memory.peak','memory.swap.current','pids.current','cpu.max','memory.max','io.stat']}
    d['process_count']=len(read(root/'cgroup.procs').splitlines())
    d.update(cpu=kv(root/'cpu.stat'),events=kv(root/'memory.events'),memory_stat=kv(root/'memory.stat'),pressure=pressure(root))
    return d
start=time.monotonic(); prev=None; containers=[]; refresh=0; thermal_history=[]; service_pids={}; output_usage={}
if (out/'samples.jsonl').exists():
    for line in (out/'samples.jsonl').read_text().splitlines()[-40:]:
        old=json.loads(line)
        if time.time()-old['time']<=180 and old.get('temperature_C'):
            thermal_history.append((start-(time.time()-old['time']),max(old['temperature_C'].values())))
end=start+duration
with (out/'samples.jsonl').open('a',buffering=1) as f:
    while time.monotonic()<end:
        tick=time.monotonic()
        if tick>=refresh:
            try:
                ids=subprocess.check_output(['docker','ps','-q'],text=True).split()
                containers=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True)) if ids else []
            except Exception: pass
            for name in ['cloudflared','qbittorrent-nox']:
                ids=subprocess.run(['pgrep','-x',name],capture_output=True,text=True).stdout.split()
                service_pids[name]=int(ids[0]) if ids else 0
            for c in containers:
                if c['Name']=='/phantomguard-server-prototype-1':
                    try:
                        code="import pathlib,json; p=pathlib.Path('/workspace/runs/browser'); f=[x for x in p.rglob('*') if x.is_file()]; print(json.dumps({'files':len(f),'bytes':sum(x.stat().st_size for x in f),'directories':len(list(p.iterdir()))}))"
                        output_usage=json.loads(subprocess.check_output(['docker','exec',c['Id'],'python','-c',code],text=True,stderr=subprocess.DEVNULL,timeout=3))
                    except Exception:pass
            refresh=tick+20
        stat=read('/proc/stat'); cpu=[int(x) for x in stat.splitlines()[0].split()[1:9]]
        vm=kv('/proc/vmstat'); mem={l.split(':')[0]:int(l.split()[1])*1024 for l in read('/proc/meminfo').splitlines()}
        d={'time':time.time(),'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'stage':read(out/'stage').strip() or 'baseline','elapsed':tick-start,'host_cpu_ticks':cpu,'memory':mem,'vm':{k:vm.get(k,0) for k in ['pswpin','pswpout','pgmajfault','oom_kill']},'load':read('/proc/loadavg').strip(),'host_pressure':{x:read('/proc/pressure/'+x) for x in ['cpu','memory','io']},'disks':[l for l in read('/proc/diskstats').splitlines() if not any(x in l.split()[2] for x in ['loop','ram'])],'containers':{}}
        if prev:
            dt=tick-prev[0]; delta=[a-b for a,b in zip(cpu,prev[1])]; total=sum(delta)
            d.update(host_cpu_pct=100*(total-delta[3]-delta[4])/total if total else 0,host_iowait_pct=100*delta[4]/total if total else 0,swap_in_Bps=(vm['pswpin']-prev[2]['pswpin'])*4096/dt,swap_out_Bps=(vm['pswpout']-prev[2]['pswpout'])*4096/dt)
        for c in containers:
            pid=c['State']['Pid']; name=c['Name'].lstrip('/')
            d['containers'][name]={'id':c['Id'],'pid':pid,'health':c['State'].get('Health',{}).get('Status'),'restarts':c['RestartCount'],'cgroup':group(cg(pid))}
        for name,pid in [('cloudflared',service_pids.get('cloudflared',0)),('qbittorrent',service_pids.get('qbittorrent-nox',0))]:
            d[name]={'stat':read(f'/proc/{pid}/stat'),'status':{l.split(':')[0]:l.split(':',1)[1].strip() for l in read(f'/proc/{pid}/status').splitlines() if l.startswith(('VmRSS:','VmHWM:','Threads:'))},'cgroup':group(cg(pid))}
        d['output_usage']=output_usage
        d['services']={}
        d['temperature_C']={}
        for hw in pathlib.Path('/sys/class/hwmon').glob('hwmon*'):
            if read(hw/'name').strip()=='coretemp':
                for sensor in hw.glob('temp*_input'):
                    d['temperature_C'][read(sensor.with_name(sensor.name.replace('_input','_label'))).strip()]=int(read(sensor))/1000
        d['thermal_throttle']={str(p):int(read(p) or 0) for p in pathlib.Path('/sys/devices/system/cpu').glob('cpu*/thermal_throttle/*_count')}
        thermal_history.append((tick,max(d['temperature_C'].values(),default=0)))
        thermal_history=[x for x in thermal_history if tick-x[0]<=180]
        d['temperature_180s_mean_C']=sum(x[1] for x in thermal_history)/len(thermal_history)
        thermal_sustained=len(thermal_history)>1 and tick-thermal_history[0][0]>=170 and d['temperature_180s_mean_C']>80
        for name,url in [('jellyfin','http://127.0.0.1:8096/health'),('stoat','http://127.0.0.1:8880/'),('sonarr','http://127.0.0.1:8989/ping'),('prowlarr','http://127.0.0.1:9696/ping'),('portfolio','http://127.0.0.1:4000/'),('nginx','http://127.0.0.1/')]:
            t=time.monotonic()
            try:
                with urllib.request.urlopen(url,timeout=2) as res: code=res.status;res.read(256)
            except urllib.error.HTTPError as e:code=e.code
            except Exception as e:code=type(e).__name__
            d['services'][name]={'code':code,'latency_ms':1000*(time.monotonic()-t)}
        if mem.get('MemAvailable',0)<2*1024**3 or d.get('swap_out_Bps',0)>10*1024**2 or (prev and vm.get('oom_kill',0)>prev[2].get('oom_kill',0)) or max(d['temperature_C'].values(),default=0)>=95 or thermal_sustained:
            (out/'pressure-alert').write_text(json.dumps({'time':d['utc'],'available':mem.get('MemAvailable'),'swap_out_Bps':d.get('swap_out_Bps'),'temperature_C':d['temperature_C'],'temperature_180s_mean_C':d['temperature_180s_mean_C']}))
        prev=(tick,cpu,vm)
        f.write(json.dumps(d)+'\n')
        time.sleep(max(0,5-(time.monotonic()-tick)))
