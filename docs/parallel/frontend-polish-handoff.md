# Frontend polish and foreground waiting

Base: upstream main `def4a41adae71465e6ccf3f1492665e93865c272`, containing merged PRs #8/#9; branch `codex/frontend-polish`. Agent 2 remains independent.

## Changes and interface

Source and rebuilt assets are delivered together. Existing API/session/generation guards and server runtime are retained. The additional `/assets/theme.js` is packaged by existing rules and served through the existing safe same-origin asset route. CSP stays unchanged. Native/development/server policies and detector artifacts are unchanged.

- Home, Replay Lab, How It Works, embedded illustrated Guide and Results.
- Explicit selected 2D/3D controls, pre-run empty 3D, visible WebGL fallback, unchanged completed cursor/selection/history across view/theme switches.
- Light/Dark/System preference before stylesheet paint; semantic CSS, SVG, Canvas and Three.js colors; no theme-triggered job.
- One bounded Canvas hero and SVG packet/attack teaching area; hidden/offscreen/reduced-motion safeguards. Only replay creates WebGL. Playback pauses advancement offscreen.
- Selected-track velocity magnitude chart reads completed observations and configured header time. Units remain provisional; missing values and gaps remain missing. No scores or verdicts inferred.
- Central delayed wait with decorative SVG circuit dragon; minimize/expand/Cancel; stage-only announcements. Counts/denominators come from job progress, queue/cooling from current runtime fields. Elapsed clock measures the foreground operation, not detector CPU. Result retrieval is a separate phase. Evidence uses delayed inline feedback; polling cadence is unchanged. Reset/errors/generation guards prevent stale resurrection.
- Legacy `detector_cpu_seconds` remains unchanged; its UI label describes the recorded wall interval accurately.

## Targeted validation

`npm ci --no-audit --no-fund`, `npm run build`, `npm run test:polish` (two unit checks and targeted synthetic browser fixtures under production CSP). Fixtures cover mobile/desktop, themes/persistence/System changes, paused/playing view switches, pre-run selection, WebGL fallback, guide/navigation, illustration pause/step/reduced motion, central/minimized/result-loading waits and a named artifact error. No detector inference occurs in these fixtures. Reports/screenshots go to ignored `runs/frontend-polish/`.

Candidate is staged on loopback 8766 in `phantomguard-polish-stage`, with absolute read-only mounts from the installed original checkout. Local health/readiness and startup logs passed. Public clean/T1-A2 replays and cancel/reset stale-response smoke passed; final image identity and evidence are recorded below. Full suites, detector matrices, stress/performance and thermal tests are deferred as requested.

## Operations

Worktree: `/home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish`.

```bash
rtk proxy env PHANTOMGUARD_SERVER_ENV=/home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish/.env.polish PHANTOMGUARD_COMPOSE_PROJECT=phantomguard-server /home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish/scripts/server-compose up -d --no-build prototype
rtk proxy env PHANTOMGUARD_SERVER_ENV=/home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish/.env.polish PHANTOMGUARD_COMPOSE_PROJECT=phantomguard-server /home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish/scripts/server-compose logs --tail 40 prototype
rtk proxy curl -fsS http://127.0.0.1:8765/readyz
rtk proxy curl -fsS https://demo.rikon-karmakar.quest/readyz
# Roll back only this application to the retained merged #8/#9 release:
rtk proxy env PHANTOMGUARD_SERVER_ENV=/home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish/.env.rollback PHANTOMGUARD_COMPOSE_PROJECT=phantomguard-server /home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish/scripts/server-compose up -d --no-build prototype
```

Rollback image: `phantomguard-server:release-def4a41-20261005`, ID `sha256:a19b606946aae07f8a1e76213a2405a24f2ce66b4142fda378ec3e616e216a17`. Ignored environment files preserve the current absolute mounts and limits. One warm worker, 0.25 logical CPU, 2 GiB memory/no extra swap, queue 2, cooldown 5 s (user-requested change; rollback retains 30 s), numerical threads 1, PIDs 64. Tunnel `rikon-home` remains on private `127.0.0.1:8765`; no Cloudflare or unrelated service changes.

Original bundle rechecked SHA-256: `849dbd5c12410e75fd4d1ffc43aee45a7380756639548ac7d921dd7a65f90ad3`. Installed timeblock model ID `2ad682efc017e2cf96d856772b20db898edd6c1e253947b3df013a671f067115`; baseline SHA-256 `b7f592bad60c25bfa4825c528cbb69d05b76239a5d2f500b755d41c933f82faf`. No training/calibration/baseline generation occurred. Historical clean false alerts and eligible attack misses remain visible.

## Executed public release — 2026-10-05 UTC

Live: https://demo.rikon-karmakar.quest/ . Deployed source/config commit
`60eeadf537bbf1b2699dce00f60ac9779ab587a8` (frontend implementation
`487d03c0241eda93158023a7f054d28cd3540c4c`, then server cooldown change).
Image `phantomguard-server:frontend-polish-60eeadf-20261005`, Docker ID
`sha256:70e2d14ebb424bb29fb68f909e652c36b788b8d45666b212277988ff5d51426e`.
Revision label equals the deployed source. Later delivery commits contain only
handoff/PR descriptions/screenshots and do not change serving code or config.

