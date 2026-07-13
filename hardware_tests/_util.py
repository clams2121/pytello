"""Shared helpers for the manual hardware test scripts in this directory.

Not a pytest suite -- these scripts are meant to be run one at a time,
by a human, against a real drone. Import this module the same way the
numbered scripts do (``python hardware_tests/0N_....py`` from the repo
root, or after ``pip install -e .``).
"""

from __future__ import annotations

import sys


def checklist(*items: str) -> None:
    print("Pre-flight checklist:")
    for item in items:
        print(f"  [ ] {item}")
    print()
    input("Press Enter once every item above is satisfied...")


def report(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f" -- {detail}"
    print(line)


def fail(message: str) -> int:
    print(f"ABORTED: {message}", file=sys.stderr)
    return 1
