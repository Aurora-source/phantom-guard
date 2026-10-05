# Aurora server deployment and validation

Executed on **2026-10-04–05 UTC**, as `lucifer@aurora-server`. Public application:
**https://demo.rikon-karmakar.quest**. This report records server measurements;
the delivery's Windows/WSL timings are not used as server timings.

## Deployment identity and restored inputs

- Ubuntu 26.04 LTS, kernel `7.0.0-28-generic`, HP ProBook 650 G1,
  Intel i5-4200M: **2 physical cores / 4 logical CPUs**; **10.63 GiB RAM**,
  4 GiB swap, about 67 GiB root filesystem space remaining after builds.
- Docker 29.1.3 / Compose 2.40.3. System Python 3.14.4 was preserved.
  Offline verification uses isolated `.venv` Python **3.11.17**, pinned
  `requirements/offline.lock`; serving uses the pinned Debian/Python Dockerfile.
- Initial restored/tested source:
  `c2cc251c6f4cbaf64dc36ad73a0b4d515dbeff79`. Delivery source:
  `69c3b933b26f230912509f95b1a8dfe0e90b7941`. Their changes are only README,
  handoff and bundle-validation documentation; implementation/dependencies match.
- Runtime implementation commit: **`aefe8ac223d6d87cf9ecebc245407a89720866eb`**,
  branch `codex/server-deployment-tuning`; subsequent report/helper commits do not
  change the running implementation. Installed API/worker hashes match this source.
- Running image: `phantomguard-server:thermal-tuned`, image ID
  `sha256:ea980f8c541d0ee381729e0c574b67ae34d2a65ce0f89f5c3b5d03acc0d393fb`.
  Original image retained as `phantomguard-server:c2cc251-before`, ID
  `sha256:f4ce6d3395f2764bca7887017eda5da1cfc07ff624ee1a5acbfbedf972738864`.
- Archive `/home/lucifer/projects/Hacksprint-2026/phantomguard-c2cc251c6f4c.zip`:
  **10,319,138 bytes**, trusted SHA-256
  `849dbd5c12410e75fd4d1ffc43aee45a7380756639548ac7d921dd7a65f90ad3`.
  The sidecar was absent. After directly verifying this trusted digest, a sidecar
  was written because the implemented restore CLI requires it. Provenance checks
  were used normally at the exact tested source; none were bypassed.
- All **54 payload files / 154,599,161 uncompressed bytes** restored and verified;
  15 model/library files across five artifact tags, calibrated baselines and
  preprocessing contracts passed `doctor --full`. Final rehash found all 54
  unchanged. No training, recalibration, baseline rebuilding or full sweep ran.
- Application root: `/home/lucifer/projects/Hacksprint-2026/phantom-guard`.
  Inputs `data/raw`, `models`, `data/processed`, `configs`, `docs/results` are
  mounted read-only. The four raw CSV hashes match the existing `../dataset`
  originals, which were left untouched. Runtime output is the separate Docker
  volume `phantomguard-server_phantomguard-runs`, mounted at `/workspace/runs`.
- Browser AE artifact ID:
  `2ad682efc017e2cf96d856772b20db898edd6c1e253947b3df013a671f067115`;
  timeblock baseline SHA:
  `b7f592bad60c25bfa4825c528cbb69d05b76239a5d2f500b755d41c933f82faf`.
- Upstream PR #6 was verified **merged** on 2026-10-04 at 20:13:38 UTC,
  merge commit `d836269d4d70433c20db35a524dc908c9dbf9f38`.

## Effective limits and measured thermal decision

Every operation uses `scripts/server-compose`: project `phantomguard-server`,
both `compose.yaml` and `compose.server.yaml`, ignored `.env.server`. No other
container/service was stopped, pruned, reconfigured or restarted to make room.

| Setting | Effective value |
| --- | --- |
| Worker concurrency | 1 spawned replay process |
| CPU | `cpu.max = 2500 10000`: **0.25 logical CPU**, at most 6.25% of this 4-CPU host |
| Memory / swap | 2 GiB ceiling; container swap disabled; 262 MiB workload peak, 285 MiB including browser verification |
| Queue / retention | 2 waiting jobs, 6 retained jobs, 16 sessions |
| Clips / deadline | 1,200 cycles maximum; 150 s worker deadline; 300 s queue expiry |
| Cooling | 30 s rest after a worker finishes or is cancelled |
| Cleanup / output | 600 s idle/retention TTL; 30 s orphan sweep; 64 MiB output admission threshold |
| Processes / numerical threads | 64 PIDs; OpenBLAS/OMP/MKL each 1 thread |
| Backend | `127.0.0.1:8765` only, read-only root, UID 10001, dropped capabilities |
| Logs / temporary files | json-file 5 MiB × 2; `/tmp` tmpfs 64 MiB |

