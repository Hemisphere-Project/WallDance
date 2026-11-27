"""The project switch's neighbours (A2, #t-023 follow-up, unit sw2): a camera change, a new project
and a failed switch.

The rig of tests/test_project_switch_clean.py (MainLoop's pumps and acquire, the real
CameraController and ConfigManager, a fake 20 fps IDS camera on a virtual clock, every frame through
FrameProcessor.replay_gpu_cached), driven through three other doors:

(a) a camera source switch, ``CameraController._cb_camera_change`` (the SelectSource command's
    handler): another camera is another view -- the old tracks' coordinates mean nothing there -- so
    it starts clean exactly as a project switch does, and its silence is never a camera stall;
(c) « Start blank » / « New project », ``ConfigManager._cb_project_blank`` (its model load pauses and
    reopens the camera through the app's real ``_ModelCameraAdapter``): it starts clean too;
(d) a failed switch (an unreadable file; a model load that fails inside the switch): the old project
    is kept -- same wall, same dancers: its track and its slot stay -- but the failed attempt's
    silence is not a camera stall (no « [CameraStall] » line, no keep-alive re-send).

On release 1 + the switch fix (d31fd3d) the probe of gates/sw found: a 1 s camera change counted as a
stall (+1), the old camera's frame re-sent 18 times, the old slot kept across the change of view;
Start blank and the failed switches were not driven there."""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

pytest.importorskip("torch")                              # runtime.* -> camera stack
import test_project_switch_clean as T                      # noqa: E402  (the harness)


def _record(rec):
    path = os.environ.get("WD_SWITCH_REPORT")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
    return rec


def _walking(s):
    x = {"v": T.OLD_XY[0]}

    def scene(t):
        x["v"] += 1.0
        return [T._det(x["v"], T.OLD_XY[1])]

    s.scene = scene


def _silence(s, t_req):
    return round(min(d for d in s.cam.delivered if d > t_req)
                 - max(d for d in s.cam.delivered if d <= t_req), 3)


def _drive_clean(tmp_path, monkeypatch, capsys, kind, trigger, *, block, warm):
    """T._drive's drive with another door: a dancer earns slot 1 (3 s), the tracker loses her
    (0.3 s, the slot coasts), ``trigger(s)``, an empty wall (4 s), then a first dancer elsewhere."""
    s = T._session(tmp_path, monkeypatch)
    _walking(s)
    T._run(s, 3.0)
    held = [o.track_id for o in s.proc.last_emitted]
    assert held, "the drive needs a held slot"
    s.scene = lambda t: []
    T._run(s, 0.3)
    assert [o.track_id for o in s.proc.last_emitted] == held
    stalls0, frames0 = s.proc.stall.stalls, len(s.osc.frames)
    capsys.readouterr()
    s.cam.arm(block, warm)
    t_req = s.clock.t
    trigger(s)
    t_end = s.clock.t
    after = {"tracks": len(s.proc.tracker.tracks) + len(s.proc.tracker._dormant),
             "slots_bound": T._bound(s), "osc_clears": len(s.osc.clears)}
    T._run(s, 4.0)
    empty_frames = s.osc.frames[frames0:]
    t_new = s.clock.t
    s.scene = lambda t: [T._det(*T.NEW_XY)]
    n_new = s.n
    while s.n - n_new < 40 and s.clock.t < t_new + 15.0:
        T._tick(s)
    out = capsys.readouterr().out
    old_frames = [f for f in empty_frames if f[1]]
    new_first = next((f for f in s.osc.frames if f[0] >= t_new and f[1]), None)
    new_slot = None
    if new_first is not None and s.proc._slots is not None:
        sid = new_first[1][0][0]
        sl = next(sl for sl in s.proc._slots.slots if sl.sid == sid)
        xy = np.array(new_first[1][0][1:])
        seen_at = T._KP.mean(axis=0) + T.NEW_XY
        new_slot = {"sid": sid, "bound_since": round(sl.bound_since, 3), "xy": xy.tolist(),
                    "fresh": bool(sl.bound_since >= t_end
                                  and float(np.abs(xy - seen_at).max()) <= 5.0)}
    return _record({
        "kind": kind, "block_s": block, "warm_s": warm, "event_s": round(t_end - t_req, 3),
        "silence_s": _silence(s, t_req), "held_ids": held,
        "stall_count_moved": s.proc.stall.stalls - stalls0,
        "camerastall_lines": [ln for ln in out.splitlines() if "[CameraStall]" in ln],
        "old_slot_frames_after": len(old_frames),
        "tracker_after": after["tracks"], "slots_bound_after": after["slots_bound"],
        "osc_clears_at_event": after["osc_clears"], "osc_clears_total": len(s.osc.clears),
        "new_dancer_slot": new_slot, "camera_source": s.app.camera.state.source})


