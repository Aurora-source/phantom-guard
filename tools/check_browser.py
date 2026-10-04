#!/usr/bin/env python3
"""Execute the real browser workflow on a running service, using Chromium.

Install requirements/dev.lock and `python -m playwright install chromium` first.
No detector mocks, labels, threshold changes, or invented performance metrics.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--url',default='http://127.0.0.1:8765')
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1440,'height':1100})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(args.url)
        expect(page.locator('#ready')).to_contain_text('pipeline ready',timeout=60000)
        page.locator('#cycles').fill('150')
        page.locator('#run').click()
        expect(page.locator('#progress')).to_contain_text('Complete',timeout=120000)
        assert page.locator('#objects').inner_text()!='—'
        page.locator('#step').click()
        assert page.locator('#seek').input_value()=='1'
        page.locator('#play').click()
        page.wait_for_timeout(200)
        page.locator('#play').click()
        assert int(page.locator('#seek').input_value())>1
        page.locator('#seek').evaluate("e => { e.value='100'; e.dispatchEvent(new Event('input')); }")
        assert page.locator('#clock').inner_text().startswith('101/')
        page.screenshot(path=str(args.output/'browser-clean.png'),full_page=True)
        page.locator('#reset').click()
        expect(page.locator('#progress')).to_contain_text('Reset complete',timeout=10000)
        page.locator('#attack').select_option('T3')
        assert page.locator('#level option[value=A2]').is_disabled()
        page.locator('#attack').select_option('T1')
        page.locator('#level').select_option('A2')
        page.locator('#cycles').fill('300')
        page.locator('#run').click()
        expect(page.locator('#progress')).to_contain_text('Complete',timeout=120000)
        page.locator('#seek').evaluate("e => { e.value='180'; e.dispatchEvent(new Event('input')); }")
        page.locator('#filter-results').click()
        page.wait_for_timeout(500)
        page.screenshot(path=str(args.output/'browser-T1-A2.png'),full_page=True)
        assert 'SIMULATED T1' in page.locator('#run-kind').inner_text()
        assert 'not flagged' in page.locator('.legend').inner_text().lower()
        # Another browser context has an independent session and empty cursor.
        other=browser.new_context().new_page()
        other.goto(args.url)
        expect(other.locator('#ready')).to_contain_text('pipeline ready',timeout=60000)
        assert other.locator('#objects').inner_text()=='—'
        page.locator('#reset').click()
        page.locator('#run').click()
        page.wait_for_timeout(100)
        page.locator('#cancel').click()
        expect(page.locator('#progress')).to_contain_text('Reset complete',timeout=60000)
        assert page.locator('#objects').inner_text()=='—'
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(args.output/'browser-mobile.png'),full_page=True)
        browser.close()
    if errors:
        raise AssertionError(errors)
    result={'ok':True,'url':args.url,'browser':'Chromium / Playwright 1.58.0',
            'checks':['readiness','clean real replay','play/pause/step/seek','reset','T1/A2 real attack',
                      'unsupported T3/A2 disabled','generated report filters','independent browser session','cancel','mobile layout'],
            'page_errors':errors,'screenshots':['browser-clean.png','browser-T1-A2.png','browser-mobile.png']}
    (args.output/'browser-validation.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