The output admission threshold bounds accepted storage use; it is **not** a
filesystem hard quota. Numerical thread caps and build-context exclusions already
existed in the delivered Dockerfile; the server override makes caps explicit.
Worker observations showed one numerical thread, with about 12 idle container
threads/PIDs, up to 16 during the workload and 20 during concurrent browser/health verification commands.

Initial one-worker, **1 logical CPU / 2 GiB** deployment had ample RAM but reached
93°C during the early thermal check. A 0.35 CPU trial was stopped at 90°C using
the then-conservative spike guard. The owner's clarified policy permits short
90–95°C spikes and requires sustained temperatures below 75–80°C. The final
measurement guard stopped only benchmark jobs if a sample reached 95°C, a
three-minute mean exceeded 80°C, available RAM fell below 2 GiB, swap writes
exceeded 10 MiB/s, or a new host OOM occurred. A 0.25 CPU trial without rest
still crossed an 80°C three-minute mean and was stopped. Adding the 30 s rest
completed all stages: final one-client three-minute mean peaked at **79.81°C**,
instantaneous maximum **89°C**; two-client maximum 80°C; recovery 67–76°C.
This is a bounded, approximately 18-minute workload, not a 24-hour thermal
guarantee under different ambient temperatures or unrelated future workloads.

## Sampling and reproducible load

Ignored evidence directory:
`runs/server-validation/20261004T204200Z/`. `measurement-summary.json` contains
sample counts/statistics (**1,938 total samples**); raw `host-sampling/samples.jsonl` retains `/proc`, disk,
PSI, temperatures, cgroups, processes, service probes and output observations.
Collection ran in a separate responsive background tool session. The initial collector ended at 23:15 UTC; final public checks resumed collection at 03:05 UTC on October 5. No measurements are claimed across that gap. The local-browser stage label continued into additional idle observation after its check, so its aggregate is not a busy-browser benchmark. No monitoring
stack was installed. CPU thermal-throttle counters were recorded and did not increase after temperature sampling began. Cgroup memory PSI stayed zero during the final load; CPU pressure/throttling from the quota is retained in raw data. Samples were approximately **5 s apart**, with Docker PID
discovery/output scans about every 20 s. Five unavailable cgroup observations
during application recreation are omitted from container statistics.

CPU units: host CPU is percentage of all **four** logical CPUs, excluding
I/O wait (reported separately). Docker/cgroup CPU uses **100% = one logical CPU**;
divide by four for its whole-host share. Memory is actual cgroup usage/peak, not
the 2 GiB limit. `memory.peak` is lifetime high water for that container, not a
new peak per stage. Physical disk counters avoid double-counting LVM devices.
Initial temperature sampling began after the first load run; no initial idle
temperature baseline is claimed.

The canonical requests, chosen from `/api/catalog`, were repeated before/after:

| Case | Recording | Cycles / seed | Attack |
| --- | --- | --- | --- |
| Clean | onePersonMovingFrontAndBack.csv | 600 / 11 | none |
| T1 | onePersonMovingFrontAndBack.csv | 600 / 11 | T1/A2 |
| T2 | multiplePeopleChaotic.csv | 600 / 22 | T2/A4 |
| T3 | multiplePeopleChaotic.csv | 1,000 / 11 | T3/A4 translated moving |
| T4 | onePersonMovingSideToSide.csv | 900 / 33 | T4/A4 moving |

Final stages: idle 150 s / 31 samples (includes a harmless controller retry);
one client 533 s / 108 samples; two clients 164 s / 34 samples; four-client burst
195 s / 40 samples; recovery 125 s / 26 samples. Original idle 115 s / 24 samples,
one client 125 s / 26 samples, two clients 149 s / 31 samples; original burst was
thermally interrupted. Single/two-client request sequences are identical.
The final burst controller explicitly starts the expensive worker, queues clients,
checks overload/conflict, cancels all owned jobs, and finishes a fresh clean reset;
the initial burst controller differed, so those burst timings are not a matched
performance comparison. API polling cadence is recorded in workload files.

