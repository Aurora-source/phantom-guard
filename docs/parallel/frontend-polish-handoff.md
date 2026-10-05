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

Candidate is staged on loopback 8766 in `phantomguard-polish-stage`, with absolute read-only mounts from the installed original checkout. Local health/readiness and startup logs passed. Final public clean/T1-A2 replays, cancel/reset stale-response smoke and image identity are recorded in the delivery addendum after promotion. Full suites, detector matrices, stress/performance and thermal tests are deferred as requested.

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

Rollback image: `phantomguard-server:release-def4a41-20261005`, ID `sha256:a19b606946aae07f8a1e76213a2405a24f2ce66b4142fda378ec3e616e216a17`. Ignored environment files preserve the current absolute mounts and limits. One warm worker, 0.25 logical CPU, 2 GiB memory/no extra swap, queue 2, cooldown 30 s, numerical threads 1, PIDs 64. Tunnel `rikon-home` remains on private `127.0.0.1:8765`; no Cloudflare or unrelated service changes.

Original bundle rechecked SHA-256: `849dbd5c12410e75fd4d1ffc43aee45a7380756639548ac7d921dd7a65f90ad3`. Installed timeblock model ID `2ad682efc017e2cf96d856772b20db898edd6c1e253947b3df013a671f067115`; baseline SHA-256 `b7f592bad60c25bfa4825c528cbb69d05b76239a5d2f500b755d41c933f82faf`. No training/calibration/baseline generation occurred. Historical clean false alerts and eligible attack misses remain visible.
