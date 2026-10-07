"""A camera reopen restores the project's IDS crop ratio (2026-10-06 night: the silent reconnect after
each playback -> LIVE switch reopened the camera at the default ratio 1.0 -- portrait 1488x1528 -- while
the project said 1.38, so the takes flipped between landscape and portrait)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from runtime.camera_controller import CameraController


def _host(w, h, ratio=1.38):
    return SimpleNamespace(
        _use_unified_camera=True,
        unified_camera=MagicMock(width=w, height=h),
        ids_exposure_auto=False, ids_exposure_us=25000.0,
        ids_gain_auto=False, ids_gain_db=36.0,
        ids_ratio=ratio, _cb_ids_ratio_change=MagicMock())


def test_reopen_at_the_default_crop_reapplies_the_project_ratio():
    host = _host(1488, 1528)
    CameraController._reapply_ids_settings(host)
    host.unified_camera.set_exposure.assert_called_once_with(25000.0)
    host.unified_camera.set_gain.assert_called_once_with(36.0)
    host._cb_ids_ratio_change.assert_called_once_with(1.38)


def test_reopen_at_the_right_crop_does_not_restart_the_camera():
    host = _host(1776, 1300)                       # 1.366: within 5 % of 1.38
    CameraController._reapply_ids_settings(host)
    host._cb_ids_ratio_change.assert_not_called()


def test_every_open_uses_the_project_ratio():
    """The open itself is built at the project's crop (no extra stream restart afterwards)."""
    cam = MagicMock()
    cam.open.side_effect = lambda src: (setattr(cam, "seen_ratio", cam.crop_ratio), False)[1]
    host = SimpleNamespace(unified_camera=cam, ids_ratio=1.38, _normalize_camera_source=lambda s: s,
                           camera=MagicMock(), ui=MagicMock(available=False))
    try:
        CameraController._open_camera_unified(host, "ids")
    except Exception:
        pass                                   # the failed-open path needs more of the controller
    assert cam.seen_ratio == 1.38


def test_unified_camera_opens_ids_at_its_crop_ratio_or_the_default():
    from camera.ids_camera import UnifiedCamera
    assert UnifiedCamera().crop_ratio is None   # the config default IDS_RATIO until the controller sets it
