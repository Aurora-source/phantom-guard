# Polish replay views, themes, guide and truthful loading; use a 5s server cooldown

## Changes

- Make 2D/3D selection visible and accessible; permit empty pre-run 3D and explain WebGL fallback without losing completed replay state.
- Add Home / Replay Lab / How It Works / Guide / Results, an embedded illustrated guide, bounded Canvas radar hero and SVG teaching controls.
- Add persisted Light/Dark/System themes with a synchronous same-origin CSP-compatible bootstrap and semantic visualization colors.
- Plot selected completed-track speed from actual velocities/timestamps, retaining provisional units and unavailable states.
- Show delayed central decorative circuit-dragon waiting, honest reported phase/counts/queue/cooling, result retrieval, minimize/expand and Cancel. Reuse existing polling and generation guards; meaningful phase announcements only.
- Set only the explicit server cooldown to 5 seconds at the user's request; preserve CPU/memory/worker/queue limits and development cooldown zero.
- Include source and rebuilt packaged assets; no dependency/CSP/detector/artifact changes.

## Validation and deployment

Two targeted unit tests and nine synthetic browser fixture checks passed under production CSP. Staged package health/readiness/assets/3D/theme passed. Live HTTPS readiness/assets and one clean plus one T1/A2 moving replay (150 cycles each, same front/back recording and seed 11) passed. Those results were reused for playback, view, chart and theme checks; controls submitted no extra jobs. One bounded cancellation/reset check passed with minimized Cancel and a deliberately late stale 404. No fatal page errors. Representative artifact errors and WebGL fallback were fixture checks, not manufactured serving artifacts.

Deployed source/config: `60eeadf537bbf1b2699dce00f60ac9779ab587a8`.
Image: `phantomguard-server:frontend-polish-60eeadf-20261005`, `sha256:70e2d14ebb424bb29fb68f909e652c36b788b8d45666b212277988ff5d51426e`.
Live: https://demo.rikon-karmakar.quest/ . Container environment confirms cooldown 5, one warm worker, queue two and numerical threads one; quota 2500/10000 and memory 2 GiB unchanged. Compatible installed artifacts are reused, original bundle SHA verified. Previous release/env retained for rollback; Cloudflare and unrelated services untouched.

Screenshots, exact commands and evidence: [frontend polish handoff](docs/parallel/frontend-polish-handoff.md). Subsequent delivery commits are documentation/screenshots only. Agent 2 remains independent. Full suites, accuracy/attack matrices, broad browser/session tests, benchmarks and thermal tests are deferred as requested. Historical false-alert target failure and eligible attack misses remain visible. No training/calibration/baseline generation occurred.
