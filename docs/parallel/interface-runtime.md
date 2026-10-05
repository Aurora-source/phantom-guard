# Runtime interface contract (Agent 1)

Anchor: `3813b51d0669a4f702638d906cf456034ca8f16a`. Existing catalog/session/job/result/cancel/evaluation endpoints remain compatible. Results remain authenticated private bytes with `Cache-Control: no-store`.

## Request to Agent 2

Provide a public immutable prepared-input/pool API with a versioned identity including recording content hashes, exact training segment bounds/split provenance, decoder/feature schema, config, model/baseline identities and implementation. A new attacker/RNG and Detector are required per request. Expose optional prepared pools to the existing attacker factory without recomputing unrelated training statistics. Give preparation CPU/wall timing and bounded retained-byte estimates; freeze arrays and forbid mutable request state in caches. Full-test-segment planning must preserve seeded frame bytes/order/timestamps/indices and EOF-finalized labels. Agent 1 currently retains full attacked-stream materialization. Please publish completed branch/SHA, compatibility handoff and equivalence fixtures through the user; no detector/artifact changes are included in this branch.

## Request to Agent 3

Existing `/`, `/app.js`, `/style.css` continue working. Additional packaged files may use `/assets/<relative-path>` under `web/static/assets`, with no traversal/symlinks, correct MIME and same-origin CSP. Ship compiled assets in that directory, disclose build inclusion and any CSP needs. No CDN or inline script policy changes. Progress is optional; consumers must tolerate unknown stages/absent fields. Percentages require a real `total`.

Example status fragment (illustrative fixture, not a measured result):
```json
{"state":"running","progress":{"stage":"detecting","completed":25,"total":150}}
```
Result `timings` preserves existing fields. New `process_cpu_seconds` and `stage_process_cpu_seconds` use process CPU clocks, while existing `detector_cpu_seconds`/`assembly_cpu_seconds` are legacy wall intervals and include quota/descheduling delays. Loading/planning fields remain wall time, planning includes full materialization/I/O. First usable output is the complete result; progress is not detector evidence.

## Lifecycle and cache policy

Optional warm workers reuse existing parsed recording tuples and the existing training pool cache; the adapter freezes NumPy arrays/track lists before reuse, caps pools at 64 MiB/two variants, and clears both caches on any identity change. Each invocation constructs fresh models, attacker, RNG, detector/tracking/fusion, lists and output directory. No pool preparation algorithm changes; a temporary adapter manages the anchor’s existing `attack.pools._cache` pending Agent 2’s public interface. Workers are recycled after bounded jobs or RSS budget, killed/replaced on cancellation/deadline, and caches cleared after input/config identity changes. Fast metadata checks include device/inode/size/mtime/ctime and missing files. Read-only deployment is required; full content verification happens at startup, metadata change, periodic verification and explicit forced checks. An adversary able to mutate artifacts while preserving metadata is outside this local trusted-artifact contract. Configuration file changes require restart rather than using stale in-memory config. Restoring artifacts requires readiness verification before admission.

Worker cache budget: parsed recordings plus at most 64 MiB of frozen existing pool arrays, at most the configured four recording files; 16-job recycle and 384 MiB post-job worker RSS budget on Linux. No cached attacker objects or labels. Status adds optional `runtime.queue_seconds` and `runtime.cooldown_remaining_seconds`; queue time is exact scheduler elapsed time until dispatch, cooling is a current estimate included in waiting time, not additive job CPU.

Measured JSON result fragment: [runtime-fixture.json](runtime-fixture.json), generated from the staged fixed-request output. CPU values are actual process time; the fixture does not imply a desktop performance result.

## Public-browser reset race for Agent 3

Observed through the real public hostname: an in-flight GET /api/jobs/<id> can return 404 after DELETE and overwrite the completed reset message. Reset does remove the worker/output and clears job state, but the old run catch handler writes its error unconditionally. Please guard asynchronous poll/result continuations AND catch/finally UI writes using the captured generation/job identity; generation already increments on reset. Check generation again after awaited GET/result, and ignore errors from stale generations. Preserve normal owner 404 responses and session isolation. Agent 1 does not alter frontend-owned assets or conceal this issue through backend tombstones. Strict browser test recorded the failure under public-browser/browser-events.json; functional cancellation checks should remain separate from this display race.