Final workload: **9 completed jobs, zero failed/timed-out jobs**, 6 deliberate
cancellations, 6 expected HTTP 429 queue rejections and 2 expected 409 duplicate
session submissions. Expected 404s include ownership probes and authenticated
heartbeat probes against a nonexistent job. Final recovery had zero output files
and zero managed job directories; readiness remained healthy, with no automatic
application restarts or new host/container OOMs. One worker with two queued jobs
is the tested operating capacity; four simultaneous clients receive bounded
queue rejections. This is not four-worker support.

### Before/after measurements

| Metric | Initial 1 CPU | Final 0.25 CPU + rest |
| --- | ---: | ---: |
| Idle host CPU mean | 20.49% (24 samples) | 17.05% (31) |
| Idle app CPU mean, one-core units | 2.17% | 3.11% |
| Busy one-client host CPU mean | 46.37% (26) | 22.22% (108) |
| Busy one-client app CPU mean, one-core units | 94.10% | 20.08% |
| Busy sampled/lifetime peak RAM | 252 / 287 MiB | 235 / 259 MiB |
| Peak RAM including two-client stage | 313 MiB | 262 MiB |
| Minimum available host RAM, one-client | 6.74 GiB | 6.76 GiB |
| Host swap written, one-client stage | 4.24 MiB | 0 MiB |
| Clean submission→completion, first case | 5.72 s | 24.85 s |
| T1–T4 submission→completion, first cases | 17.8–20.8 s | 121.3–131.3 s |
| Observed queue, first clean / following attacks | 0.69 s / 0.22–0.71 s | 2.6 s / 29–30 s cooling |
| Two-client clean / T1 completion | 5.5 / 24.3 s | 50.7 / 170.4 s |
| Job status GET median / maximum | 2.05 / 55.57 ms (730) | 7.20 / 39.55 ms (912) |
| Result GET median / maximum | 308 / 1,066 ms (17) | 126 / 204 ms (9) |
| Three-minute temperature mean / spike | initial mean unmeasured; 93°C spot | maximum 79.81 / 89°C |
| Existing Jellyfin probe timeouts, one/two stages | 2 | 0 |

Idle CPU did not improve: cold report/readiness work and the smaller quota are
visible. Warm recovery memory remained about 92 MiB; it did not return to the
initial cold 40 MiB, while child workers/output were reclaimed. Existing host
swap was already about 1,854 MiB; final roughly 1,959 MiB reflects intervening
host activity and page-ins: 221 MiB was written during an earlier thermal-cooldown
stage, with other smaller writes, before the final trial.
The final load itself wrote **zero** swap and Phantom Guard's cgroup swap stayed
zero. Do not interpret the higher total as application memory pressure.

Original one-client host I/O-wait averaged 34.68%; final 1.27%. Physical disk
read/write means were 5.34/1.35 MiB/s initially and 0.038/0.180 MiB/s finally.
Other workloads changed (e.g. Jellyfin memory/CPU), and later reads were warm,
so differences in whole-host utilization/I/O are observational, not isolated
causal effects of the patch. The directly enforced application CPU reduction,
unchanged outputs and reduced result handling are stronger evidence.

Existing services were sampled on the same schedule. Baseline/final one-client
mean local probe times (ms): Jellyfin 10.01→2.89, Stoat 40.45→5.47,
Sonarr 5.61→3.78, Prowlarr 26.36→2.93, nginx 1.30→1.06. Their expected HTTP
codes were preserved throughout the final workload; portfolio returned its
pre-existing 404. All previously running containers kept their identities and
restart counts; existing healthy checks remained healthy. The already-failed
`smartmontools.service` and historical stopped game containers were preserved.

Cloudflared baseline: **29.36 MiB RSS / 0.27% of one CPU**; final one-client
**29.17 MiB / 0.32%**, two-client **29.29 MiB / 0.34%**. Public browser stage (60 samples): **28.16 MiB RSS / 0.53% of one CPU** on average. Its instantaneous/three-minute temperature maxima were **88°C / 77.75°C**. Browser verification brought the container lifetime memory peak to **285.12 MiB**, below the retained 2 GiB ceiling; 20 PIDs/threads were briefly sampled with concurrent verification commands. Public browser stage statistics are separately retained. These include existing tunnel routes.

### Where job time goes

