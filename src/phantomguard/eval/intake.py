"""Read-only, hashed intake of new recording sessions into a versioned manifest.

Why: every recording available today comes from one session (four consecutive segments), so no result
here can claim to generalise to a new day, room or mounting. New recordings must arrive with their
identity fixed *before* anyone looks at them, and one session group must stay unopened until the
detector policy is frozen, so that a final test exists that nobody tuned on.

Layout: ``<source>/<session-group>/*.csv`` (one sub-directory per recording session); CSV files lying
directly in ``<source>`` form one group named by ``flat_group``. Group names are labels chosen by the
person recording; they are never detector inputs.

Guarantees:
* sources are opened read-only and never modified, moved or normalised;
* every file is identified by SHA-256 and byte size; the manifest ID hashes only identities and roles;
* a *reserved* group is hashed and its CSV header line checked, nothing else is read (``inspected:
  false``), and a later scan cannot silently un-reserve it;
* inspected groups get structural statistics only (counts, integrity, counter continuity, duration),
  the same facts docs/data.md lists for the existing recordings - not detector scores;
* identical files in two groups are an error; overlapping counter ranges across groups are reported
  (they suggest the "groups" are segments of one session, which matters for independence claims).
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
from pathlib import Path

from phantomguard.workspace import CSV_COLUMNS, digest

MANIFEST_SCHEMA = "phantomguard.intake/1"
ROLES = ("development", "train", "validation", "calibration", "final_test")
FLAT_GROUP = "flat"


class IntakeError(ValueError):
    pass


def _header_ok(path: Path) -> bool:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return next(csv.reader(f), None) == CSV_COLUMNS


def discover(source: Path, flat_group: str = FLAT_GROUP) -> dict[str, list[Path]]:
    """group -> sorted CSV paths. Only one directory level; deeper nesting is reported, not walked."""
    source = Path(source)
    if not source.is_dir():
        raise IntakeError(f"{source}: not a directory")
    groups: dict[str, list[Path]] = {}
    flat = sorted(p for p in source.glob("*.csv") if p.is_file())
    if flat:
        groups[flat_group] = flat
    for d in sorted(p for p in source.iterdir() if p.is_dir()):
        files = sorted(p for p in d.glob("*.csv") if p.is_file())
        if d.name in groups:
            raise IntakeError(f"group name {d.name!r} collides with the flat group; pass a different --flat-group")
        if files:
            groups[d.name] = files
    return groups


def structural_summary(path: Path, tick_seconds: float, moving_mps: float) -> dict:
    """Streaming structural facts of one decoder-v2 CSV (no detector, no learned model)."""
    rows = cycles = 0
    last_cycle = None
    counters: list[int] = []
    ts_first = ts_last = None
    bad_len = bad_status = count_mismatch = dup_slot = moving = 0
    slots: set = set()
    with path.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows += 1
            cyc = r["cycle_num"]
            if cyc != last_cycle:
                cycles += 1
                last_cycle = cyc
                slots = set()
                counters.append(int(r["meas_counter"]))
                if r["obj_count_header"] != r["obj_count_actual"]:
                    count_mismatch += 1
                if r["sync_status"].lower() != "0x01":
                    bad_status += 1
                ts = float(r["sync_timestamp"])
                ts_first = ts if ts_first is None else ts_first
                ts_last = ts
            if r["raw_len"] != "8":
                bad_len += 1
            if r["slot"] in slots:
                dup_slot += 1
            slots.add(r["slot"])
            try:
                moving += float(r["speed_mps"]) >= moving_mps
            except ValueError:
                pass
    steps = [b - a for a, b in zip(counters, counters[1:])]
    gaps = sum(1 for s in steps if s != 1 and s != -65535)      # 16-bit wrap counts as continuous
    duration = (ts_last - ts_first) * tick_seconds if ts_first is not None else 0.0
    return {"rows": rows, "cycles": cycles,
            "meas_counter": {"first": counters[0] if counters else None, "last": counters[-1] if counters else None,
                             "discontinuities": gaps},
            "duration_seconds_provisional": round(duration, 3),
            "integrity": {"raw_len_not_8": bad_len, "header_count_mismatch_cycles": count_mismatch,
                          "status_not_ok_cycles": bad_status, "duplicate_slot_rows": dup_slot},
            "moving_row_fraction": round(moving / rows, 5) if rows else None,
            "units_note": "duration uses the provisional tick_seconds from config; moving threshold is "
                          "motion.moving_threshold_mps"}


def _manifest_id(groups: dict) -> str:
    core = {g: {"role": v["role"], "reserved": v["reserved"],
                "files": sorted((f["file"], f["sha256"], f["bytes"]) for f in v["files"])}
            for g, v in groups.items()}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


def scan(source: Path, *, roles: dict[str, str] | None = None, reserve: set[str] | None = None,
         previous: dict | None = None, tick_seconds: float = 1e-4, moving_mps: float = 0.3,
         flat_group: str = FLAT_GROUP, now: str | None = None) -> dict:
    roles, reserve = dict(roles or {}), set(reserve or ())
    found = discover(source, flat_group)
    unknown = (set(roles) | reserve) - set(found)
    if unknown:
        raise IntakeError(f"groups not found under {source}: {sorted(unknown)}")
    bad_roles = {g: r for g, r in roles.items() if r not in ROLES}
    if bad_roles:
        raise IntakeError(f"unknown roles {bad_roles}; choose from {ROLES}")
    prev_groups = (previous or {}).get("groups", {})
    for g, v in prev_groups.items():
        if v.get("reserved"):
            if g not in found:
                raise IntakeError(f"reserved group {g!r} from the previous manifest is missing under {source}")
            reserve.add(g)        # a reservation is sticky; un-reserving is a deliberate edit of the manifest
    for g in reserve:
        if roles.get(g, "final_test") != "final_test":
            raise IntakeError(f"reserved group {g!r} can only have role final_test")
        roles[g] = "final_test"
    for g, r in roles.items():
        if r == "final_test" and g not in reserve:
            raise IntakeError(f"group {g!r}: role final_test requires --reserve (it must stay uninspected)")
    groups, by_hash, notes = {}, {}, []
    for g, files in found.items():
        reserved = g in reserve
        entries = []
        for p in files:
            if not _header_ok(p):
                raise IntakeError(f"{p}: not the decoder-v2 22-column schema; decode the capture first")
            e = {"file": p.name, "bytes": p.stat().st_size, "sha256": digest(p)}
            if e["sha256"] in by_hash:
                raise IntakeError(f"{p} is byte-identical to {by_hash[e['sha256']]}; a recording may belong to one group only")
            by_hash[e["sha256"]] = f"{g}/{p.name}"
            prev = {f["file"]: f for f in prev_groups.get(g, {}).get("files", [])}.get(p.name)
            if prev and prev["sha256"] != e["sha256"]:
                raise IntakeError(f"{g}/{p.name} changed since the previous manifest ({prev['sha256'][:12]} -> "
                                  f"{e['sha256'][:12]}); recordings are immutable, add it under a new name")
            if not reserved:
                e["structure"] = structural_summary(p, tick_seconds, moving_mps)
            entries.append(e)
        groups[g] = {"role": roles.get(g, "development"), "reserved": reserved, "inspected": not reserved,
                     "session_id": hashlib.sha256("".join(sorted(e["sha256"] for e in entries)).encode()).hexdigest()[:16],
                     "files": entries}
        if reserved:
            groups[g]["reservation"] = {"rule": "hash and header only; open after the detector policy and artifacts are "
                                                "frozen, run the final matrix once, report whatever it shows",
                                        "since": (prev_groups.get(g, {}).get("reservation") or {}).get("since") or now}
    ranges = [(g, e["file"], e["structure"]["meas_counter"]["first"], e["structure"]["meas_counter"]["last"])
              for g, v in groups.items() for e in v["files"] if "structure" in e and e["structure"]["cycles"]]
    for i, (g1, f1, a1, b1) in enumerate(ranges):
        for g2, f2, a2, b2 in ranges[i + 1:]:
            if g1 != g2 and max(a1, a2) <= min(b1, b2):
                notes.append(f"meas_counter ranges of {g1}/{f1} and {g2}/{f2} overlap; check they are separate sessions")
    for g, v in groups.items():
        if not v["reserved"]:
            total = sum(e["structure"]["duration_seconds_provisional"] for e in v["files"])
            if total < 30 * 60:
                notes.append(f"group {g}: {total / 60:.1f} provisional minutes of recording; 30-60 min of clean "
                             f"recording per new session is recommended for alert-rate estimates")
    return {"schema": MANIFEST_SCHEMA, "created": now, "source": str(source), "manifest_id": _manifest_id(groups),
            "groups": groups, "notes": notes,
            "contract": {"labels": "group names, roles and file names are bookkeeping; never detector features",
                         "immutability": "sources are never written; changed bytes under an existing name are an error"}}


def load_manifest(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != MANIFEST_SCHEMA:
        raise IntakeError(f"{path}: manifest schema {data.get('schema')!r} is not supported by this tool "
                          f"(expects {MANIFEST_SCHEMA}); use the matching tool version")
    if _manifest_id(data["groups"]) != data["manifest_id"]:
        raise IntakeError(f"{path}: manifest_id does not match its contents (edited by hand?)")
    return data


def verify(manifest: dict, source: Path, flat_group: str = FLAT_GROUP) -> list[str]:
    """Re-hash every listed file; returns a list of problems (empty = verified)."""
    problems = []
    for g, v in manifest["groups"].items():
        base = Path(source) if g == flat_group else Path(source) / g
        for e in v["files"]:
            p = base / e["file"]
            if not p.is_file():
                problems.append(f"missing {g}/{e['file']}")
            elif p.stat().st_size != e["bytes"] or digest(p) != e["sha256"]:
                problems.append(f"changed {g}/{e['file']}")
    return problems


def today() -> str:
    return _dt.date.today().isoformat()
