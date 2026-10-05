#!/usr/bin/env python3
"""Hashed, read-only session intake (see phantomguard.eval.intake for the contract).

    python -m phantomguard.commands.intake scan --source D:/recordings --reserve session-c \
        --role session-a=train --role session-b=validation --manifest runs/intake/manifest.json
    python -m phantomguard.commands.intake verify --source D:/recordings --manifest runs/intake/manifest.json

``scan`` writes a new manifest (an existing one is read first so reservations stay sticky and changed
bytes under a known name fail). ``verify`` re-hashes every listed file. Nothing under --source is written.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from phantomguard.config import add_path_arguments, config_from_args, paths
from phantomguard.eval.intake import FLAT_GROUP, IntakeError, load_manifest, scan, today, verify


def _roles(values: list[str]) -> dict[str, str]:
    out = {}
    for v in values or ():
        group, sep, role = v.partition("=")
        if not sep:
            raise IntakeError(f"--role expects GROUP=ROLE, got {v!r}")
        out[group] = role
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("scan", "verify"))
    ap.add_argument("--source", type=Path, required=True, help="directory of session sub-directories (read-only)")
    ap.add_argument("--manifest", type=Path, help="manifest path (default <output>/intake/manifest.json)")
    ap.add_argument("--role", action="append", default=[], help="GROUP=ROLE (development|train|validation|calibration)")
    ap.add_argument("--reserve", action="append", default=[], help="GROUP to keep uninspected as the final test")
    ap.add_argument("--flat-group", default=FLAT_GROUP, help="group name for CSVs directly under --source")
    add_path_arguments(ap)
    args = ap.parse_args(argv)
    cfg = config_from_args(args)
    manifest_path = args.manifest or paths(cfg).output / "intake" / "manifest.json"
    try:
        if args.action == "verify":
            problems = verify(load_manifest(manifest_path), args.source, args.flat_group)
            print(json.dumps({"ok": not problems, "manifest": str(manifest_path), "problems": problems}, indent=2))
            return 0 if not problems else 1
        previous = load_manifest(manifest_path) if manifest_path.is_file() else None
        result = scan(args.source, roles=_roles(args.role), reserve=set(args.reserve), previous=previous,
                      tick_seconds=float(cfg["units"]["tick_seconds"]),
                      moving_mps=float(cfg["motion"]["moving_threshold_mps"]), flat_group=args.flat_group, now=today())
    except IntakeError as exc:
        print(f"intake: {exc}", file=sys.stderr)
        return 2
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {g: {"role": v["role"], "reserved": v["reserved"], "files": len(v["files"])} for g, v in result["groups"].items()}
    print(json.dumps({"manifest": str(manifest_path), "manifest_id": result["manifest_id"], "groups": summary,
                      "notes": result["notes"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
