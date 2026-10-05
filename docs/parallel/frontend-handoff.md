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

In progress. The supplied ZIP SHA-256 matches
`849dbd5c12410e75fd4d1ffc43aee45a7380756639548ac7d921dd7a65f90ad3`.
Models/config/caches/outputs and Python/frontend environments are isolated here.
Raw recordings are shared read-only. Completed measurements, screenshots,
build commands, dependency notices, final source SHA and PR will be recorded below.
