#!/usr/bin/env python3
"""Compatibility entry point; prefer phantomguard CLI."""
import sys
from phantomguard.commands import replay_demo as _command
if __name__ == "__main__":
    raise SystemExit(_command.main())
else:
    sys.modules[__name__] = _command
    globals().update(_command.__dict__)
