# Frontend experience handoff (Agent 3)

## Integration contract — opened 2026-10-05

Branch `codex/frontend-experience`; isolated worktree `D:\Hacksprint\phantom-guard-frontend-experience`.
Common anchor verified: `3813b51d0669a4f702638d906cf456034ca8f16a` (upstream PR #7).
Upstream main had no newer commits on the first fetch. No other unfinished branch is merged.

**Asset request to Agent 1:** none. Development-time bundling emits only
`src/phantomguard/web/static/{index.html,app.js,style.css}`. Three.js and controls,
if used, are bundled into the existing classic script. No CDN, additional route,
font/texture request, dynamic import, server renderer, CSP change or package-data
change is required. Please validate these three paths against your final server.

**Optional additive API request to Agents 1/2:** retain current catalog/request,
job/result and evaluation fields. Publish versioned runtime/evidence contracts in
`docs/parallel/interface-runtime.md` and `interface-evidence.md` with a pushed
commit before integration. Useful additions: catalog recording descriptions and
per-selection eligibility/seed bounds; queue/cooling stage/counts; result source
cycle/time correspondence for clean/attacked alignment (do not join emitted frame
indices); score bounds/definitions and explicit persistent/current reasons;
evaluation exclusions/unsupported/censored denominators, precision/localization,
measured ablation and run commit/artifact IDs. Ground truth must be separate
presentation-only data, keyed to final frame identity, never inferred from flags.
Until supplied, unavailable evidence is identified explicitly, legacy progress is
not fabricated, and synchronized comparison requires declared correspondence.

## Reference access

- `qczWrEklqnQ`: the primary YouTube URL redirected to Google's anti-bot page;
  no verified title, visuals, captions, transcript or timestamps obtained.
- `YvLgZiwoT3U`: primary page exposed the title “Why My Websites Always Look
  Next Level”; body contained navigation only. No playback, transcript,
  observable visual technique or timestamps obtained.
- `dn6MDl86fRY`: primary page exposed the title “(REALLY) How To Build a $10,000
  Website With CHATGPT 6 Codex Astra (MAX)”; body contained navigation only.
  No playback, transcript, observable visual technique or timestamps obtained.

No claim is made that these videos were watched. The design is original:
an instrument-like radar workspace, paper-toned educational sections, orange
navigation accents, green not-flagged markers, precise typography and restrained
browser-only depth. No third-party branding or video assets are copied.

Primary implementation references inspected:
[on-demand rendering](https://threejs.org/manual/pages/rendering-on-demand.html),
[responsive rendering](https://threejs.org/manual/pages/responsive.html),
[page visibility](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API),
[reduced motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion).

## Validation and delivery

Implementation tested at `5c09bb524df008b84a7b626eb13323ec742e659e` on
`codex/frontend-experience`; documentation/test-only delivery commits follow it.
Upstream PR: [#8](https://github.com/Krishna-Gunjan/phantom-guard/pull/8).
The supplied ZIP SHA-256 matches
`849dbd5c12410e75fd4d1ffc43aee45a7380756639548ac7d921dd7a65f90ad3`.
Models/config/caches/outputs and Python/frontend environments are isolated here.
Raw recordings are shared read-only. Completed measurements, screenshots,
build commands, dependency notices, final source SHA and PR will be recorded below.

Delivery completed: `codex/frontend-experience` is pushed only to the user's fork,
and upstream PR #8 is open for review. The GitHub connector's PR creation request
returned 403; the machine's existing Git authentication successfully created the
same authorized PR through GitHub's API. No credentials were printed or stored.

## Completed experience

Three linked areas provide a direct working replay, optional field guide,
interactive causal architecture, user-stepped illustrative attack sequence and
actual historical evaluation. A configurable discrete 2D viewer and optional
client-only Three.js view share the exact result cursor. Symbols are planar
observations: no measured height, road, person/car classes or hardware detail is
invented. Green says **not flagged**. Scene findings never recolor every object.

Track histories and event lists are indexed once. Trails hold at most 20 points
from one lifetime. Final-frame selection also distinguishes duplicate observations
associated with one track. The event log pages 30 rows, with an alerts-only
filter; clicking an event selects its actual cycle/frame. Details expose current
reported reasons, persistence, actual zero scores, warm-up/unavailable status,
optional calibrated bounds and explicit unknown attribution.

Polling uses real stages/counts, bounded backoff, a hidden-tab gate before every
status request, generation guards and aborts. Terminal polling stops. Completed
jobs recover on reload using per-tab session storage; expired tokens/jobs offer
recovery. Cancellation and reset clear current results and prevent late responses
from reinstalling them. Completed clean results may be retained without creating
another job. Sequential runs respect the existing one-job-per-session scheduler.

The original historical evaluation bundle remains the evidence source. The
browser shows actual denominators, run-status counts, misses, per-recording clean
episodes/minute, report/source/artifact provenance and sortable recall/detection.
It identifies absent precision/localization/ablation data. No historical number
has been replaced by a proposed detector improvement.

## Exact build / preview commands

Source: `frontend/src/`; compiled assets: `src/phantomguard/web/static/`.
Node 22.22.2/npm 10.9.7 and Python 3.11.9 were used on Windows.

From the isolated worktree, PowerShell:

```powershell
rtk py -3.11 -m venv .venv-frontend
rtk .\.venv-frontend\Scripts\python.exe -m pip install -r requirements/runtime.lock
rtk .\.venv-frontend\Scripts\python.exe -m pip install -e . --no-deps
rtk .\.venv-frontend\Scripts\python.exe frontend/tools/restore_preview.py --archive 'D:\Hacksprint\phantom-guard-portable-hosted-prototype\deployment-bundles\phantomguard-c2cc251c6f4c.zip' --raw 'D:\Hacksprint\phantom-guard-phases-3-6\dataset'
rtk npm --prefix frontend ci
rtk npm --prefix frontend run build
rtk npm --prefix frontend test
rtk npm --prefix frontend exec -- playwright install chromium
rtk .\.venv-frontend\Scripts\python.exe -m phantomguard serve --port 8773 --workers 1 --max-cycles 600 --job-seconds 120 --root 'D:\Hacksprint\phantom-guard-frontend-experience' --config runs/frontend/runtime/configs/default.yaml --data-dir 'D:\Hacksprint\phantom-guard-phases-3-6\dataset' --models-dir runs/frontend/runtime/models --baseline runs/frontend/runtime/configs/baseline.json --processed-dir runs/frontend/runtime/data/processed --reports-dir runs/frontend/runtime/docs/results --output-dir runs/frontend/jobs
```

In another terminal, run `rtk npm --prefix frontend run test:browser`, then from
`frontend/`, `rtk node tests/attack-options.mjs`. The browser URL defaults to
`http://127.0.0.1:8773`; `PHANTOMGUARD_BROWSER_URL` overrides it.
Open that URL for the ordinary Python-served preview. No Node service is needed.

On Linux the same npm commands work in `frontend/`. Create a Python 3.11 venv,
install the same runtime lock and editable package, then use
`python frontend/tools/restore_preview.py --archive /absolute/bundle.zip --raw /absolute/read-only-recordings`.
Use the same Python serve arguments with the local absolute root/raw paths and
forward slashes. The helper verifies the archive and each shared raw recording,
restores absent payloads under `runs/frontend/runtime`, refuses differing existing
copies, and checks readiness. A `.zip.sha256` sidecar is required by bundle verification.

The bundle's tested code SHA is `c2cc251c6f4cbaf64dc36ad73a0b4d515dbeff79`.
Compared with the required common anchor, detector/attacker/tracker/frame code
is identical; inspected backend changes add timings/byte serving/scheduler limits.
The helper restores into ignored isolated preview paths because the ordinary
`restore-bundle` command intentionally requires the exact older bundle commit.
It does not change that safeguard. All 54 archived payload hashes and the four
read-only external recording hashes were verified; runtime doctor passed.

## Tests and observed browser cost

- 7 Node browser-model tests passed: lifetime separation, bounded causal trails,
  scene/object distinction, persistence, final-frame identity, missing versus zero
  metrics, malformed results and declared source alignment.
- 27 browser workflow checks passed: actual 600-cycle clean and T1/A2 recordings,
  desktop 1440×1000, mobile 390×844/reduced motion, step/back/scrub, alerts,
  reload recovery, two actual isolated sessions, cancel/reset, terminal polling,
  eight view switches, three actual repeated runs/resets; synthetic errors,
  expiry, queue rejection, no material, unavailable WebGL, warm-up/bounds,
  empty/malformed scenes, delayed results, legacy progress, additive comparison,
  cooling and hidden-tab polling recovery.
- Python API verification: 19 unit checks passed with 2 data-dependent skips;
  those 2 actual-data cases passed separately with the isolated artifact/data
  environment. A global artifact override is deliberately absent from the
  missing-artifact unit check. Final JUnit outputs stay under `runs/frontend/`.
- Additional T2/A2, T3/A3 and T4/A3 actual 150-cycle UI checks passed and are
  recorded in `attack-options-validation.json`. These are UI/compatibility
  checks; alerting-cycle counts are not forged-object precision or detection rate.

The additional clips reported 47, 12 and 0 alerting cycles respectively. Zero
alerts in a selected prefix does not establish a measured attack evasion: the
legacy browser endpoint does not report emitted/eligible attacks in that prefix.

Actual selected front/back clean clip: 600 cycles, 13,218 object cycles,
20 alerting cycles. Actual T1/A2 clip: 600 cycles, 13,689 object cycles,
315 alerting cycles. The clean alerting cycles remain visible as false alerts.
The browser cannot infer how many of the attacked clip's alerts localize forged
frames. These observations do not replace the offline matrix.

Full generated measurement output: [browser-validation.json](browser-validation.json).
Test machine: AMD Ryzen 9 8940HX, 32 logical CPUs, 31.2 GiB RAM, Windows build
26200; Playwright Chromium 145.0.7632.6 using ANGLE/Vulkan **SwiftShader software
rendering**. GPU hardware present on the machine was not used by this renderer.
The i5 home server is not this browser test machine.

| Sample (~1.5 seconds each) | Main-thread task CPU | Script CPU | Render calls | Browser render median / p99 |
| --- | ---: | ---: | ---: | ---: |
| Paused 2D | 2.91 ms | 0 ms | 0 | no renders |
| Playing 2D | 195.12 ms | 40.06 ms | 40 | 0.90 / 1.50 ms |
| Paused 3D | 0.54 ms | 0 ms | 0 | no renders |
| Playing 3D | 355.23 ms | 77.48 ms | 39 | 1.90 / 2.90 ms |

These are short DevTools Performance samples of the page main thread; they are
not whole-browser/GPU CPU percentages or overnight thermal/device benchmarks.
Render timing measures synchronous browser update/submission, not GPU completion.
Playback is nominal-cadence, discrete and best effort, capped at 60 updates/s.
The exact cursor and timestamp remain inspectable when a browser falls behind.

After eight view switches: 8 GPU geometries, zero textures, ~12.6 MB live JS heap
after forced GC. Three run/reset samples after forced GC: 3.92, 4.15, 4.21 MB;
short-run growth is bounded in this sample, not a proof of indefinite stability.
There are no continuous paused render loops or postprocessing effects; pixel
ratio is capped at 1.5 and the 3D drawing buffer at 1.2 million pixels.
The actual workflow issued 20 status polls across its jobs and recovery checks;
none after completed playback began. Total measured requests: 54.
Unexpected browser console/page errors: zero. All network assets were self-hosted.

| Asset | Raw bytes | Estimated gzip bytes |
| --- | ---: | ---: |
| `/` (`index.html`) | 26,641 | 7,501 |
| `/app.js` (includes pinned Three.js) | 566,222 | 148,926 |
| `/style.css` | 28,026 | 5,964 |

Initial loopback navigation: 190.4 ms; JS transfer 566,522 bytes including headers,
3.2 ms locally. Legacy Waitress serves uncompressed assets; gzip sizes are build
estimates, not observed deployed transfers. WAN/mobile/i5 performance is untested.

Screenshots inspected at the recorded desktop/mobile sizes:
[before](screenshots/before-desktop.png), [after 2D](screenshots/after-desktop.png),
[after 3D](screenshots/after-3d.png), [actual attack](screenshots/after-attack.png),
[mobile / reduced motion](screenshots/after-mobile.png).

## Additive interfaces implemented / requested

No pushed runtime/evidence contract commit was supplied during this task. No
unfinished Agent 1/2 branch was merged or assumed to share these files.
Current legacy schemas are tested. The following opt-in fields are accepted by
the frontend and covered where indicated by **schema fixtures**, not measurements:

- Catalog: existing string recordings or `{id,title,description}` records;
  `seed_min`/`seed_max` (or `seed.{min,max}`); attack entry `motions` and
  `eligibility.reason`. Existing `attacks[].supported/reason` remains authoritative.
- Status: existing `progress.{stage,completed,total}` plus `progress.unit/units`,
  `queue_position` and `cooling_seconds`. Absent progress yields plain text.
- Result comparison: both runs declare
  `provenance.source_alignment={kind:"source_cycle_index",recording_sha256,segment_lo}`,
  and cycles carry integer `source_cycle_index`. Same recording hash and segment
  are required. Alignment never uses emitted frame indices or numeric slots.
- Object/result `score_bounds` provides display-only bounds. Optional provenance
  `source_commit`/`git_head` and `configuration_sha256` are shown if supplied.
- Evaluation optional `denominators`, `localization` and measured `ablation`
  are displayed alongside immutable report provenance. Rich structured fields
  will need adaptation to the exact pushed Agent 2/1 contract.

Legacy comparison stays unavailable because source correspondence is undeclared.
Ground truth has no overlay until a separate final-frame presentation contract
arrives; detector flags are never used as inferred truth. Legacy evaluation
publishes at most 100 rows and filters for all-layer rows: unsupported/no-material
attempt details can be absent even though manifest counts are shown. The status
filter says when no supplied rows match; it cannot invent omitted attempts.
Exact precision, localization, bounds, full-job write/network timings and
measured ablation benefit remain unavailable where the backend omits them.

## Integration and delivery

Agent 1 can cherry-pick the completed frontend commits onto the integration
branch, then validate `/`, `/app.js`, `/style.css` under final CSP and run the
browser tests against the integrated runtime. Existing `web/static/*`
package-data already covers all compiled outputs. No additional static route,
module import, font, texture or endpoint is required. Rebuild using the lockfile
only when source changes; deployment serves compiled files directly via Python.

Dependencies and licenses: Three.js 0.180.0/MIT deployed; esbuild 0.25.10/MIT,
Playwright 1.58.0/Apache-2.0 and Prettier 3.6.2/MIT development-only. Complete
Three.js license is embedded in `/app.js`. See
[dependency notices](../../frontend/third-party-notices.md).
Original SVG/teaching assets and symbolic 3D geometry use no copied branding,
textures, raster generation or third-party visual assets.

Production deployment/CSP confirmation belongs to Agent 1 and remains an
integration check. This task does not deploy, merge, modify main, alter models,
change detector logic or touch API/job code. Worktree and fork branch remain
available for review. Upstream main was still at the common anchor on final fetch.
