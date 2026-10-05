from __future__ import annotations

import os

# Path overrides from the caller's shell must never reach tests: a fixture that writes into the
# configured raw directory once overwrote an external dataset copy this way (docs/decisions.md,
# 2026-10-05). Tests that need overrides set them with monkeypatch.
for _k in [k for k in os.environ if k.startswith("PHANTOMGUARD_")]:
    os.environ.pop(_k)

import csv
from pathlib import Path

import pytest

from phantomguard.config import load_config, raw_path

CFG = load_config()
FILES = CFG["data"]["files"]


def _have_data() -> bool:
    return all(raw_path(CFG, f).exists() for f in FILES)


needs_data = pytest.mark.skipif(not _have_data(), reason="data/raw CSVs not present")


@pytest.fixture(scope="session")
def cfg():
    return CFG


def iter_rows(name: str):
    with open(raw_path(CFG, name), newline="") as f:
        yield from csv.DictReader(f)
