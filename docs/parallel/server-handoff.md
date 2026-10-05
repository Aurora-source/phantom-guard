# Agent 1 runtime and deployment handoff

Common anchor: 3813b51d0669a4f702638d906cf456034ca8f16a. Runtime source tested in the staged image: 9dd938cbde46343c1c0ac942006ebe2f112bbed4. Branch: codex/server-performance in the Aurora-source fork. No detector, planner, learned feature/config, trained artifact or browser source changes are included. The original archive and all dataset originals are preserved.

The interface contract is [interface-runtime.md](interface-runtime.md); portable resource selection and desktop benchmark requests are in [../runtime-policy.md](../runtime-policy.md). The full original deployment report remains in [../server-validation.md](../server-validation.md), with an appended measured runtime experiment. Raw timestamped evidence is ignored under runs/server-performance/20261005.

Job admission/readiness now uses trusted read-only file metadata and periodic content verification. Missing/replaced artifacts invalidate success; changed configuration requires restart. The server selects one warm spawned worker, bounded immutable recording/pool retention, 16-job/384-MiB worker recycling, cancellation/deadline termination and idle TTL. Each job creates new model instances, Detector/history, attacker/RNG, frame/output lists and labels. Full segment attack planning/materialization is retained. The temporary adapter freezes the existing private pool cache; Agent 2 should replace it with its public versioned preparation API. No learned/statistical artifact is generated on this server.

Existing endpoints and evidence remain compatible. Optional progress stages and correct process CPU timings supplement legacy wall-clock fields. The completed result stays private bytes. Safe same-origin /assets paths are packaged under web/static/assets for Agent 3; CSP and private no-store headers are retained. No optional attacker ground-truth API is added.

Relevant validation: 13 runtime tests and 21 existing browser/backend tests passed with restored artifacts. Native doctor --full passed all five model/baseline sets. The local Chromium check passed independent sessions, cancellation/reset, playback/seek, unsupported choices and private responses. Native Linux default policy tests confirm no server quota/cooldown/numerical caps are silently selected; Windows hardware/concurrency benchmarking remains for Agent 2.

Later integration must fetch completed Agent 2/3 commits and handoffs into a separate codex/prototype-integration worktree and merge normally. No Agent 2/3 branches have been integrated here. Keep the original artifact bundle until a new compatible tested bundle is explicitly supplied. Missing/new artifacts block only deployment of that detector version.

The deployed service uses the existing production Compose project phantomguard-server, private localhost port 8765, existing application runs volume/network and unchanged host-running rikon-home tunnel route. Staging used its own project/8766 and is stopped after promotion. Current local/public readiness returned HTTP 200 with runtime identity abdeb69002b69b4fd1a0d1e0257fd6b2bc6cc3f648834dd4624911786e686046. The image is phantomguard-server:performance-9dd938c, Docker ID sha256:fba909d2252e5aff4bbb3f38c357afe7210ffca1d57bafa4d36fa88ac4efca83.

At identical quota/rest, five fixed jobs used 109.106 versus 45.752 measured cgroup core-seconds (2.38× reduction). Warm T1 active wall dropped from about 88 to 14.02 seconds; T2–T4 from about 88–99 to 17.5–23.4 seconds. First cold T1 still prepares pools (61.64 seconds planning, 104.70 end-to-end). Eleven final staged jobs completed with no failures/timeouts. Exact non-timing detector outputs and five full-stream/EOF-label/lifecycle hashes matched, including a new T1 seed. Peak app RAM fell from 320 to 287 MiB; warm idle retains 140.5 versus 123 MiB before. The final full benchmark kept the three-minute temperature mean below 80°C. All fixed requests keep full attack planning and detector frames.

Operational commands (each wrapper explicitly selects both Compose files and project/env):

```bash
APP=/home/lucifer/projects/Hacksprint-2026/phantom-guard-server-performance
export PHANTOMGUARD_COMPOSE_PROJECT=phantomguard-server
export PHANTOMGUARD_SERVER_ENV="$APP/.env.promote"
"$APP/scripts/server-compose" config
"$APP/scripts/server-compose" up -d --no-build prototype
"$APP/scripts/server-compose" ps
"$APP/scripts/server-compose" logs --tail 100 prototype
curl -fsS http://127.0.0.1:8765/readyz
curl -fsS https://demo.rikon-karmakar.quest/readyz
"$APP/scripts/server-compose" restart prototype
"$APP/scripts/server-compose" stop prototype
```

For an update, fetch the completed reviewed branch into this worktree, retain the prior source/image and env, build with the staged env/project/8766, repeat the documented fixed checks, then point .env.promote to that verified image and use the commands above. Never restore a new bundle at a mismatched source or manufacture artifacts on the server.

Rollback to retained thermal-tuned image with the same absolute artifact mounts and limits:

```bash
env PHANTOMGUARD_SERVER_ENV="$APP/.env.promote.rollback" PHANTOMGUARD_COMPOSE_PROJECT=phantomguard-server "$APP/scripts/server-compose" config
env PHANTOMGUARD_SERVER_ENV="$APP/.env.promote.rollback" PHANTOMGUARD_COMPOSE_PROJECT=phantomguard-server "$APP/scripts/server-compose" up -d --no-build prototype
curl -fsS http://127.0.0.1:8765/readyz
```

Original rollback images, dataset originals, model sets and archive remain present. The bundle checksum is 849dbd5c12410e75fd4d1ffc43aee45a7380756639548ac7d921dd7a65f90ad3; serving model ID is 2ad682efc017e2cf96d856772b20db898edd6c1e253947b3df013a671f067115, baseline SHA b7f592bad60c25bfa4825c528cbb69d05b76239a5d2f500b755d41c933f82faf. No new bundle is required for this runtime-only release. Existing false-alert/attack-evasion limitations remain unchanged and visible.

Public Chromium repeat passed all five grouped checks and 94 no-store/DYNAMIC private responses, with no page errors. The first attempt caught the frontend reset/poll message race; its required Agent 3 fix is documented in interface-runtime.md. Cancellation/session ownership and replay work correctly, but the intermittent display race remains open.
