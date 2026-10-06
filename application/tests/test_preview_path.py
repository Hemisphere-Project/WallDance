"""Preview path: display-only work, never on the tracking outputs.

* the preview rate cap keeps its phase (15 fps on a 20 fps stream really is
  15/s, 10 fps is 10/s, with frame-time jitter);
* with an ROI the GPU downloads the ROI at preview scale and the CPU composes
  straight into the texture-sized canvas (``scaled_roi_rect`` shared by both);
* the frame-wait paths render the UI at most at 60 Hz.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest

from core.visualization import scaled_roi_rect

torch = pytest.importorskip("torch")
gp = pytest.importorskip("core.gpu_pipeline")


def _pipeline(cap):
    p = gp.GpuPipeline(gp.GpuPipelineSettings(preview_width=520, preview_height=535))
    p.settings.preview_fps_cap = cap
    p.update_settings(p.settings)
    return p


@pytest.mark.parametrize("cap,expected", [(15.0, 15), (10.0, 10), (None, 20)])
def test_rate_cap_keeps_phase_on_a_20fps_stream(cap, expected):
    p = _pipeline(cap)
    rng = np.random.default_rng(0)
    t0 = 1000.0
    n = sum(p._preview_due(True, t0 + i * 0.05 + rng.uniform(-0.004, 0.004))
            for i in range(200))                       # 10 s of frames, jittered
    assert abs(n / 10.0 - expected) <= 0.5


def test_rate_cap_no_burst_after_a_stall():
    p = _pipeline(15.0)
    assert p._preview_due(True, 0.0)
    assert not p._preview_due(True, 0.03)
    assert p._preview_due(True, 30.0)                  # 30 s gap: one preview...
    assert not p._preview_due(True, 30.01)             # ...not a catch-up burst
    assert not p._preview_due(False, 60.0)             # preview off: never


def test_roi_preview_target_is_preview_scale():
    p = _pipeline(None)
    roi = {"enabled": True, "x": 137, "y": 232, "w": 1299, "h": 1139}
    w, h = p._preview_target(roi, 1488, 1528)
    x0, y0, x1, y1 = scaled_roi_rect(137, 232, 1299, 1139, 1488, 1528, 520, 535)
    assert (w, h) == (x1 - x0, y1 - y0) and w < 520 and h < 535
    assert p._preview_target({"enabled": False}, 1488, 1528) == (520, 535)


def test_scaled_roi_rect_identity_and_bounds():
    assert scaled_roi_rect(10, 20, 30, 40, 100, 100, 100, 100) == (10, 20, 40, 60)
    assert scaled_roi_rect(0, 0, 100, 100, 100, 100, 50, 50) == (0, 0, 50, 50)
    x0, y0, x1, y1 = scaled_roi_rect(90, 90, 50, 50, 100, 100, 37, 37)
    assert 0 <= x0 <= x1 <= 37 and 0 <= y0 <= y1 <= 37


def test_compose_roi_preview_at_render_size_matches_the_old_path():
    pytest.importorskip("dearpygui")
    import cv2
    from ui.roi_mask_editor import RoiMaskEditor
    src_w, src_h, out_w, out_h = 1488, 1528, 520, 535
    x, y, w, h = 137, 232, 1299, 1139
    ed = object.__new__(RoiMaskEditor)
    ed.state = SimpleNamespace(effective_roi=lambda fw, fh: (x, y, w, h))
    rng = np.random.default_rng(0)
    roi_full = cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (31, 31), 8)
    old = cv2.resize(ed._compose_roi_preview(roi_full, src_w, src_h), (out_w, out_h))
    x0, y0, x1, y1 = scaled_roi_rect(x, y, w, h, src_w, src_h, out_w, out_h)
    small = cv2.resize(roi_full, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
    new = ed._compose_roi_preview(small, src_w, src_h, out_w, out_h)
    assert new.shape == old.shape == (out_h, out_w, 3)
    assert not new[: y0 - 1].any() and not new[:, : x0 - 1].any()   # black outside
    inner = (slice(y0 + 2, y1 - 2), slice(x0 + 2, x1 - 2))
    assert np.abs(new[inner].astype(int) - old[inner].astype(int)).mean() < 2.0


def test_idle_renders_are_capped_at_60hz():
    import runtime.main_loop as ml
    renders = []
    app = SimpleNamespace(last_fps_time=0.0,
                          ui=SimpleNamespace(render_frame=lambda: renders.append(1)))
    loop = ml.MainLoop(app)
    t_end = time.perf_counter() + 0.1
    while time.perf_counter() < t_end:
        loop._render_while_waiting()
    assert 1 <= len(renders) <= 8                       # ~6 at 60 Hz in 100 ms
