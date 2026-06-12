"""Calibrate All wizard — the pure state machine (no dpg anywhere).

DECOMPOSITION_PLAN post-Phase-3 follow-up: one guided operator flow chaining
Calib1 (scene/lighting) → report card → Calib2 (dancers) → pool review →
apply → save, built **only** on the Phase 3 command/event vocabulary. The two
calibration engines stay separate (different stage directions, cadence, trust
models) — this merges nothing but the UX.

This module is deliberately renderer-free so it is:
- headless-testable (tests/test_wizard_state.py), and
- reusable verbatim by the Phase 5 tablet client (a websocket renderer would
  serialize the same returned commands and feed the same events).

Contract:
- Operator actions (``start_scene``, ``apply_pool``, ...) mutate ``step`` and
  return the list of ``runtime.api`` commands the renderer must submit.
- Seam events are fed via ``on_progress`` / ``on_report_card`` /
  ``on_pool_changed``; the report/pool intakes return True when the wizard
  consumed the event (the adapter falls back to the classic dialogs on False,
  so both entry points — wizard and plain buttons — keep working).
- Run-end detection: the flows publish ``CalibProgress(None)`` both on success
  (immediately followed by the report card / pool event in the same drain) and
  on cancellation/stall (followed by nothing). ``on_progress(None)`` therefore
  moves RUNNING → *_ENDED, and the success event — arriving before the next
  render — moves *_ENDED onward; if nothing follows, the renderer shows the
  retry affordance. No timers needed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from runtime import api

# Steps (string constants; trivially JSON-transportable for the tablet client)
INTRO = "intro"
SCENE_RUNNING = "scene_running"
SCENE_ENDED = "scene_ended"          # run ended, no report card (cancel/stall)
SCENE_REPORT = "scene_report"
DANCERS_READY = "dancers_ready"
DANCERS_RUNNING = "dancers_running"
DANCERS_ENDED = "dancers_ended"      # run ended, no pool event (cancel/stall)
POOL_REVIEW = "pool_review"
APPLIED = "applied"
CLOSED = "closed"


class WizardState:
    """State + transitions for the Calibrate All flow."""

    def __init__(self) -> None:
        self.step: str = CLOSED
        self.progress_text: Optional[str] = None
        self.scene_summary: Optional[str] = None
        self.pool_rows: List[Dict[str, Any]] = []
        self.pool_proposal: str = ""
        self.applied_summary: Optional[str] = None
        self.apply_requested: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.step != CLOSED

    def open(self) -> List[api.Command]:
        """(Re)open the wizard at the intro step. No runtime effect."""
        self.__init__()
        self.step = INTRO
        return []

    def close(self) -> List[api.Command]:
        """Close from any step; cancels a run still collecting."""
        cmds: List[api.Command] = []
        if self.step == SCENE_RUNNING:
            cmds.append(api.StartCalibration())   # toggle-cancel
        elif self.step == DANCERS_RUNNING:
            cmds.append(api.StartDancersRun())    # toggle-cancel
        self.step = CLOSED
        return cmds

    # ------------------------------------------------------------------
    # Operator actions (renderer buttons) → commands to submit
    # ------------------------------------------------------------------
    def start_scene(self) -> List[api.Command]:
        if self.step not in (INTRO, SCENE_ENDED):
            return []
        self.step = SCENE_RUNNING
        self.progress_text = None
        return [api.StartCalibration()]

    def cancel_scene_run(self) -> List[api.Command]:
        if self.step != SCENE_RUNNING:
            return []
        self.step = INTRO
        return [api.StartCalibration()]          # toggle-cancel

    def continue_to_dancers(self) -> List[api.Command]:
        if self.step != SCENE_REPORT:
            return []
        self.step = DANCERS_READY
        return []

    def start_dancers(self) -> List[api.Command]:
        if self.step not in (DANCERS_READY, DANCERS_ENDED):
            return []
        self.step = DANCERS_RUNNING
        self.progress_text = None
        return [api.StartDancersRun()]

    def cancel_dancers_run(self) -> List[api.Command]:
        if self.step != DANCERS_RUNNING:
            return []
        self.step = DANCERS_READY
        return [api.StartDancersRun()]           # toggle-cancel

    def add_another_run(self) -> List[api.Command]:
        """Pool is accumulative across runs — loop back for another costume /
        position / recording."""
        if self.step != POOL_REVIEW:
            return []
        self.step = DANCERS_READY
        return []

    def apply_pool(self, selected_paths: List[str]) -> List[api.Command]:
        if self.step != POOL_REVIEW:
            return []
        # Stay on POOL_REVIEW until the applied report card arrives — a
        # rejected proposal (toast) leaves the operator exactly where they
        # can change the selection.
        self.apply_requested = True
        return [api.ApplyCalib2(list(selected_paths))]

    def skip_apply(self) -> List[api.Command]:
        """Finish without applying the pool (Calib1 results stay applied)."""
        if self.step != POOL_REVIEW:
            return []
        self.applied_summary = None
        self.step = APPLIED
        return []

    def save_project(self) -> List[api.Command]:
        """'Save to project' = a normal timestamped save (ROADMAP bug #6)."""
        if self.step not in (SCENE_REPORT, APPLIED):
            return []
        self.step = CLOSED
        return [api.SaveConfig()]

    def keep_session(self) -> List[api.Command]:
        if self.step not in (SCENE_REPORT, APPLIED):
            return []
        self.step = CLOSED
        return []

    # ------------------------------------------------------------------
    # Seam-event intake (adapter routes; bool = consumed)
    # ------------------------------------------------------------------
    def on_progress(self, text: Optional[str]) -> None:
        if not self.active:
            return
        self.progress_text = text
        if text is None:
            # Run ended: success events follow within the same drain (before
            # the next render); otherwise the *_ENDED retry UI shows.
            if self.step == SCENE_RUNNING:
                self.step = SCENE_ENDED
            elif self.step == DANCERS_RUNNING:
                self.step = DANCERS_ENDED

    def on_report_card(self, summary: str) -> bool:
        if self.step in (SCENE_RUNNING, SCENE_ENDED):
            self.scene_summary = summary
            self.step = SCENE_REPORT
            return True
        if self.step == POOL_REVIEW and self.apply_requested:
            self.apply_requested = False
            self.applied_summary = summary
            self.step = APPLIED
            return True
        return False

    def on_pool_changed(self, rows: List[Dict[str, Any]], proposal: str) -> bool:
        if self.step in (DANCERS_RUNNING, DANCERS_ENDED):
            self.pool_rows = list(rows)
            self.pool_proposal = proposal
            self.step = POOL_REVIEW
            return True
        if self.step == POOL_REVIEW:
            # e.g. a re-aggregate while reviewing — refresh in place.
            self.pool_rows = list(rows)
            self.pool_proposal = proposal
            return True
        return False

    # ------------------------------------------------------------------
    # Renderer helpers
    # ------------------------------------------------------------------
    def default_selection(self) -> List[str]:
        """Pre-check non-stale runs (stale = framing changed since the run)."""
        return [r["path"] for r in self.pool_rows if not r.get("stale", False)]
