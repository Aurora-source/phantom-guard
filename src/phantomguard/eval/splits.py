"""Fixed data splits. Do not change these to tune results (CLAUDE.md hard rule 5).

(a) time-block: per file, first 60% of cycles train, next 20% validation, last 20% test.
(b) leave-one-scenario-out: train = first 80% of each of three files, validation = their last 20%,
    test = the whole fourth file.
"""

from __future__ import annotations

from dataclasses import dataclass

from phantomguard.config import load_config, raw_path
from phantomguard.io.replay import load_recorded_cycles

Range = tuple[int, int]


@dataclass(frozen=True)
class Segment:
    file: str
    lo: int
    hi: int

    @property
    def n(self) -> int:
        return self.hi - self.lo


def n_cycles(cfg: dict, name: str) -> int:
    cycles, _ = load_recorded_cycles(str(raw_path(cfg, name)))
    return len(cycles)


def time_block(n: int, train_frac: float, val_frac: float) -> dict[str, Range]:
    a = int(n * train_frac)
    b = int(n * (train_frac + val_frac))
    return {"train": (0, a), "val": (a, b), "test": (b, n)}


def time_block_segments(cfg: dict | None = None) -> dict[str, list[Segment]]:
    cfg = cfg or load_config()
    tf, vf = cfg["splits"]["train_frac"], cfg["splits"]["val_frac"]
    out: dict[str, list[Segment]] = {"train": [], "val": [], "test": []}
    for f in cfg["data"]["files"]:
        for part, (lo, hi) in time_block(n_cycles(cfg, f), tf, vf).items():
            out[part].append(Segment(f, lo, hi))
    return out


def loso_folds(cfg: dict | None = None) -> dict[str, dict[str, list[Segment]]]:
    cfg = cfg or load_config()
    tf, vf = cfg["splits"]["train_frac"], cfg["splits"]["val_frac"]
    cut = tf + vf
    folds = {}
    files = cfg["data"]["files"]
    for held in files:
        tr, va = [], []
        for f in files:
            if f == held:
                continue
            n = n_cycles(cfg, f)
            tr.append(Segment(f, 0, int(n * cut)))
            va.append(Segment(f, int(n * cut), n))
        folds[held] = {"train": tr, "val": va, "test": [Segment(held, 0, n_cycles(cfg, held))]}
    return folds