The exact image first passed health/readiness/startup logs on loopback 8766;
packaged frontend assets, pre-run WebGL and theme were checked there. Promotion
recreated only `phantomguard-server`'s prototype. The staging container is stopped;
its private volume/network and all retained images remain available. Local and
public readiness return 200/ok, container healthy, automatic restart count zero,
no OOM. Actual live environment has cooldown **5**, worker/warm-worker **1**, queue
**2**, OPENBLAS/OMP/MKL **1**; cgroup limits remain CPU **2500/10000**, memory/swap
**2147483648 bytes**, PIDs **64**, loopback **127.0.0.1:8765**. Effective merged
container development configuration independently shows cooldown **0**, adjustable
four CPUs and no fractional quota. This is a configuration change, not a measured
algorithm speedup or a thermal claim.

Essential public Chromium checks passed:

- One newly computed clean and one supported T1/A2 moving replay, each 150 cycles,
  `onePersonMovingFrontAndBack.csv`, seed 11, with original model/baseline identities
  and private `Cache-Control: no-store` results. They were reused for all completed
  playback, chart, theme and view checks; no additional inference was submitted by
  presentation controls.
- Selected 3D before a run and after reset; synchronized controls/label/renderer;
  paused and playing 2D/3D switches retained object selection and cursor/history.
  Themes changed the current result presentation without another job.
- Real central/minimized waiting, guide access, completion removing both panels,
  desktop/mobile light/dark layouts and reduced-motion/manual teaching controls.
- One further bounded 50-cycle request for Cancel/Reset: a real in-flight status
  response was held and replaced with a delayed 404 fixture after successful DELETE.
  The old response neither replaced the cancellation/reset message nor reopened the
  loader; owner status returned 404 and Run became available. No extra completed
  detector replay was required.
- Root, app.js, style.css and assets/theme.js all returned 200. No fatal page errors.
  WebGL fallback, storage/System theme changes, result-retrieval phase and a named
  missing-artifact error were also covered by lightweight synthetic UI fixtures
  under the unchanged production CSP. Those fixtures are not detector results.

Raw evidence: ignored `runs/frontend-polish/{fixture-checks.json,public-smoke.json,
public-clean-result.json,public-T1-result.json,deployment.json,*-ready*.json,
*compose-effective.json,development-effective.json}`. No credentials/session
bearer tokens are saved. Full suites, detector accuracy/attack matrices, broad
session/browser tests, stress/performance benchmarks and thermal tests remain
explicitly deferred until Agent 2 finishes. Existing reported false-alert and
attack-miss limitations remain visible. No compatible artifact upload is needed.

### Screenshots

| State | Light | Dark |
|---|---|---|
| Before (old System-dark preference still rendered the light UI) | [Before light](screenshots/frontend-polish/before-light.png) | [Before dark preference](screenshots/frontend-polish/before-dark.png) |
| Live landing | [After light](screenshots/frontend-polish/after-light.png) | [After dark](screenshots/frontend-polish/after-dark.png) |
| Live mobile | [Mobile light](screenshots/frontend-polish/after-mobile-light.png) | [Mobile dark](screenshots/frontend-polish/after-mobile-dark.png) |
| Live central wait | [Central light](screenshots/frontend-polish/loading-central-public-light.png) | [Central dark](screenshots/frontend-polish/loading-central-public-dark.png) |
| Live minimized wait | [Minimized light](screenshots/frontend-polish/loading-minimized-public-light.png) | [Minimized dark](screenshots/frontend-polish/loading-minimized-public-dark.png) |

Before images capture the old full page; after images capture the landing viewport.
The actual dragon is decorative and omitted from assistive output. The screenshots
show real waiting states, not precomputed detector outcomes.

### Exact build/update and scoped stop

```bash
rtk proxy git -C /home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish fetch origin codex/frontend-polish
# Select a reviewed serving SHA in an isolated checkout; do not overwrite local work.
rtk proxy npm --prefix /home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish/frontend ci --no-audit --no-fund
rtk proxy npm --prefix /home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish/frontend run build
# Executed image build from the exact published serving/config commit:
rtk proxy docker build --label org.opencontainers.image.revision=60eeadf537bbf1b2699dce00f60ac9779ab587a8 -t phantomguard-server:frontend-polish-60eeadf-20261005 /home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish
rtk proxy env PHANTOMGUARD_SERVER_ENV=/home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish/.env.polish PHANTOMGUARD_COMPOSE_PROJECT=phantomguard-server /home/lucifer/projects/Hacksprint-2026/phantom-guard-frontend-polish/scripts/server-compose stop prototype
```

The local builder used its pinned base/dependencies and no learned-artifact commands.
A subsequent build from documentation-only delivery HEAD has the same serving inputs;
the deployed image/revision above is retained precisely for reproducibility.

## Git delivery

Branch pushed: https://github.com/Aurora-source/phantom-guard/tree/codex/frontend-polish .
Upstream PR creation was attempted once after validation and rejected with HTTP
403, `Resource not accessible by integration`. No upstream PR is claimed and no
merge/force-push was performed. Prepared exact title/body:
[frontend-polish-pr.md](frontend-polish-pr.md).
Create/compare URL:
https://github.com/Krishna-Gunjan/phantom-guard/compare/main...Aurora-source:phantom-guard:codex/frontend-polish?expand=1 .
The running validated release is unaffected by this GitHub permission blocker.