Final first clean: artifact loading 0.84 s, recording loading 4.56 s,
detect/display 11.30 s, presentation/metrics 0.25 s; worker elapsed 16.95 s,
observed queue 2.58 s, submission→completion 24.85 s. T1: artifact 0.86 s,
data 9.26 s, attacker planning/materialization **61.69 s**, detect/display
15.08 s, presentation 0.19 s; worker elapsed 87.08 s, observed queue 29.21 s,
submission→completion 121.29 s. T2/T3/T4 attacker paths took 68.54/61.05/67.25 s.
The attacker field includes attack data materialization/I/O; it is not pure
planner CPU. Worker elapsed excludes spawn/imports/final result writing;
submission time includes readiness/admission, spawn, queue, worker exit and poll
resolution. Result transfer is measured separately. Observed queue has roughly
one polling interval of uncertainty and includes request admission time.

Assembly **data delay** stayed 33.2 ms median / 33.5–33.6 ms p99. Fields called
`detector_cpu_seconds`/`assembly_cpu_seconds` inherit existing `perf_counter`
timers: they are wall intervals, including descheduling/quota waits, not actual
CPU seconds. Actual CPU is in cgroup `usage_usec`. Final per-cycle combined
processing p99 was 49–81 ms across first cases, so the original 10 ms processing
budget is **not met** on this thermally constrained deployment. Few jobs do not
establish stable tail latency. CPU throttling is expected: about 440 throttled
seconds accumulated in the 533 s one-client stage; no memory/OOM events occurred.

## Changes and correctness evidence

1. Serve the worker's atomic finite `result.json` bytes directly, preserving
   session ownership and `Cache-Control: no-store`; avoid parsing/re-encoding a
   multi-megabyte clip on each fetch. Same-result local CPU profiling: eight
   T3 result responses 5.021 s before, 0.015 s after. This is a focused profile,
   not public network latency. Decoded response content remained identical.
2. Expose bounded queue, storage, retention and cooling settings through CLI/env;
   use the measured server limits above. Keep the 2 GiB memory margin.
3. Poll running jobs every 1 s and queued jobs every 2 s (previously 500 ms).
   Display rendering measured about 1.97 ms/call for a real 1,000-cycle result;
   no detector-frame or evidence simplification was justified or applied.
4. Revisit valid UUID orphan output directories after restart and their TTL.
   Original cleanup checked only at startup, leaving young crash directories
   indefinitely; regression coverage now verifies eventual cleanup.
5. Add phase timing and reproducible measurement/browser scripts and a dedicated
   server Compose wrapper/example. Build was serialized and excluded data/models,
   caches, outputs and credentials using the existing `.dockerignore`.

Detector, data decoder, attack implementation, evaluation, model artifacts,
calibration, configuration and dependency lock files are unchanged from tested
source. Every frame's non-timing result matched exactly in all **9 final jobs**
against original runs, including fixed seeds, object IDs/points/scores/alerts,
timestamps and source alignment. All eight completed jobs in the preceding
0.25 CPU trial also matched. Clean/T1-A2 PNG/GIF replay and actual four-type smoke
(4 cells / 36 layer rows) passed after restore; smoke matched delivery results,
apart from measurement worker count and at most 1.12e-16 floating AUROC variation.

Final relevant suite: **21 passed in 68.22 s**, using restored real artifacts and a 30% user-scope CPU cap. It covers validation, bounded queue, ownership, byte-preserving results, actual clean/attack replay, fresh reset, actual timeout release, cooldown and delayed orphan cleanup. Shared detector code was not changed; artifact-generating training tests/full sweep were not run. A temporary isolated container with an empty model mount returned health 200 and readiness 503 with named missing paths, then was removed. Original mounted artifacts were not modified. Two application-only restart checks were executed; the first controller selected the wrong reference file and was corrected. The completed check recovered readiness in **9.50 s**, invalidated old sessions (401), rejected unsupported T3/A2 (400), completed a fresh 150-cycle clean replay exactly matching the original prefix, and reclaimed its aged crash output on the periodic sweep. Other container identities/restart counts/health states were unchanged. Final `doctor --full` passed.

## Cloudflare and browser validation

