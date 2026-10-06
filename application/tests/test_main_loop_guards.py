"""Headless tests for the main loop's diagnostics and crash hygiene.

The loop drives DPG, the camera and the GPU, so these tests run single
stages / ``run()`` against minimal fakes (``SimpleNamespace``): only the
attributes a stage touches exist, so an unexpected dependency fails loudly.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("torch")  # runtime.main_loop -> core.pipeline -> torch/ultralytics

from runtime.main_loop import MainLoop, _Tick  # noqa: E402


class _Ui:
    def __init__(self, render_s: float = 0.0):
        self.renders = 0
        self.render_s = render_s

    def render_frame_raw(self):
        self.renders += 1
        if self.render_s:
            time.sleep(self.render_s)

    def render_frame(self):
        self.renders += 1


def _loop(**app_attrs):
    app = SimpleNamespace(last_fps_time=0.0, **app_attrs)
    return MainLoop(app), app


# ---------------------------------------------------------------------------
# PERF-11: the GUI tail reaches the [Budget] line
# ---------------------------------------------------------------------------

def test_budget_line_reports_dpg_render_and_gui_stats(capsys):
    loop, app = _loop(ui=_Ui(render_s=0.003), fps=19.7,
                      timing={"process_wall": 31.6, "yolo": 11.6,
                              "preview_draw": 1.0, "preview_upload": 3.0})
    t = _Tick(gui_stats_ms=0.7, camera_read_ms=0.4)
    t.log_timing = True          # what the preview stage now sets
    loop._tick_render(t)
    out = capsys.readouterr().out
    budget = [ln for ln in out.splitlines() if ln.startswith("[Budget]")]
    assert len(budget) == 1
    assert "dpg_render=" in budget[0]     # never printed before PERF-11
    assert "gui_stats=0.7" in budget[0]
    assert "process_wall=31.6" in budget[0]
    assert app.ui.renders == 1


def test_budget_not_logged_when_preview_stage_did_not_ask(capsys):
    loop, app = _loop(ui=_Ui(), fps=19.7, timing={"process_wall": 31.6})
    loop._tick_render(_Tick(gui_stats_ms=0.7))
    assert "[Budget]" not in capsys.readouterr().out
    assert "dpg_render" in app.timing     # still injected for the GUI stats
