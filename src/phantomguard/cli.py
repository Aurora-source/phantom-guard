"""Installed CLI. Preparation is offline; serve never imports training services."""
from __future__ import annotations
import argparse
import importlib
import json
import os
import sys


def main(argv=None) -> int:
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(key, "1")
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"baseline": "learn_baseline", "train": "train", "calibrate": "calibrate",
                "clean-eval": "run_clean_eval", "attack-eval": "run_attack_eval", "replay": "replay_demo"}
    if argv and argv[0] in commands:
        try:
            return importlib.import_module("phantomguard.commands." + commands[argv[0]]).main(argv[1:]) or 0
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            print(f"{argv[0]} failed: {exc}", file=sys.stderr)
            if isinstance(exc, ImportError):
                print('Install offline extras: python -m pip install ".[training,viewer,evaluation]" (CPU torch instructions in docs/setup.md)', file=sys.stderr)
            return 2
    from phantomguard.config import add_path_arguments, config_from_args
    parser = argparse.ArgumentParser(prog="phantomguard", description="Recorded SR75 replay and simulated CAN attacks; CPU prototype")
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "import-data", "doctor", "bundle", "verify-bundle", "restore-bundle", "serve"):
        p = subs.add_parser(name)
        add_path_arguments(p)
        if name == "import-data":
            p.add_argument("--source", required=True, help="directory containing the four expected CSVs; never modified")
        if name == "doctor":
            p.add_argument("--full", action="store_true", help="all LOSO folds and offline IF comparison artifacts")
        if name == "bundle":
            p.add_argument("--archive", help="archive path (default deployment-bundles/phantomguard-SHA.zip)")
        if name in {"verify-bundle", "restore-bundle"}:
            p.add_argument("--archive", required=True)
        if name == "serve":
            p.add_argument("--host", default=os.environ.get("PHANTOMGUARD_HOST", "127.0.0.1"))
            p.add_argument("--port", type=int, default=int(os.environ.get("PHANTOMGUARD_PORT", "8765")))
            p.add_argument("--workers", type=int, default=int(os.environ.get("PHANTOMGUARD_WORKERS", "2")))
            p.add_argument("--max-cycles", type=int, default=int(os.environ.get("PHANTOMGUARD_MAX_CYCLES", "1200")))
            p.add_argument("--job-seconds", type=int, default=int(os.environ.get("PHANTOMGUARD_JOB_SECONDS", "120")))
            p.add_argument("--max-queued", type=int, default=int(os.environ.get("PHANTOMGUARD_MAX_QUEUED", "4")))
            p.add_argument("--max-jobs", type=int, default=int(os.environ.get("PHANTOMGUARD_MAX_JOBS", "8")))
            p.add_argument("--max-sessions", type=int, default=int(os.environ.get("PHANTOMGUARD_MAX_SESSIONS", "16")))
            p.add_argument("--ttl", type=int, default=int(os.environ.get("PHANTOMGUARD_TTL", "600")))
            p.add_argument("--output-mib", type=int, default=int(os.environ.get("PHANTOMGUARD_OUTPUT_MIB", "128")))
            p.add_argument("--job-cooldown", type=float, default=float(os.environ.get("PHANTOMGUARD_JOB_COOLDOWN", "0")))
    for name in commands:
        subs.add_parser(name, help="see phantomguard " + name + " --help")
    args = parser.parse_args(argv)
    try:
        cfg = config_from_args(args)
        if args.command == "serve":
            from phantomguard.web.api import serve
            serve(cfg, host=args.host, port=args.port, workers=args.workers, max_cycles=args.max_cycles, job_seconds=args.job_seconds,
                  max_queued=args.max_queued,max_jobs=args.max_jobs,max_sessions=args.max_sessions,ttl=args.ttl,output_mib=args.output_mib,
                  job_cooldown=args.job_cooldown)
            return 0
        from phantomguard import workspace
        if args.command == "init":
            result = workspace.initialize(cfg)
        elif args.command == "import-data":
            result = workspace.import_data(cfg, args.source)
        elif args.command == "doctor":
            result = workspace.doctor(cfg, full=args.full)
        else:
            from phantomguard import bundle
            result = {"bundle": bundle.build(cfg, args.archive)} if args.command == "bundle" else (
                bundle.restore(cfg, args.archive) if args.command == "restore-bundle" else bundle.verify(args.archive))
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0 if result.get("ok", True) else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
