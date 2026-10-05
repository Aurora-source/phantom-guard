# Upstream PR delivery

Title: Reduce hosted replay CPU through bounded warm reuse and explicit runtime profiles

Head: Aurora-source:codex/server-performance
Base: Krishna-Gunjan/phantom-guard main

The fork branch was published successfully. Upstream creation returned HTTP 403, Resource not accessible by integration; no upstream PR was created.

Create URL: https://github.com/Krishna-Gunjan/phantom-guard/compare/main...Aurora-source:codex/server-performance?expand=1

## Exact body

Browser job submission repeatedly revalidated immutable artifacts, and fresh spawned workers rebuilt CSV and attacker pools on every request. This change caches validated readiness/reports and optionally reuses a bounded warm process while creating fresh detector, attacker/RNG and session state for every job.

Server policy is explicitly selected: 0.25 logical CPU, one worker, queue two, 2 GiB/no extra swap, 30-second rest and one numerical thread. Native/development defaults do not inherit those server limits. Cancellation/deadline kills the affected worker; immutable pools/recordings are bounded, frozen and invalidated by verified input/config/code identity. Full attack materialization and all detector frames remain unchanged. Safe packaged /assets routing supports the future frontend branch under the existing CSP.

Validated on the Ubuntu i5-4200M server at unchanged quota/cooldown: the identical five-job stage used 109.106 versus 45.752 measured cgroup core-seconds (2.38x less CPU). Warm T1 active wall was about 88 to 14 seconds; T2–T4 about 88–99 to 17–23 seconds. Peak app RAM was 320 to 287 MiB; warm idle retains slightly more RAM. Eleven staged jobs completed, bounded queue rejection/cancellation and recovery passed, and the three-minute temperature mean stayed below 80°C. Complete non-timing detector outputs plus CAN bytes/order/timestamps, EOF labels and scientific lifecycle sidecars matched exactly, including another seed.

Thirteen runtime tests, 21 existing restored-data API/browser tests, two real cold/warm deadline cases, full five-set artifact doctor, and real local/public Chromium checks passed. Public HTTPS/readiness and the compatible deployed image are verified. The first public check exposed an intermittent existing frontend reset/poll message race; a repeat passed, and the exact generation-guard request is documented for Agent 3. This PR preserves frontend/detector ownership and does not integrate unfinished Agent 2/3 branches.

No models, calibration, baselines or thresholds were regenerated. The original bundle/data and rollback images remain. Established false-alert and attack-evasion limitations are retained. See docs/server-validation.md, docs/runtime-policy.md, docs/parallel/interface-runtime.md and docs/parallel/server-handoff.md for raw-evidence paths, measurement scope, operations/rollback and later integration requirements.

