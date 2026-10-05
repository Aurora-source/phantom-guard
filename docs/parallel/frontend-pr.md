# Prepared upstream pull request

Title: Add guided radar replay lab with synchronized browser 3D and evidence inspection

Base: `Krishna-Gunjan/phantom-guard:main`

Head: `Aurora-source:codex/frontend-experience`

## Body

The recorded replay UI now connects a working radar lab, an interactive causal architecture and the actual historical evaluation. Judges can open the replay directly, inspect a selected final frame's reasons/scores/status, follow scene and persistent alerts, and switch between the same discrete cycles in 2D and browser-only 3D.

Track histories and alert events are indexed once; bounded trails and paged logs avoid rescanning the entire recording every render. Jobs use catalog support, honest stage/count progress, per-tab reload recovery, cancellation/reset generation guards, bounded polling and hidden-tab suspension. Three.js is pinned and bundled into the existing `/app.js`; Python serves the existing three compiled assets under the current CSP.

The frontend reports missing scores/evidence, preserves green = not flagged, keeps scene findings separate from object attribution, and retains successful examples, misses and clean false-alert variation. Synchronized clean comparison requires explicit source-cycle correspondence; it remains unavailable for legacy results. Ground-truth overlay, precision/localization and measured ablation await an explicit additive evidence contract. The illustrated attack lesson and 3D heights/geometry are labeled illustrative.

Validation: 7 frontend model tests, 27 browser workflow checks, 3 additional actual T2/T3/T4 UI checks, 19 Python API unit checks and 2 isolated real-data API checks passed. Actual clean/T1 runs used the verified supplied artifact bundle and read-only recordings. Desktop/mobile, reduced motion, unavailable WebGL, scene/object separation, persistence, expiry/errors, cancel/reset, two sessions, stale responses, reload recovery and additive schema fixtures are covered. Browser console/page errors were zero.

On the tested Ryzen 9 workstation with Chromium/SwiftShader, paused views rendered no frames; synchronous browser render p99 was 1.5 ms in 2D and 2.9 ms in 3D. Compiled JS is 566,222 bytes (estimated gzip 148,926). Measurements, provenance, before/after screenshots, dependency licenses, exact Windows/Linux commands and integration limits are in [the handoff](https://github.com/Aurora-source/phantom-guard/blob/codex/frontend-experience/docs/parallel/frontend-handoff.md).

This branch starts at the shared PR #7 anchor `3813b51d0669a4f702638d906cf456034ca8f16a`. Agent 1 retains runtime, integration and deployment ownership; the changed runtime files are the three existing static assets. The frontend environment, restored copies and generated outputs are isolated. No unfinished parallel branch is merged.

Screenshots: [before](https://github.com/Aurora-source/phantom-guard/blob/codex/frontend-experience/docs/parallel/screenshots/before-desktop.png), [2D](https://github.com/Aurora-source/phantom-guard/blob/codex/frontend-experience/docs/parallel/screenshots/after-desktop.png), [3D](https://github.com/Aurora-source/phantom-guard/blob/codex/frontend-experience/docs/parallel/screenshots/after-3d.png), [mobile](https://github.com/Aurora-source/phantom-guard/blob/codex/frontend-experience/docs/parallel/screenshots/after-mobile.png).
