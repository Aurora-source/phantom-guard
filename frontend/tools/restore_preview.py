"""Restore the supplied trusted bundle into isolated, ignored preview paths.

This is a development helper, not a request-time endpoint. Never writes raw data,
tracked configs, models, or report files. Refuses to replace differing copies.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile

from phantomguard.bundle import verify
from phantomguard.workspace import digest, doctor
from phantomguard.config import load_config

EXPECTED = "849dbd5c12410e75fd4d1ffc43aee45a7380756639548ac7d921dd7a65f90ad3"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--raw", required=True, type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    target = (project / "runs/frontend/runtime").resolve()
    verification = verify(args.archive)
    if verification["sha256"] != EXPECTED:
        raise ValueError("This preview helper requires the supplied compatible bundle SHA-256")
    with zipfile.ZipFile(args.archive) as archive:
        manifest = json.loads(archive.read("bundle-manifest.json"))
        for row in manifest["payload"]:
            if row["path"].startswith("data/raw/"):
                raw = args.raw.resolve() / Path(row["path"]).name
                if digest(raw) != row["sha256"]:
                    raise ValueError(f"Read-only recording differs from the bundle: {raw}")
                continue
            destination = (target / row["path"]).resolve()
            if not destination.is_relative_to(target):
                raise ValueError("Destination escaped the isolated preview root")
            if destination.exists():
                if digest(destination) != row["sha256"]:
                    raise ValueError(f"Existing isolated copy differs; preserved: {destination}")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(row["path"]))
    cfg = load_config(target / "configs/default.yaml", root=project, overrides={
        "data_dir": args.raw.resolve(), "models": target / "models",
        "baseline": target / "configs/baseline.json", "processed_dir": target / "data/processed",
        "reports": target / "docs/results", "output": project / "runs/frontend/jobs",
    })
    state = doctor(cfg)
    print(json.dumps({"bundle": verification, "preview": str(target), "readiness": state}, indent=2))
    if not state["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
