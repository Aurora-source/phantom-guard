# New recordings: intake, roles and a reserved final test

All results so far come from **one recording session**: four consecutive segments (about 10.6
provisional minutes in total, `meas_counter` 43529-62623, see [data](data.md)). Thresholds are chosen
by out-of-recording cross-validation inside that session, so every clean-alert rate in the reports is
an estimate for *this room, mounting and day*. Nothing here licenses a claim about another session.
The only way to measure that is new, independent recordings, kept apart from tuning.

## What to record (recommendation, owner's decision)

* **30-60 minutes of clean recording per new session.** At a target below 1 persistent alert per minute,
  observing 0 episodes in 10 minutes still allows a true rate of about 0.37/min (95% Poisson bound);
  30 minutes brings that to about 0.12/min and 60 minutes to about 0.06/min. Episodes cluster, so the
  real uncertainty is larger than these bounds.
* **Several sessions, not one long one**: a different day, the sensor re-mounted, and ideally a
  different room. Session-to-session change (clutter layout, multipath, people) is what the current data
  cannot show.
* **The same scenario mix** as today (empty room, one person front/back, side to side, several people),
  plus whatever the deployment will meet (doors, carts, reflective surfaces). Write the scenario in the
  folder name or a note; it is bookkeeping, never a detector input.
* Keep the decoder-v2 CSV format unchanged (`tools/decode.py`); raw captures stay untouched.
* Never synthesise, splice or edit "clean" recordings. A recording that went wrong is set aside and
  reported, not repaired.

## Intake workflow

Put each session in its own folder:

```text
D:/recordings/
  2026-11-02-lab-a/   emptyRoom.csv  onePerson....csv  ...
  2026-11-09-lab-a/   ...
  2026-11-16-lab-b/   ...        <- reserve this one
```

Scan, assign roles and reserve one group **before anyone opens it**:

```bash
python -m phantomguard.commands.intake scan --source D:/recordings \
    --role 2026-11-02-lab-a=train --role 2026-11-09-lab-a=validation \
    --reserve 2026-11-16-lab-b --manifest runs/intake/manifest.json
python -m phantomguard.commands.intake verify --source D:/recordings --manifest runs/intake/manifest.json
```

* Sources are opened read-only; nothing under `--source` is written, moved or normalised.
* Every file is identified by SHA-256 and size; the manifest ID hashes identities and roles only.
* A **reserved** group is hashed and its header line checked, nothing else (`inspected: false`). Later
  scans keep it reserved automatically; un-reserving is a deliberate manifest edit, recorded in
  `docs/decisions.md` with the frozen policy/commit it is opened against.
* Inspected groups get structural statistics only (rows, cycles, counter continuity, integrity counts,
  provisional duration, moving fraction) - the same facts as [data](data.md), never detector scores.
* The scan refuses: changed bytes under a known name, the same file in two groups, a non-decoder-v2
  header, and `final_test` without `--reserve`. Overlapping counter ranges between groups are reported
  (they suggest segments of one session, which matters for independence claims).

## Using a reserved group

Open it exactly once, after the detector policy and artifacts are frozen and committed: run the
clean evaluation and the fixed-seed attack matrix on it, publish whatever it shows (including a
missed clean-rate target), and do not change thresholds in response. A second look after a change
needs a new reserved group.

The current four recordings cannot serve as an uninspected group: all of them were inspected while
building the detector. They are labelled `development` by the intake scan.
