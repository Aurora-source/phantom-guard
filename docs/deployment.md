# Ubuntu home server: isolated CPU prototype

This prepares a local P31 browser prototype; it does not deploy to the owner's
server. Use the tested Git SHA and verified bundle from [server handoff](server-handoff.md).
Docker uses **Python 3.11.17 on Debian bookworm**, with an immutable image digest
and pinned NumPy/SciPy/PyYAML/Waitress runtime. Ubuntu 26.04's system Python is
not used. Native Ubuntu 26.04.1 WSL validation is separately reported; a WSL
test is not a deployment or hardware validation of the home server.

## Container startup

After checking out the matching code and restoring the bundle:

```bash
docker compose config
docker compose build
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS http://127.0.0.1:8765/readyz
docker compose logs --tail 100 -f
```

The project is named `phantomguard-p31`, with a dedicated network and named runs
volume. Host port **127.0.0.1:8765** is loopback only; change it in `.env` if an
existing service uses it. No host network, privilege, GPU, training daemon or
existing service is required. All raw data, models, processed fold baselines,
configs and selected reports are bind-mounted read-only. Runs are a separate
writable volume. The image runs as UID/GID **10001**, drops capabilities, uses a
read-only filesystem, 64 MiB tmpfs, 2 CPU / 2 GiB / 64-process limits and bounded
rotating logs. Do not mount credentials or the Windows environment.

`.env.example` documents Compose substitutions. Environment variables inside the
container use portable `/workspace/...` paths; host input/model directories can
be configured through `PHANTOMGUARD_DATA_MOUNT`/`PHANTOMGUARD_MODELS_MOUNT`.
Relative host paths are relative to compose.yaml. `/healthz` checks process life;
`/readyz` validates recordings, calibration/schema/provenance and writable runs,
and returns **503 with named errors** when required inputs are missing. The Docker
health check uses readiness. Restoring files then retrying readiness is supported.

## Browser/API operation

Serve uses Waitress (eight request threads, 32 connections, 30s channel timeout,
2 KiB JSON bodies) and a separate bounded spawn-process scheduler:

- Default two active jobs, up to four queued, eight retained jobs, sixteen sessions.
- Each job: at most 1,200 cycles, 120s processing deadline (configurable within
  enforced bounds), 600s idle/retention expiry; 128 MiB managed output quota.
- Data/model artifacts are never trained or written in requests. Workers receive
  config, approved recording IDs, validated seeds and supported attack choices.
- Each worker builds its own detector/tracks/persistence/replay/RNG. A session
  token owns its jobs; another token cannot inspect/reset those jobs. Tokens are
  browser-memory only. Closing/refreshing creates a new session; idle cleanup
  removes owned output. Run one service per writable output directory.
- Cancel/reset terminates the worker and removes its output. Restart runs from
  segment start. Seeking only changes the display cursor in already computed
  causal results; it does not reuse future evidence in a detector.
- Browser clips use time-block **test** with the fixed timeblock artifacts. T3
  browser replay uses training recordings; offline evaluator additionally covers
  earlier-stream and unseen provenance. No training data enters the evaluation
  segments or labels enter detector features/thresholds/colors.
- Sidecars retain final emitted frame indices. The browser does not read labels
  or claim attack detection rates for its clip. The evaluation panel shows actual
  published matrix runs with recording/split/type/level/seed/run/config/model IDs.
  Scene detection and exact forged-object identification stay separate.

GET `/api/catalog`, `/api/evaluation`, `/api/evaluation/runs` are presentation
endpoints. POST `/api/sessions` creates a token. POST `/api/jobs`, GET
`/api/jobs/<id>` and `/result`, DELETE `/api/jobs/<id>` require `Authorization:
Bearer <token>`. There are no upload, arbitrary path, shell or training endpoints.
T3/A0–A2 and static T3/T4 are rejected. Some supported combinations lack eligible
source tracks/slots; they fail visibly. Seeds must be integer 0..2^32-1.

## Access through existing infrastructure

Keep the service isolated on localhost. For a private demo, use an SSH tunnel:

```bash
ssh -N -L 8765:127.0.0.1:8765 YOUR_SSH_ALIAS
```

Then open local `http://127.0.0.1:8765`. For an existing reverse proxy/tunnel,
forward to `http://127.0.0.1:8765` without changing application paths. Configure
TLS, access authentication and request/body/rate limits at that existing proxy.
Session ownership is isolation, not an account login. Avoid exposing the prototype
directly to the internet. The UI labels recorded data/simulated attacks visibly.

## Operations / update / stop

```bash
docker compose logs --tail 100
docker stats --no-stream
docker compose restart
docker compose stop
# Stops/removes only this project's containers/network, preserving runs volume:
docker compose down
```

Do not use `down -v` unless you intentionally want to discard generated runs.
To update, fetch the fork branch, check out the explicitly tested new SHA, restore
its matching verified bundle if contracts changed, rebuild/up, and check readiness
and smoke again. Never replace a valid calibrated baseline independently of its
models. Outputs are disposable and bounded; original recordings remain immutable.
Artifact availability is a deployment prerequisite, not something the API hides.

## Native alternative

Use the Linux quick start with a dedicated 3.11 venv and runtime.lock, normal
wheel install, `PHANTOMGUARD_ROOT` and read-only data/models. Start
`python -m phantomguard serve --host 127.0.0.1 --port 8765`. Optional evaluation/
viewer extras are only needed offline. Containers add enforced CPU/memory/process
bounds; the native scheduler still applies queue/cycle/deadline/session/output
limits. Validation reports distinguish native Windows, native WSL Ubuntu and
container Debian runtime rather than claiming they are the same OS.

References: [Python spawn-process behavior](https://docs.python.org/3.11/library/multiprocessing.html#contexts-and-start-methods),
[Compose variable precedence](https://docs.docker.com/compose/how-tos/environment-variables/envvars-precedence/).
