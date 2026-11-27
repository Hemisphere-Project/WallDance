"""H2c (#t-060): draw the family's health lines (tests/test_h2c_cause_name.LINES) and the 27/11 line in the real GUI.

A script (never imported by pytest: it opens a DearPyGui viewport, minimized), on tests/h2_line_draw.py's harness --
the GUI's own painter, the real theme, ImGui's own text metrics -- with these cases in place of its own:

    python tests/h2c_family_draw.py --src <code root>/application/src --scale 1.5 --widths 1280,1600,1920,1988 --out f.json
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import h2_line_draw as D  # noqa: E402

_STATE = {"FAIL": "fail", "WARN": "warn", "NOT CHECKED": "skip", "OK": "ok"}


def build_cases(tests_dir: str):
    sys.path.insert(0, tests_dir)
    import test_h2c_cause_name as F
    cases = [("health", label, _STATE[text.split(" - ", 1)[0]], text, ins) for label, text, _name, ins in F.LINES]
    cases.append(("health", "27/11", "fail", F.LINE_2711, F._after_lead(F.RW.PROMPT_RUN)))
    return cases


if __name__ == "__main__":
    D.build_cases = build_cases
    sys.exit(D.main())
