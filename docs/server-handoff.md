# Server handoff: Phantom Guard P31

## Matching source and payload

- Fork: https://github.com/Aurora-source/phantom-guard
- Branch: https://github.com/Aurora-source/phantom-guard/tree/codex/portable-hosted-prototype
- Verified upstream PR: https://github.com/Krishna-Gunjan/phantom-guard/pull/6
  (merged into `main` on 2026-10-04 as `d836269d4d70433c20db35a524dc908c9dbf9f38`).
- Tested implementation SHA: **c2cc251c6f4cbaf64dc36ad73a0b4d515dbeff79**.
- Final delivery HEAD is a later documentation/evidence-only child. Its SHA is
  verified in the PR and final delivery response; its implementation matches the
  tested SHA. Restore the archive at the explicit tested SHA, as enforced by CLI.
- Initial branch base: `89b182ff5777fe11c1676443085b36a52df0e94c` (accepted PR #4).
  Accepted main `fc88afe` (PR #5) and `f93f283` are merged with both histories preserved. The
  Arch instructions and report portability interface/tests remain included.
- Windows worktree: `D:\Hacksprint\phantom-guard-portable-hosted-prototype`.
- Archive: **D:\Hacksprint\phantom-guard-portable-hosted-prototype\deployment-bundles\phantomguard-c2cc251c6f4c.zip**.
- Archive size: **10319138 bytes**; expanded payload **154599161 bytes**,
  **54 files**, plus the embedded manifest.
- Archive SHA-256: **`849dbd5c12410e75fd4d1ffc43aee45a7380756639548ac7d921dd7a65f90ad3`**.
- Sidecar: the archive's `.zip.sha256` file. Transfer both files. They are ignored
  local payloads and are **not uploaded to GitHub**.

The verified archive maps four unchanged original CSVs to `data/raw`, fifteen
trusted inference/comparison artifacts to `models`, four fold baselines to
`data/processed`, matching YAML/baseline to `configs`, and required real reports
to `docs/results` plus `docs/results/portable`. Its manifest records every payload
SHA-256/size, tested source SHA, dependency versions, configuration and artifact
IDs. Original files came from the external `dataset` directory in the earlier
worktree, with no companion files required; see [data.md](data.md) for exact names,
bytes, schema and hashes. Input hashes were checked before/after import/packaging.

No virtual environment, credential, `.env`, unrelated source directory, job
sidecar, cache, temporary log or Docker image is inside the archive. Text metadata
uses LF; original recording bytes are unchanged. Restoration verifies all paths,
checksums and size bounds **before** writing absent files and refuses differing
existing files. Use a matching clean checkout and preserve existing installations.

## Executed validation and results

- Native Windows, Python 3.11.9: 170 tests passed, zero skips, in both the worktree and normal-wheel clone.
- Ubuntu 26.04.1 WSL2, isolated uv Python 3.11.16: 170 tests passed, zero skips, in a normal-wheel clone. System Python 3.14.4 is not used.
- Final archive source SHA additionally passed the full 170-test Windows suite and fresh archive-only restoration on both OSes, from paths containing spaces and invocation outside the repository.
- Both clean restores verified all 54 payload hashes, restored twice, passed doctor --full, clean/T1-A2 PNG/GIF export, four actual T1–T4 smoke evaluation cells and dependency checks. Their lightweight environments have no PyTorch.
- Real Chromium/Playwright checks ran on Windows against Windows, WSL Ubuntu and Debian container APIs. Playback, reset, cancellation, unsupported choices, report filters and independent sessions passed with zero page errors. The final rebuilt container also passed all four 600-cycle attack jobs and fresh-report provenance checks.
- Container runtime: pinned Python 3.11.17 Debian bookworm, Docker Linux/amd64. Read-only root/inputs, nonroot UID, 2 CPU, 2 GiB and 64-PID limits verified. Missing-model readiness correctly returned 503 while health returned 200; normal readiness returned 200.
- Real Windows Tk clean/attacked event loops passed automated closure. Headless viewer exports passed on Linux; no native Linux desktop browser, Linux Tk interaction or human usability review is claimed.
- Full matrix ran at e530d8f with frozen models/config. Later changes anchor viewer input paths and normalize source newlines. All scoring code is identical after newline normalization; both final-wheel smoke runs match 36 corresponding full-matrix layer rows and valid AE/IF scores within 1e-12. Original benchmark hashes remain intact.
- Compileall, pip check, source/payload/original dataset hashes and diff whitespace checks passed. The bundle rebuilt byte-identically. Actual home-server deployment remains for the server session.

The complete fresh generated matrix and clean reports are in
[results/portable](results/portable/summary.md); previous accepted measurements
remain in the parent results directory. `portable_validation.json` identifies
executed commands, OS/runtime, tests, model/data/config provenance and outputs.
`bundle_validation.json` records final archive/restoration verification separately
(an archive cannot contain its own final checksum).

Fresh clean results: **1.42 false alerts/minute time-block test**, **3.409 LOSO**.
The complete sweep has **2,411 eligible attack runs**, **198 no-material attempts**,
**55 slot-capacity exclusions**, and **1,296 unsupported T3 requests**, with no
integration blocker. **18 completed runs fully evade scene detection**.
For T4/A3–A4, scene detection is about 48% time-block / 43% LOSO; exact forged-object
identification is about 23% / 20%. These are distinct denominators, not proof of attribution.
Worst completed attacked-run p99 is **8.55 ms** under 24 workers; separate idle
single-worker clean p99 is **1.97 ms**. Both are below the configured 10 ms
processing budget. Assembly p99 is separately **33.5–33.6 ms** under assumed ticks.
The baseline/train/calibrate sequence took about 232 seconds, clean evaluation
47 seconds, and full attack evaluation 1,987 seconds on the 32-logical-CPU workstation.
Representative 600-cycle container jobs took 9.3–9.8 seconds; an active sample used
145 MiB (not a measured peak). Server latency/resource usage must be measured there.

No thresholds, learned weights or model choice were selected from these held-out
results. All five bundles were prepared offline in baseline -> train -> calibrate order after the upstream artifact mismatch; no training occurs in API requests.
The under-one clean-alert/minute acceptance requirement remains **NOT MET**.
False-alert investigation points to genuine RCS/envelope/repeated-motion variation
and LOSO protocol/envelope generalization; ablations and per-reason counts are
reported. Unit test success is not a performance acceptance claim. Coordinates,
tick duration and reconstructed header DLC remain assumptions. Static phantoms,
sparse eligible motion and slot capacity can evade or prevent generation. Source
I/O, attacker planning, IF comparison, JSON/network and rendering are excluded
from detector CPU timing. Assembly delay is reported separately.

Actual Ubuntu home-server deployment, physical radar/CAN hardware, a public
reverse-proxy integration and human usability review have **not** been performed.
Ubuntu 26.04.1 ran under WSL2; the container is Debian bookworm on Docker Desktop's
Linux engine, not an Ubuntu image. Arch guidance is retained but Arch was not run.
CI configuration is included; local evidence does not claim future CI results.

## Transfer from Windows PowerShell

Set the unknown connection details from your existing server configuration.
`RemoteTransferDir` must be an **absolute directory writable by your SSH user**;
do not substitute an assumed username, public IP or existing service directory.

```powershell
$ServerHost = 'SET_YOUR_SERVER_HOST_OR_SSH_ALIAS'
$ServerUser = 'SET_YOUR_SSH_USERNAME'
$ServerPort = 22  # change to your actual SSH port
$RemoteTransferDir = 'SET_ABSOLUTE_WRITABLE_SERVER_TRANSFER_DIRECTORY'
$Destination = "${ServerUser}@${ServerHost}"
$Bundle = 'D:\Hacksprint\phantom-guard-portable-hosted-prototype\deployment-bundles\phantomguard-c2cc251c6f4c.zip'
$BundleName = Split-Path -Leaf $Bundle
$ExpectedSha256 = '849dbd5c12410e75fd4d1ffc43aee45a7380756639548ac7d921dd7a65f90ad3'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Bundle).Hash.ToLowerInvariant() -ne $ExpectedSha256) { throw 'Bundle checksum mismatch' }
ssh -p $ServerPort $Destination "mkdir -p -- '$RemoteTransferDir'"
scp -P $ServerPort "$Bundle" "${Destination}:${RemoteTransferDir}/$BundleName"
scp -P $ServerPort "$Bundle.sha256" "${Destination}:${RemoteTransferDir}/$BundleName.sha256"
scp -P $ServerPort 'D:\Hacksprint\phantom-guard-portable-hosted-prototype\docs\server-handoff.md' "${Destination}:${RemoteTransferDir}/server-handoff.md"
ssh -p $ServerPort $Destination
```

These commands transfer privately over your configured SSH connection; they have
not been executed against the unknown home server. Use quoted paths throughout.

## First commands in Codex on the Ubuntu server

Select two explicit absolute directories; they must be yours and separate from
the server's existing services. Docker/Compose must already be installed. Use the
dedicated 3.11 environment for restoration/offline diagnostics, never system 3.14.
The container itself installs only the pinned lightweight serving runtime.

```bash
TRANSFER_DIR="SET_THE_SAME_ABSOLUTE_TRANSFER_DIRECTORY"
APP_ROOT="SET_AN_ABSOLUTE_NEW_APPLICATION_DIRECTORY"
BUNDLE_NAME="phantomguard-c2cc251c6f4c.zip"
TESTED_SHA="c2cc251c6f4cbaf64dc36ad73a0b4d515dbeff79"
cat "$TRANSFER_DIR/server-handoff.md"
(cd "$TRANSFER_DIR" && sha256sum -c "$BUNDLE_NAME.sha256")
git clone https://github.com/Aurora-source/phantom-guard.git "$APP_ROOT"
cd "$APP_ROOT"
git fetch origin codex/portable-hosted-prototype
git checkout --detach "$TESTED_SHA"
git remote add upstream https://github.com/Krishna-Gunjan/phantom-guard.git
# Install uv only if missing; use its official installer, then expose its binary:
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.11
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install --python .venv/bin/python -r requirements/offline.lock
uv pip install --python .venv/bin/python --no-deps .
export PHANTOMGUARD_ROOT="$APP_ROOT"
python -m phantomguard init
python -m phantomguard verify-bundle --archive "$TRANSFER_DIR/$BUNDLE_NAME"
python -m phantomguard restore-bundle --archive "$TRANSFER_DIR/$BUNDLE_NAME"
python -m phantomguard doctor --full
python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --cycles 150 --gif-cycles 40 --export --output-dir runs/smoke/clean
python -m phantomguard replay --file onePersonMovingFrontAndBack.csv --attack T1 --level A2 --seed 11 --cycles 150 --gif-cycles 40 --export --output-dir runs/smoke/attack
python -m phantomguard attack-eval --smoke --workers 1 --output-dir runs/smoke/evaluation
cp .env.example .env
docker compose config
docker compose build
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS http://127.0.0.1:8765/readyz
docker compose logs --tail 100
```

Before startup check whether port 8765 is free (`ss -ltn '( sport = :8765 )'`);
if occupied, set `PHANTOMGUARD_PORT` in `.env` and use that port in checks/tunnels.
The example selects `/workspace/docs/results/portable` for fresh report display.
The Compose project `phantomguard-p31` has its own network/runs volume, localhost
binding, read-only inputs/models/config, UID 10001, restart behavior, health check,
2 CPU/2 GiB/64-PID limits, bounded jobs/outputs and rotating logs. No GPU/training
service is used. Do not rebuild a baseline independently of the restored models.

If reusing an existing clone, first inspect status/remotes; fetch and check out the
tested SHA without reset/clean. Add `upstream` only if absent. Refused restoration
means an existing file differs: preserve it and restore into a separate matching
clean clone. The transferred guide remains available while the tested code
checkout predates its final documentation-only commit.

For local viewing from Windows, keep the server service on loopback and run:

```powershell
ssh -p $ServerPort -N -L 8765:127.0.0.1:8765 $Destination
```

Open `http://127.0.0.1:8765`. Choose another local port if needed. For an existing
reverse proxy/tunnel forward to the loopback service, configure TLS/access
authentication there, and preserve session isolation. Session tokens provide job
ownership, not account authentication. See [deployment.md](deployment.md) for
logs, stop/restart, cleanup, readiness errors and safe updates. A 503 readiness
response must be fixed; it is not a successful start or a reason to train online.

## Server continuation acceptance

Verify the transferred SHA/payload and doctor output, then repeat smoke and browser
checks on the actual server. Optional full validation needs dev.lock, CPU training
lock and Chromium; it never tunes on held-out results. Browser reset/cancellation
must create/terminate isolated runs. Seeking displays already computed causal
outputs; no future frames influence earlier verdicts. Green means not flagged.
Labels remain separate final-index sidecars; scene alerts are distinct from
identifying forged objects. Retain unsupported/evading/censored outcomes and
record actual machine latency instead of assuming these workstation measurements.
