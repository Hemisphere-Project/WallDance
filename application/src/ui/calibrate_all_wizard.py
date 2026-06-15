"""Calibrate All wizard — the DPG renderer over ui/wizard_state.py.

One modal window whose content swaps per wizard step. All runtime effects go
through ``submit`` (the RuntimeAPI queue) using the commands returned by the
state machine; all runtime feedback arrives as seam events routed here by the
adapter (``on_progress`` / ``on_report_card`` / ``on_pool_changed``). The
window itself is GUI-local state — opening it has no runtime effect, exactly
like the future tablet client opening its own wizard page.
"""
from __future__ import annotations

from typing import Callable, List, Optional

import dearpygui.dearpygui as dpg

from gui_builder import scaled
from gui_constants import TEXT_DIM, TEXT_MUTED, TEXT_NORMAL, WARN_ORANGE
from ui import wizard_state as ws
from ui.wizard_state import WizardState

TAG = "calibrate_all_modal"
_BODY = "calall_body"
_PROGRESS = "calall_progress"


class CalibrateAllWizard:
    """Renders the wizard modal and bridges it to the command/event seam."""

    def __init__(self, gui, submit: Callable) -> None:
        self._gui = gui            # WallDanceGUI: _center_modal / recentering
        self._submit = submit
        self.state = WizardState()
        self._checkbox_tags: List[tuple] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.state.active

    def open(self) -> None:
        self._run(self.state.open)
        w, h = scaled(580), scaled(460)
        if dpg.does_item_exist(TAG):
            dpg.delete_item(TAG)
        with dpg.window(
            label="Calibrate All",
            modal=True,
            tag=TAG,
            width=w,
            height=h,
            pos=self._gui._center_modal(TAG, w, h),
            no_resize=True,
            no_move=True,
            no_close=True,
            no_collapse=True,
        ):
            dpg.add_group(tag=_BODY)
        self._render()

    def _close(self) -> None:
        self._run(self.state.close)
        if dpg.does_item_exist(TAG):
            dpg.delete_item(TAG)

    # ------------------------------------------------------------------
    # Seam-event intake (routed by the adapter; bool = consumed)
    # ------------------------------------------------------------------
    def on_progress(self, text: Optional[str]) -> None:
        before = self.state.step
        self.state.on_progress(text)
        if not self.state.active or not dpg.does_item_exist(TAG):
            return
        if self.state.step != before:
            self._render()
        elif text and dpg.does_item_exist(_PROGRESS):
            dpg.set_value(_PROGRESS, text)

    def on_report_card(self, summary: str) -> bool:
        consumed = self.state.on_report_card(summary)
        if consumed:
            self._render()
        return consumed

    def on_pool_changed(self, rows, proposal: str) -> bool:
        consumed = self.state.on_pool_changed(rows, proposal)
        if consumed:
            self._render()
        return consumed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _run(self, action: Callable, *args) -> None:
        """Run a state action and submit whatever commands it returns."""
        for cmd in action(*args):
            self._submit(cmd)

    def _act(self, action: Callable, *args) -> None:
        self._run(action, *args)
        if self.state.active:
            self._render()
        elif dpg.does_item_exist(TAG):
            dpg.delete_item(TAG)

    def _render(self) -> None:
        if not dpg.does_item_exist(_BODY):
            return
        dpg.delete_item(_BODY, children_only=True)
        step = self.state.step
        build = {
            ws.INTRO: self._build_intro,
            ws.SCENE_RUNNING: self._build_scene_running,
            ws.SCENE_ENDED: self._build_scene_ended,
            ws.SCENE_REPORT: self._build_scene_report,
            ws.DANCERS_READY: self._build_dancers_ready,
            ws.DANCERS_RUNNING: self._build_dancers_running,
            ws.DANCERS_ENDED: self._build_dancers_ended,
            ws.POOL_REVIEW: self._build_pool_review,
            ws.APPLIED: self._build_applied,
        }.get(step)
        if build is not None:
            build()

    # --- step panels ----------------------------------------------------

    def _text(self, s: str, color=TEXT_NORMAL, wrap: int = 540):
        dpg.add_text(s, color=color, wrap=scaled(wrap), parent=_BODY)

    def _spacer(self, h: int = 8):
        dpg.add_spacer(height=scaled(h), parent=_BODY)

    def _buttons(self, *specs):
        with dpg.group(horizontal=True, parent=_BODY):
            first = True
            for label, cb, width in specs:
                if not first:
                    dpg.add_spacer(width=scaled(10))
                dpg.add_button(label=label, callback=cb, width=scaled(width))
                first = False

    def _build_intro(self) -> None:
        self._text("Guided calibration in two steps:")
        self._spacer(6)
        self._text("1.  SCENE & LIGHTING  -  clear the stage (rigging-time).\n"
                   "     Drives exposure/gain (IDS), seeds gamma/CLAHE, sweeps\n"
                   "     MOG2, captures the clean plate. (Exclusion masks are a\n"
                   "     manual paint step in phase ①.)", TEXT_MUTED)
        self._spacer(4)
        self._text("2.  DANCERS  -  1-4 dancers moving on stage (live or\n"
                   "     playback). Pools evidence runs: person height, image\n"
                   "     size, sensitivity seed.", TEXT_MUTED)
        self._spacer(10)
        self._text("Results apply to the session as you go; you choose what "
                   "to save at the end.", TEXT_DIM)
        self._spacer(12)
        self._buttons(("Start scene calibration",
                       lambda: self._act(self.state.start_scene), 190),
                      ("Close", self._close, 90))

    def _build_scene_running(self) -> None:
        self._text("Scene & lighting calibration running...")
        self._spacer(6)
        dpg.add_text(self.state.progress_text or "Starting...",
                     tag=_PROGRESS, color=(160, 200, 255), parent=_BODY)
        self._spacer(8)
        self._text("Keep the stage clear.", TEXT_MUTED)
        self._text("If nothing starts, check the message at the bottom "
                   "(model loaded? camera or playback running?).", TEXT_DIM)
        self._spacer(12)
        self._buttons(("Cancel run",
                       lambda: self._act(self.state.cancel_scene_run), 120),
                      ("Close", self._close, 90))

    def _build_scene_ended(self) -> None:
        self._text("The scene run ended without a result "
                   "(cancelled, or the source stalled).", WARN_ORANGE)
        self._spacer(12)
        self._buttons(("Start again",
                       lambda: self._act(self.state.start_scene), 130),
                      ("Close", self._close, 90))

    def _build_scene_report(self) -> None:
        self._text("Scene calibration - measured and applied to this session:")
        self._spacer(6)
        self._text(self.state.scene_summary or "", TEXT_MUTED)
        self._spacer(10)
        self._text("Next: dancer calibration. Get 1-4 dancers on stage "
                   "(or pick a recording with dancers).", TEXT_DIM)
        self._spacer(12)
        self._buttons(("Continue: dancers",
                       lambda: self._act(self.state.continue_to_dancers), 160),
                      ("Save & finish now",
                       lambda: self._act(self.state.save_project), 150),
                      ("Keep session only",
                       lambda: self._act(self.state.keep_session), 150))

    def _build_dancers_ready(self) -> None:
        self._text("Dancer calibration - evidence run")
        self._spacer(6)
        self._text("Have 1-4 dancers move around the stage; vary positions "
                   "and distance. Works live or during recording playback.",
                   TEXT_MUTED)
        self._spacer(12)
        self._buttons(("Start dancers run",
                       lambda: self._act(self.state.start_dancers), 160),
                      ("Close", self._close, 90))

    def _build_dancers_running(self) -> None:
        self._text("Dancer calibration running...")
        self._spacer(6)
        dpg.add_text(self.state.progress_text or "Starting...",
                     tag=_PROGRESS, color=(160, 200, 255), parent=_BODY)
        self._spacer(8)
        self._text("Keep the dancers moving.", TEXT_MUTED)
        self._spacer(12)
        self._buttons(("Cancel run",
                       lambda: self._act(self.state.cancel_dancers_run), 120),
                      ("Close", self._close, 90))

    def _build_dancers_ended(self) -> None:
        self._text("The dancers run ended without a result "
                   "(cancelled, or the source stalled).", WARN_ORANGE)
        self._spacer(12)
        self._buttons(("Start again",
                       lambda: self._act(self.state.start_dancers), 130),
                      ("Close", self._close, 90))

    def _build_pool_review(self) -> None:
        self._text("Runs in the pool (uncheck to exclude):")
        self._checkbox_tags = []
        defaults = set(self.state.default_selection())
        with dpg.child_window(height=scaled(130), border=True, parent=_BODY):
            for i, row in enumerate(self.state.pool_rows):
                tag = f"calall_run_chk_{i}"
                with dpg.group(horizontal=True):
                    dpg.add_checkbox(tag=tag,
                                     default_value=row["path"] in defaults)
                    if row.get("stale"):
                        dpg.add_text(row["label"] + "  [STALE - framing changed]",
                                     color=WARN_ORANGE)
                    else:
                        dpg.add_text(row["label"])
                self._checkbox_tags.append((tag, row["path"]))
            if not self.state.pool_rows:
                dpg.add_text("(empty - add a run)", color=TEXT_DIM)
        self._spacer(6)
        self._text("Pooled proposal (selected runs):")
        self._text(self.state.pool_proposal, TEXT_MUTED)
        self._spacer(6)
        self._text("Add more runs (other costumes / positions / recordings) "
                   "for a more robust pool, or apply now.", TEXT_DIM)
        self._spacer(10)
        self._buttons(("Apply selected", self._on_apply, 140),
                      ("Add another run",
                       lambda: self._act(self.state.add_another_run), 140),
                      ("Skip apply",
                       lambda: self._act(self.state.skip_apply), 110))

    def _on_apply(self) -> None:
        selected = [path for tag, path in self._checkbox_tags
                    if dpg.does_item_exist(tag) and dpg.get_value(tag)]
        self._act(self.state.apply_pool, selected)

    def _build_applied(self) -> None:
        self._text("Calibration complete.")
        self._spacer(6)
        if self.state.applied_summary:
            self._text(self.state.applied_summary, TEXT_MUTED)
        else:
            self._text("Pool not applied - the scene results remain applied "
                       "to this session.", TEXT_MUTED)
        self._spacer(10)
        self._text("'Save to project' writes a normal timestamped project "
                   "save (what startup and the picker load).", TEXT_DIM)
        self._spacer(12)
        self._buttons(("Save to project",
                       lambda: self._act(self.state.save_project), 150),
                      ("Keep this session only",
                       lambda: self._act(self.state.keep_session), 180))