Host systemd `cloudflared.service` uses locally managed tunnel **rikon-home**,
UUID `9da48199-ab6f-4451-9e38-5df666fa6600`, config `/etc/cloudflared/config.yml`.
Only the demo hostname was added, first ingress rule:
`demo.rikon-karmakar.quest → http://127.0.0.1:8765`. Other existing hostnames,
the SSH route and final HTTP-404 catch-all were preserved. Original config backup:
`/etc/cloudflared/config.yml.bak.phantomguard.20261004T205100Z`.
Actual ingress validation and rule matching passed (demo rule 0, SSH rule 6,
unknown host final rule 8). Only demo DNS was routed to this tunnel. A temporary
overlapping connector kept the existing routes available during connector reload;
the service returned with four connections, then the temporary connector stopped.
Private credentials stayed out of Git and the report.

Public DNS resolves through the server's normal resolver; HTTPS `/healthz` and
`/readyz` return 200. Initial ISP negative caching required a temporary verified
browser resolver override; final validation uses normal DNS. Assets, catalog,
evaluation panel and private API were loaded by real headless Chromium 149,
in two independent contexts. Browser checks below are the synchronized post-restart checks. Earlier harness attempts could read the preceding clean result before the attack request began; those are excluded from browser success claims. The harness now waits for the accepted new job, verifies clean/attack request metadata and awaits reset before closing the browser.

