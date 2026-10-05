"""Real browser checks against a deployed service; never log session tokens."""
import argparse, json, pathlib, time, re
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--out',required=True)
p.add_argument('--executable');p.add_argument('--host-ip');p.add_argument('--samples');a=p.parse_args()
out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
checks=[];errors=[];responses=[]
def guard():
    if a.samples and (pathlib.Path(a.samples)/'pressure-alert').exists():
        raise RuntimeError('Host reserve/temperature guard breached')
def start_run(page):
    with page.expect_response(lambda r:r.url.endswith('/api/jobs') and r.request.method=='POST' and r.status==202,timeout=30000):
        page.locator('#run').click()
    expect(page.locator('#progress')).to_contain_text(re.compile(r'[Qq]ueued|running'),timeout=30000)

def wait_done(page):
    end=time.monotonic()+180
    while time.monotonic()<end:
        guard();progress=page.locator('#progress').inner_text()
        if progress.startswith('Complete'):return progress
        if any(x in progress for x in ['exceeded','unavailable','expired','failed']):raise RuntimeError(progress)
        time.sleep(1)
    raise RuntimeError('Browser replay did not complete within 180s')
with sync_playwright() as pw:
    args=['--host-resolver-rules=MAP '+urlsplit(a.base).hostname+' '+a.host_ip] if a.host_ip else []
    browser=pw.chromium.launch(headless=True,executable_path=a.executable,args=args)
    contexts=[browser.new_context(viewport={'width':1440,'height':1000}) for _ in range(2)]
    pages=[c.new_page() for c in contexts]
    try:
        for page in pages:
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('response',lambda r:responses.append({'url':r.url,'status':r.status,
                'cache_control':r.headers.get('cache-control'),'cf_cache_status':r.headers.get('cf-cache-status')}))
            page.goto(a.base);expect(page.locator('#ready')).to_contain_text('pipeline ready',timeout=60000)
            page.locator('#cycles').fill('150')
        first,second=pages
        first.locator('#attack').select_option('T1');first.locator('#level').select_option('A2');start_run(first)
        expect(first.locator('#progress')).to_contain_text('running',timeout=180000)
        start_run(second);expect(second.locator('#progress')).to_contain_text('queued',timeout=30000)
        checks.append({'independent_sessions':first.evaluate('()=>token')!=second.evaluate('()=>token'),
            'second_client_queued':True})
        jid=first.evaluate('()=>job')
        status=second.evaluate("async jid=>(await fetch('/api/jobs/'+jid,{headers:{Authorization:'Bearer '+token}})).status",jid)
        assert status==404;checks.append({'cross_session_status':status})
        second.locator('#cancel').click();expect(second.locator('#progress')).to_contain_text('Reset complete',timeout=15000)
        first.locator('#cancel').click();expect(first.locator('#progress')).to_contain_text('Reset complete',timeout=15000)
        checks.append({'queued_and_running_cancel':True})
        expect(first.locator('#run')).to_be_enabled(timeout=15000)
        first.locator('#attack').select_option('');start_run(first);done=wait_done(first)
        clean=first.evaluate('()=>({request:result.request,summary:result.summary,provenance:result.provenance,cycles:result.cycles.length})')
        assert clean['request']['attack'] is None and clean['cycles']==150
        first.locator('#step').click();assert first.locator('#clock').inner_text().startswith('2/150')
        first.locator('#seek').evaluate("el=>{el.value=149;el.dispatchEvent(new Event('input'))}")
        assert first.locator('#clock').inner_text().startswith('150/150')
        checks.append({'clean':clean,'step_and_seek':True,'progress':done})
        first.locator('#attack').select_option('T3');assert first.locator('#level').input_value()=='A3'
        assert first.locator('#level option[value=A2]').is_disabled()
        first.locator('#attack').select_option('T1');first.locator('#level').select_option('A2');start_run(first)
        done=wait_done(first);attacked=first.evaluate('()=>({request:result.request,summary:result.summary,provenance:result.provenance,cycles:result.cycles.length})')
        assert attacked['request']['attack']=='T1' and attacked['request']['level']=='A2' and attacked['cycles']==150
        assert attacked['provenance']['model_artifact_id']==clean['provenance']['model_artifact_id']
        first.locator('#play').click();time.sleep(1);assert not first.locator('#clock').inner_text().startswith('1/')
        first.locator('#play').click();first.screenshot(path=str(out/'browser.png'))
        checks.append({'attack':attacked,'playback':True,'unsupported_choices_disabled':True,'progress':done})
        # Private payloads must remain uncacheable at the origin and Cloudflare.
        private=[r for r in responses if '/api/jobs' in r['url'] and r['status']==200]
        assert private and all(r['cache_control']=='no-store' and r['cf_cache_status']!='HIT' for r in private)
        assert not errors,errors
        (out/'browser.json').write_text(json.dumps({'url':a.base,'browser':browser.version,'checks':checks,
            'page_errors':errors,'responses':responses},indent=2))
        print(json.dumps({'url':a.base,'checks':len(checks),'page_errors':errors,'private_responses':len(private)}))
    finally:
        (out/'browser-events.json').write_text(json.dumps({'url':a.base,'checks':checks,'page_errors':errors,'responses':responses},indent=2))
        for page in pages:
            try:page.evaluate('async()=>{await reset();return job===null}')
            except Exception:pass
        browser.close()
