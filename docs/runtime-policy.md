# Explicit runtime policies

`PHANTOMGUARD_RUNTIME_PROFILE=server` is selected only by `compose.server.yaml` or an explicit native environment. Before numerical imports, it fills missing OPENBLAS/OMP/MKL thread counts with one. Explicit values are preserved. CLI and spawned workers use the same function. A plain native invocation uses `development` and does not set numerical threads, a CPU quota or cooling delay. Dockerfile no longer embeds server numerical settings.

Precedence: serve CLI flags > environment > scheduler defaults. Compose shell variables override the selected env file; each example isolates project/files/env. Server defaults: one browser worker, 0.25 logical CPU (2500/10000 microseconds), 2 GiB RAM/no additional swap, two waiting jobs, 30 s rest, 64 PIDs. Generic Compose uses adjustable 4 GiB/128 PID development ceilings and has no CPU quota or cooling delay. Those 2 GiB/64 PID generic ceilings and Docker numerical defaults predated the earlier server patch; this branch removes their universal defaults. Server workers recycle after 16 jobs or 384 MiB post-job RSS on Linux, idle workers expire at the configured session TTL; cancellation/deadline always terminates the affected worker. Frozen parsed recording tuples and the existing training pool cache survive, scoped to a verified content identity. No attacker/RNG survives. Pools are frozen before reuse and evicted above 64 MiB/two variants. Agent 2's public API will replace the temporary private-cache adapter.

## Server

Copy `deploy/server.env.example` to ignored `.env.server`, select absolute artifact mounts and image. Always run `scripts/server-compose` (explicit base + server files, env, project). For staging set `PHANTOMGUARD_SERVER_ENV=/absolute/.env.stage PHANTOMGUARD_COMPOSE_PROJECT=phantomguard-performance-stage`, port 8766. Inspect `config` before `up -d --no-build prototype`. Set WARM_WORKERS=0 to use one fresh process per job.

## Linux native development

```bash
PHANTOMGUARD_RUNTIME_PROFILE=development OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python -m phantomguard serve --root /absolute/workspace --workers 2 --job-cooldown 0 --warm-workers
```

## Windows native development (PowerShell)

```powershell
$env:PHANTOMGUARD_RUNTIME_PROFILE='development'
$env:OPENBLAS_NUM_THREADS='1'; $env:OMP_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'
python -m phantomguard serve --root D:\Hacksprint\phantom-guard --workers 2 --job-cooldown 0 --warm-workers
```

Do not copy `.env.server` to a desktop. Remove inherited PHANTOMGUARD_JOB_COOLDOWN/WORKERS/CPU variables when changing policies; explicit CLI options override inherited serving controls.

## Container development

```bash
docker compose --project-name phantomguard-dev --env-file deploy/development.env.example -f compose.yaml -f compose.development.yaml config
docker compose --project-name phantomguard-dev --env-file deploy/development.env.example -f compose.yaml -f compose.development.yaml up -d --build prototype
```

This selects 4 CPUs/4 GiB and two browser workers as adjustable examples, zero rest, single numerical threads. These are not claims about the Ryzen machine. Base Compose alone has no .25 CPU policy. Never combine server and development overrides.

## Desktop benchmark request to Agent 2

Verify actual core count/RAM/BLAS backend first. Use existing compatible artifacts and fixed inputs/seeds, benchmark cold plus repeated warm cases at numerical thread counts 1/2/4 with fixed process count, then offline evaluation process counts 1/2/4/8 subject to measured RAM. Record true process CPU, wall, RSS and decisions; avoid process count multiplied by numerical threads exceeding useful cores. Tiny AE matrices can be slower with many BLAS threads. Browser workers and offline evaluation `--workers` are independent controls. No detector/artifact rebuilding is authorized on this server; desktop training/calibration belongs to Agent 2. Desktop performance and Windows spawn behavior remain desktop validation work.

Timing scope: process CPU fields start at each replay_worker invocation. Initial spawned-process imports and the warm-worker content-identity verification precede that entry, so use cgroup CPU counters and API end-to-end duration for complete cold-start costs. First usable output is the completed result; progress is not a partial detector result. Legacy detector/assembly CPU fields include quota/scheduling waits and must not be used as true CPU time.
