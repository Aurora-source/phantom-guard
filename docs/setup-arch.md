# Arch Linux

The setup guidance accepted in upstream PR #5 is retained here alongside the
Windows and Ubuntu quick starts. This portable branch pins Python **3.11** and
dependencies; Arch's rolling system Python is not the serving runtime.

```bash
sudo pacman -Syu --needed git uv tk
git clone https://github.com/Aurora-source/phantom-guard.git
cd phantom-guard
git checkout codex/portable-hosted-prototype
uv python install 3.11
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install --python .venv/bin/python -r requirements/offline.lock
uv pip install --python .venv/bin/python --no-deps .
export PHANTOMGUARD_ROOT="$(pwd)"
python -m phantomguard init
```

Use [the README data/artifact steps](../README.md), then `doctor --full`, replay,
evaluation and `serve`. Supply all four case-sensitive CSV filenames through
`import-data --source` or the configured read-only external data root. Models and
recordings are ignored. CPU training uses `requirements/training-cpu.lock` from
the official PyTorch CPU index. Never install into Arch's externally managed
system Python, and preserve existing environments when creating a new one.

`tk` is needed only for the matplotlib interactive window in a graphical session.
Headless exports and the browser need neither Tk nor a desktop; Tk uses XWayland
on Wayland. No CUDA/GPU is required. Evaluation workers each default to one BLAS
thread; choose `--workers` to fit memory and CPU instead of assuming all CPUs are
available. Failed checks never count as passes; cache provenance and final frame
index labels remain in the generated reports. Arch itself has not been tested
in this assignment; executed Windows/Ubuntu/container checks are in the handoff.