def _assert_clean(rec):
    assert rec["stall_count_moved"] == 0 and not rec["camerastall_lines"], rec
    assert rec["old_slot_frames_after"] == 0, rec
    assert rec["tracker_after"] == 0 and rec["slots_bound_after"] == [], rec
    assert rec["osc_clears_at_event"] == 1 and rec["osc_clears_total"] == 1, rec
    assert rec["new_dancer_slot"] is not None and rec["new_dancer_slot"]["fresh"], rec


REOPENS = [(0.0, 1.0), (0.0, 3.0), (1.0, 0.0), (3.0, 0.0)]
REOPEN_IDS = [f"warm{w:g}s" if w else f"block{b:g}s" for b, w in REOPENS]


# -- (a) a camera source switch -------------------------------------------------------------------
@pytest.mark.parametrize("block,warm", REOPENS, ids=REOPEN_IDS)
def test_a_camera_source_switch_starts_clean(tmp_path, monkeypatch, capsys, block, warm):
    """warmN: the new camera opens at once and delivers N s later (the main loop polls it);
    blockN: its open blocks N s inside the handler."""
    rec = _drive_clean(tmp_path, monkeypatch, capsys, "camera_source",
                       lambda s: s.cams._cb_camera_change("1"), block=block, warm=warm)
    assert rec["camera_source"] == "1", rec
    _assert_clean(rec)


def test_reselecting_the_open_camera_changes_nothing(tmp_path, monkeypatch):
    """The source already open: no reopen, no reset (the handler's early return)."""
    s = T._session(tmp_path, monkeypatch)
    _walking(s)
    T._run(s, 3.0)
    held = [o.track_id for o in s.proc.last_emitted]
    opens0 = len(s.cam.opens)
    s.cams._cb_camera_change("ids")
    assert len(s.cam.opens) == opens0 and not s.osc.clears
    T._run(s, 0.5)
    assert [o.track_id for o in s.proc.last_emitted] == held


# -- (c) Start blank / New project ----------------------------------------------------------------
def _real_model_load_camera(s, ok=True):
    """The camera sequence of ModelController's load (_load_model_with_progress_impl) through the
    app's real _ModelCameraAdapter: pause the camera, load (0.5 s), reopen and flush."""
    from app import _ModelCameraAdapter
    port = _ModelCameraAdapter(s.app)

    def load(*a, **k):
        was_open, source = port.snapshot()
        if was_open:
            port.close()
        s.clock.sleep(T.SWITCH_BLOCK_S)
        if was_open:
            port.reopen_and_flush(source)
        return ok
    return load


def _blank(s):
    s.app.models._load_default_model_startup = _real_model_load_camera(s)
    assert s.mgr._cb_project_blank()


def _new_project(s):
    s.app.models._load_default_model_startup = _real_model_load_camera(s)
    assert s.mgr._cb_project_new("showC")


@pytest.mark.parametrize("door", ["blank", "new"])
@pytest.mark.parametrize("block,warm", REOPENS[:2], ids=REOPEN_IDS[:2])
def test_start_blank_starts_clean(tmp_path, monkeypatch, capsys, door, block, warm):
    rec = _drive_clean(tmp_path, monkeypatch, capsys, f"project_{door}",
                       _blank if door == "blank" else _new_project, block=block, warm=warm)
    _assert_clean(rec)


