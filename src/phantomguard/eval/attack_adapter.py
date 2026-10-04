"""Optional Phase 2 integration, owned by the evaluation/demo layers.

This module never generates attacks. The default provider is the contributors'
``scenarios.create_attacker`` plus ``injector.AttackedSource``. An explicit
``module:function`` provider may instead adapt an alternative contributor API;
it accepts source, cfg, baseline and the keyword context documented below and
returns a FrameSource that writes the required label sidecar.
"""

from __future__ import annotations

import importlib
from copy import deepcopy
from pathlib import Path
from typing import Iterable, Iterator

from phantomguard.eval.splits import Segment
from phantomguard.frames import Frame

ATTACK_TYPES = ("T1", "T2", "T3", "T4")
LEVEL_NAMES = ("A0", "A1", "A2", "A3", "A4")
REPLAY_PROVENANCE = ("earlier_stream", "training", "unseen")


class AttackUnavailable(RuntimeError):
    pass


class UnsupportedAttack(ValueError):
    pass


def support_reason(attack_type: str, level: str, motion_case: str = "moving") -> str | None:
    if attack_type not in ATTACK_TYPES or level not in LEVEL_NAMES:
        return "unknown attack type or level"
    if motion_case not in {"static", "moving"}:
        return "motion_case must be static or moving"
    if attack_type == "T3" and level not in {"A3", "A4"}:
        return "T3 recorded-track replay is documented as inherently A3/A4"
    if attack_type in {"T3", "T4"} and motion_case == "static":
        return f"{attack_type} requires a real moving track"
    return None


def _provider(spec: str):
    try:
        module, name = spec.split(":", 1)
        factory = getattr(importlib.import_module(module), name)
    except (ValueError, ImportError, AttributeError) as exc:
        raise AttackUnavailable(f"attack provider {spec!r} unavailable: {exc}") from exc
    if not callable(factory):
        raise AttackUnavailable(f"attack provider {spec!r} is not callable")
    return factory


def check_available(provider: str | None = None) -> str:
    if provider:
        _provider(provider)
        return provider
    try:
        scenarios = importlib.import_module("phantomguard.attack.scenarios")
        injector = importlib.import_module("phantomguard.attack.injector")
    except ImportError as exc:
        raise AttackUnavailable("Phase 2 unavailable: attack/scenarios.py and attack/injector.py must provide "
                                "create_attacker and AttackedSource; see docs/phase2-handoff.md") from exc
    if not callable(getattr(scenarios, "create_attacker", None)) or not callable(getattr(injector, "AttackedSource", None)):
        raise AttackUnavailable("Phase 2 API unavailable: expected scenarios.create_attacker and injector.AttackedSource; "
                                "use --provider module:function to adapt the contributor API")
    return "phantomguard.attack.scenarios:create_attacker + injector.AttackedSource"


class _CheckedSource:
    def __init__(self, source: Iterable[Frame], provider_name: str, labels_path: Path | None):
        self.source = source
        self.provider_name = provider_name
        self.labels_path = labels_path

    def __iter__(self) -> Iterator[Frame]:
        for index, frame in enumerate(self.source):
            if not isinstance(frame, Frame):
                raise TypeError(f"attacker emitted {type(frame).__name__} at frame {index}; "
                                "expected ordinary Frame(can_id, data, timestamp_ticks), labels in sidecar")
            yield frame


def attack_source(source: Iterable[Frame], cfg: dict, baseline: dict, *, attack_type: str, level: str,
                  seed: int, train_segments: Iterable[Segment] = (), replay_provenance: str = "training",
                  unseen_segments: Iterable[Segment] = (), labels_path: Path | str | None = None,
                  motion_case: str = "moving", replay_variant: str = "exact", run_index: int = 0,
                  provider: str | None = None) -> Iterable[Frame]:
    """Wrap a source using Phase 2; training-library construction stays outside this API.

    A2 repairs counts/counters/slot uniqueness. A3+ knows range order and burst
    contiguity. T4 A0/A1 append a same-slot object; A2+ overwrite in place. These
    are provider responsibilities, not stricter assertions in this adapter.
    ``seed`` is the authoritative effective seed (already derived from configured
    seed and repetition by the evaluator). ``run_index`` is metadata only; do not
    derive the seed a second time. Record the effective seed in provider metadata.
    """
    reason = support_reason(attack_type, level, motion_case)
    if reason:
        raise UnsupportedAttack(reason)
    if replay_provenance not in REPLAY_PROVENANCE:
        raise ValueError(f"replay_provenance must be one of {REPLAY_PROVENANCE}")
    if replay_variant not in {"exact", "translated"}:
        raise ValueError("replay_variant must be exact or translated")
    train, unseen = tuple(train_segments), tuple(unseen_segments)
    if attack_type == "T3" and replay_provenance == "unseen":
        if not unseen:
            raise UnsupportedAttack("unseen replay requires explicit evaluation-recording segments")
        if any(a.file == b.file and max(a.lo, b.lo) < min(a.hi, b.hi) for a in unseen for b in train):
            raise ValueError("unseen replay segments overlap defender training segments")
    path = Path(labels_path) if labels_path is not None else None
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
    provider_name = check_available(provider)
    # Contributor code must not alter defender thresholds/configuration by aliasing.
    attacker_cfg, attacker_baseline = deepcopy(cfg), deepcopy(baseline)
    context = dict(attack_type=attack_type, level=level, seed=int(seed), train_segments=train,
                   replay_provenance=replay_provenance, unseen_segments=unseen, labels_path=path,
                   motion_case=motion_case, replay_variant=replay_variant, run_index=int(run_index))
    if provider:
        result = _provider(provider)(source, attacker_cfg, attacker_baseline, **context)
    else:
        scenarios = importlib.import_module("phantomguard.attack.scenarios")
        injector = importlib.import_module("phantomguard.attack.injector")
        factory_context = {k: v for k, v in context.items() if k != "labels_path"}
        attacker = scenarios.create_attacker(attacker_cfg, attacker_baseline, **factory_context)
        result = injector.AttackedSource(source, attacker, labels_path=path)
    return _CheckedSource(result, provider_name, path)