Both synchronized post-restart browser checks **passed** locally and through normal public DNS/HTTPS. Each used two independent browser contexts, completed 150-cycle clean and actual T1/A2 jobs (verified request metadata), queued the second client, rejected cross-session reads (404), cancelled queued/running jobs, reset, stepped/sought, and played the attack output. T3/A0–A2 choices were disabled. Public clean had 0 alerting cycles; the T1/A2 prefix had 49 (3,348 object cycles). Artifact IDs/baselines matched, both result payloads transferred, and no page errors occurred. Public private responses were `Cache-Control: no-store` / `CF-Cache-Status: DYNAMIC` (127 successful private responses); assets/catalog/evaluation/readiness loaded successfully. Screenshots and response logs are in `browser-local-verified/` and `browser-public-verified/` under the ignored evidence directory. No browser access blocker remains. A supplementary Python-urllib health probe was rejected at Cloudflare with HTTP 403 / error 1010; curl GET and real browser requests passed. This is consistent with the existing [Browser Integrity Check](https://developers.cloudflare.com/waf/tools/browser-integrity-check/) client-signature policy; no zone-wide security setting was changed. Use the documented curl checks for operations. A browser-facing deployment does not imply every automated user agent is accepted.

Final post-browser idle observation: **200 s / 41 samples**, no managed output files/directories, no worker retained, about **98 MiB warm container RAM**, no new OOM or automatic restart. Final local and public curl GET health/readiness all returned 200. All 20 pre-existing container identities and restart counts still matched the baseline. The measurement collector was stopped and its 128 MiB raw file saved under the ignored evidence directory.

## Exact operations

From the application root, with the already verified `.env.server`:

```bash
rtk proxy scripts/server-compose config
rtk proxy env COMPOSE_PARALLEL_LIMIT=1 scripts/server-compose build prototype
rtk proxy scripts/server-compose up -d --no-build prototype
rtk proxy scripts/server-compose ps
rtk proxy curl -fsS http://127.0.0.1:8765/healthz
rtk proxy curl -fsS http://127.0.0.1:8765/readyz
rtk proxy curl -fsS https://demo.rikon-karmakar.quest/readyz
rtk proxy scripts/server-compose logs --tail 100 prototype
rtk proxy scripts/server-compose restart prototype
rtk proxy scripts/server-compose stop prototype
```

Use `deploy/server.env.example` for a new local `.env.server`; select a unique
image tag when rebuilding. Update with `rtk git fetch origin`, check out the
explicitly reviewed new commit, install the normal wheel if using native CLI,
then use the same serialized build/up/readiness commands. Reuse the immutable
bundle only while its artifact contracts remain compatible. Do not run builders.

Initial installation/restoration commands, run at the tested SHA in a clean
checkout (all actually performed here):

```bash
rtk proxy /home/lucifer/.local/bin/uv python install 3.11
rtk proxy /home/lucifer/.local/bin/uv venv --python 3.11 .venv
rtk proxy /home/lucifer/.local/bin/uv pip install --python .venv/bin/python -r requirements/offline.lock
rtk proxy /home/lucifer/.local/bin/uv pip install --python .venv/bin/python --no-deps .
rtk proxy env PHANTOMGUARD_ROOT=/home/lucifer/projects/Hacksprint-2026/phantom-guard .venv/bin/python -m phantomguard init
rtk proxy .venv/bin/python -m phantomguard verify-bundle --archive ../phantomguard-c2cc251c6f4c.zip
rtk proxy .venv/bin/python -m phantomguard restore-bundle --archive ../phantomguard-c2cc251c6f4c.zip
rtk proxy .venv/bin/python -m phantomguard doctor --full
rtk proxy env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --cycles 150 --gif-cycles 40 --export --output-dir runs/smoke/clean
rtk proxy env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --attack T1 --level A2 --seed 11 --cycles 150 --gif-cycles 40 --export --output-dir runs/smoke/attack
rtk proxy env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python -m phantomguard attack-eval --smoke --workers 1 --output-dir runs/smoke/evaluation
```

Archive size/hash must be compared to the trusted values above before generating
a missing sidecar or invoking restore. Restore preserves differing existing files;
use a fresh checkout if needed. Installing the offline lock uses pinned ordinary
package wheels; it does not train artifacts or imply an air-gapped installation.

Reproduce monitoring and load in separate background tool/terminal sessions:

```bash
rtk proxy python3 scripts/server-sample.py runs/server-validation/NEW/host-sampling 6000
rtk proxy python3 scripts/server-benchmark.py --samples runs/server-validation/NEW/host-sampling --out runs/server-validation/NEW --label repeat --seconds 120 --poll 1
rtk proxy .venv/bin/python scripts/server-browser-check.py --base https://demo.rikon-karmakar.quest --out runs/server-validation/NEW/browser --executable /home/lucifer/.cache/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-linux64/chrome-headless-shell --samples runs/server-validation/NEW/host-sampling
```

The sampler guard reports pressure; the workload controller cancels only its
Phantom Guard jobs. Stop the sampler by its recorded PID after collection.

Rollback the implementation while retaining thermal bounds:

```bash
rtk proxy env PHANTOMGUARD_IMAGE=phantomguard-server:c2cc251-before scripts/server-compose up -d --no-build prototype
```

That original code has no cooling setting. Exact original 1-CPU configuration is
saved at `runs/server-validation/20261004T204200Z/compose-before.yaml`:

```bash
rtk proxy docker compose --project-name phantomguard-server --env-file .env.server -f runs/server-validation/20261004T204200Z/compose-before.yaml up -d --no-build prototype
```

Its sustained load caused the observed high temperatures. Return to the validated
runtime with the normal wrapper `up -d --no-build prototype`. Tunnel rollback
restores the named config backup and reloads only cloudflared; deleting the demo
DNS route is optional when withdrawing this deployment. Preserve other DNS/routes.

## Git delivery

The server has no Git HTTPS push credentials. The authorized connected GitHub app publishes the same verified file tree to the fork branch `codex/server-deployment-tuning`, based on delivery commit `69c3b933`. GitHub-generated commit metadata gives that published commit a different SHA from the original local commits; the Git tree is compared exactly before advancing the new fork ref. The original local implementation and validation commits are retained on `codex/server-deployment-tuning-audit`. The running image still contains implementation `aefe8ac`; publication does not rebuild or replace it. The working deployment branch is aligned to the published identical tree, and no upstream merge or force-push was performed. [Fork review PR #1](https://github.com/Aurora-source/phantom-guard/pull/1) targets the verified delivery branch so its diff contains only these tuning changes. Creating the upstream PR returned HTTP 403, Resource not accessible by integration. The remaining Git delivery action is to open [the upstream compare form](https://github.com/Krishna-Gunjan/phantom-guard/compare/main...Aurora-source:codex/server-deployment-tuning?expand=1) in an authorized GitHub account and submit the PR (the fork PR preserves the complete description). The running validated service is independent of this permission blocker.

## Remaining detector and measurement limits

Published results remain visible: clean false alerts **1.42/min timeblock** and
**3.409/min LOSO**, exceeding the under-one target; **18 complete attacks evaded
detection**. T4 scene detection was about 48%/43%, exact-object identification
23%/20%. Green means unflagged, not authenticated. Resource tuning did not change
or rewrite these results. Unsupported T3/A0–A2 is a valid rejection.

This is recorded replay with simulated attacks, not live sensor ingestion.
Thermal caps trade throughput for shared-host headroom. No stable service-level
tail latency, all seeds/1,200-cycle combinations, or overnight thermal behavior
is claimed. No models/artifacts are missing; no upload is required.