# -- (d) a failed switch keeps the old project ----------------------------------------------------
def _drive_failed(tmp_path, monkeypatch, capsys, kind, *, block, warm):
    """A dancer earns slot 1 (3 s) and stays (same wall, same dancers); a switch that fails;
    the camera back, 2 s of her."""
    s = T._session(tmp_path, monkeypatch)
    _walking(s)
    T._run(s, 3.0)
    held = [o.track_id for o in s.proc.last_emitted]
    tracks0 = sorted(t.track_id for t in s.proc.tracker.tracks)
    bound0 = T._bound(s)
    assert held and tracks0 and bound0
    target = None
    if kind == "unreadable_file":
        bad = tmp_path / "projects" / "showB" / "showB_broken.json"
        bad.write_text("{ not json")
        target = str(bad)
    elif kind == "blank_load_fails":                       # Start blank: the default model fails
        s.app.models._load_default_model_startup = _real_model_load_camera(s, ok=False)
    else:                                                  # model_load_fails
        def fail(name, force_pt=False):
            s.clock.sleep(T.SWITCH_BLOCK_S)
            return False
        s.app.models._load_model_with_progress = fail
        target = s.paths["showB"]
    stalls0, frames0 = s.proc.stall.stalls, len(s.osc.frames)
    capsys.readouterr()
    s.cam.arm(block, warm)
    t_req = s.clock.t
    if target is None:
        assert s.mgr._cb_project_blank() is False
    else:
        s.mgr._pending_project_switch = target
        assert T._tick(s) == "pump"
    t_end = s.clock.t
    tracks1 = sorted(t.track_id for t in s.proc.tracker.tracks)
    bound1 = T._bound(s)
    T._run(s, warm + 2.0)
    out = capsys.readouterr().out
    back = min(d for d in s.cam.delivered if d > t_req)
    during = [f for f in s.osc.frames[frames0:] if f[0] < back and f[1]]
    resumed = [f for f in s.osc.frames[frames0:] if f[0] >= back and f[1]]
    return _record({
        "kind": kind, "block_s": block, "warm_s": warm, "event_s": round(t_end - t_req, 3),
        "silence_s": _silence(s, t_req), "held_ids": held,
        "project_after": s.mgr._current_project,
        "tracks_kept": tracks1 == tracks0, "slots_kept": bound1 == bound0,
        "stall_count_moved": s.proc.stall.stalls - stalls0,
        "camerastall_lines": [ln for ln in out.splitlines() if "[CameraStall]" in ln],
        "resent_during_silence": len(during),
        "ids_after_resume": sorted({tr[0] for f in resumed for tr in f[1]}),
        "resumed_frames": len(resumed), "osc_clears": len(s.osc.clears)})


@pytest.mark.parametrize("kind", ["unreadable_file", "model_load_fails", "blank_load_fails"])
@pytest.mark.parametrize("block,warm", REOPENS[:3], ids=REOPEN_IDS[:3])
def test_a_failed_switch_keeps_the_old_project_without_a_stall(tmp_path, monkeypatch, capsys,
                                                               kind, block, warm):
    rec = _drive_failed(tmp_path, monkeypatch, capsys, kind, block=block, warm=warm)
    assert rec["project_after"] == "showA", rec
    assert rec["tracks_kept"] and rec["slots_kept"] and rec["osc_clears"] == 0, rec
    assert rec["stall_count_moved"] == 0 and not rec["camerastall_lines"], rec
    assert rec["resent_during_silence"] == 0, rec
    assert rec["resumed_frames"] > 0 and rec["ids_after_resume"] == rec["held_ids"], rec


def test_the_app_wires_the_view_change_and_the_failed_switch():
    """app.py hands the CameraController the clean start and the ConfigManager the not-live step
    (the tests above wire them as app.py does; this keeps the wiring from being lost in a merge)."""
    import inspect
    from app import WallDanceApp
    src = inspect.getsource(WallDanceApp.__init__)
    assert src.count("start_clean=self._cb_project_start_clean") == 2, "camera + config managers"
    assert "camera_not_live=lambda: self.processor.camera_not_live()" in src
