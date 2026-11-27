"""F4h (#t-056 follow-up, laptop 27/11): a point HELD on a foreground group is a point, wherever a human is told.

On the show laptop on 27/11 (an empty room, a bright window in view) slot 1 was SENT on OSC for 59.8 s, 1078
frames of it held on a foreground group (F4, state ``fg``), while the status said 0 dancers and the alert « No
dancers tracked for 30s »: both read the tracker's reported tracks, not the slot outputs OSC sends.  This test
replays that episode -- its slot states frame by frame (a synthetic sequence with the laptop log's segments; the
log itself when ``WD_F4H_LOG`` names it) -- through the real status (main loop's StatsTick fields and slot words,
RemoteApi.status), the real health line (main loop's signal (a) feed and line tick) and the real alerts
(HealthMonitor.tick), and through the watch tool's summary.  Where nothing is sent, everything reads as before.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core import points_report as PR
from core.ops_monitor import CheckResult, HealthMonitor
from runtime import api

FPS = 20.0
H = 150.0                    # the held point's box height (original px; the laptop's person_height_px)
BOUNDS_WH = (1260, 1458)     # the watched zone on the laptop (ROI 1260 x 1458 after SetRoiRect)
POINT = (877.0, 870.0)       # slot 1's sent point on the laptop (median of 1263 frames): « middle right »
HELD_GROUP = (892.0, 860.0, 4000.0)     # the foreground group under it (the window's patch)
CENTRE_GROUP = (600.0, 700.0, 4000.0)   # a second group, bare from the start of RUN for ~29 s (polls 1-2: centre)

# slot 1's states over RUN (frames 2392..6090 of 20261127_103037_live, tracking_events.jsonl), run-length:
EPISODE = [("lost", 100), ("live", 86), ("belt", 2), ("live", 2), ("fg", 3), ("live", 3), ("fg", 7),
           ("live", 9), ("belt", 6), ("live", 4), ("fg", 443), ("live", 2), ("fg", 600), ("coasting", 29),
           ("lost", 532), ("live", 5), ("coasting", 1), ("live", 1), ("coasting", 5), ("fg", 25),
           ("coasting", 30), ("lost", 1804)]
# the tracker reported a track (the log's « emitted ») on the first live block (YOLO, fresh skeleton) only here
REPORTED_BLOCKS = (1, 15)


def _synthetic():
    rows, i = [], 0
    for b, (st, n) in enumerate(EPISODE):
        for k in range(n):
            sent = st != "lost"
            rows.append({"i": i, "st": st, "xy": POINT if sent else None,
                         "reported": 1 if (b in REPORTED_BLOCKS and (b == 1 or k == 1)) else 0})
            i += 1
    return rows


def _from_log(path):
    rows, i = [], 0
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt") as fh:
        for ln in fh:
            e = json.loads(ln)
            if e.get("event") != "FRAME_SUMMARY" or not (2392 <= e["frame"] <= 6090):
                continue
            d = e["data"]
            s1 = next(s for s in d["slots"] if s["id"] == 1)
            sent = {int(p[0]): (float(p[1]), float(p[2])) for p in d.get("sent") or []}
            rows.append({"i": i, "st": s1["st"], "xy": sent.get(1), "reported": len(d.get("emitted") or [])})
            i += 1
    return rows


def _import_main_loop():
    if importlib.util.find_spec("torch") and importlib.util.find_spec("ultralytics"):
        import runtime.main_loop as ml
        return ml
    import core
    stub = types.ModuleType("core.pipeline")
    stub.ScaledTrack = object
    sys.modules["core.pipeline"] = stub
    try:
        import runtime.main_loop as ml
    finally:
        sys.modules.pop("core.pipeline", None)
        if getattr(core, "pipeline", None) is stub:
            del core.pipeline
    return ml


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class _Proc:
    """The processor as the main loop reads it: the slot outputs (``last_emitted``, as
    ``FrameProcessor._slot_track`` builds them), what went out on OSC (``last_sent``, as pipeline._emit sets it at
    L = 1: test_emit_records_what_went_out pins that on the real one), the slot table, this frame's foreground.
    ``process`` is what the main loop's _tick_process calls; ``frame`` queues what it puts out next."""

    def __init__(self):
        from core.foreground import FgBlob, FgFrame
        self._blob, self._frame = FgBlob, FgFrame
        self.last_fg = None
        self.last_emitted = []
        self.last_sent = None
        self.osc_on = True                 # pipeline._emit's osc_on (the sender and settings.osc_enabled)
        self._next = None
        self.states = [(1, "lost", 0.0), (2, "lost", 0.0)]
        self.belt_status = "ready"
        self.plate_status = "ready"
        self.stall = SimpleNamespace(stalls=0)
        self.osc = SimpleNamespace(send_errors=0, enabled=True, send_latency_ms=lambda ms: None)
        self.dancer_height = (H, 40, 0.0)
        self._plate_invalid = None
        self.plate_refusal = None
        self._plate_capture = None
        self.last_raw_det_heights = ()
        self.height_guard_event = None
        self.gpu_fallback_reason = ""
        self.bg_subtractor = SimpleNamespace(has_reference=False)
        self._fg_detector = SimpleNamespace(plate=SimpleNamespace(frame_size=(1260, 1824)), stale_since="")

    @staticmethod
    def sent_xy(tr):
        return float(tr.smoothed_centroid[0]), float(tr.smoothed_centroid[1])

    def _zone_ok_fn(self, w, h):
        return lambda x, y: True

    def slot_states(self):
        return list(self.states)

    def frame(self, groups, state, age, xy, reported=0):
        self._next = (groups, state, age, xy, reported)

    def process(self, frame, need_preview=True, frame_number=None):
        """FrameProcessor.process as the main loop sees it: this frame's foreground, slot table and slot
        outputs; what went out on OSC; the tracker's reported tracks (the log's « emitted ») returned."""
        groups, state, age, xy, reported = self._next
        ds = 6
        mask = np.zeros((BOUNDS_WH[1] // ds, BOUNDS_WH[0] // ds), np.uint8)
        self.last_fg = self._frame(mask, 0.0, 0.0, ds,
                                   blobs=[self._blob(x, y, a, 1.0, (x - 40, y - 75, x + 40, y + 75))
                                          for x, y, a in groups])
        self.states = [(1, state, age), (2, "lost", 0.0)]
        self.last_emitted = [] if xy is None else [SimpleNamespace(
            track_id=1, slot_state=state, slot_age_s=age, smoothed_centroid=np.array(xy),
            bbox=np.array([xy[0] - 30.0, xy[1] - H / 2, 60.0, H]))]
        self.last_sent = self.last_emitted if self.osc_on else None
        return [SimpleNamespace(track_id=8)] * int(reported), None, {}, 0.0


class _Bus:
    """The app's bus: every event goes on to the remote API's bus (/status); the alerts are kept."""

    def __init__(self, bus):
        self.ui_ready, self._bus, self.alerts, self.line = True, bus, [], None

    def publish(self, e):
        if isinstance(e, api.Alert):
            self.alerts.append(e)
        if isinstance(e, api.HealthLine):
            self.line = e
        self._bus.publish(e)


def _app(bus, ml):
    return SimpleNamespace(
        last_fps_time=1000.0, processor=_Proc(), fps=20.0, frame_count=0, preview_enabled=False, preview_stride=1,
        system_state=ml.SystemState.RUN, _total_frame_count=0, unified_camera=None, last_tracked=[], timing={},
        latency_ms=0.0, _ui_camera_type="ids", _watchdog=SimpleNamespace(beat=lambda: None),
        calibration=SimpleNamespace(_calibrating=False, _calibrating2=False),
        camera=SimpleNamespace(state=SimpleNamespace(is_open=True, source="ids", width=1260, height=1824)),
        preview=SimpleNamespace(width=640, height=480, render_scale=1.0),
        enhancer=SimpleNamespace(get_status=lambda: {"brightness": 0}),
        recorder=SimpleNamespace(is_playing=False),
        cameras=SimpleNamespace(_camera_reconnecting=False),
        tracker=SimpleNamespace(last_over_cap=0, logger=None),
        models=SimpleNamespace(_model_loaded=True, _model_loading=False, _trt_requested=True, model=object(),
                               current_model_name="yolo", model_manager=SimpleNamespace(is_using_tensorrt=lambda: True)),
        settings=SimpleNamespace(person_height_px=H, identity_slots_enabled=True, max_dancers=2, osc_enabled=True,
                                 enhance_enabled=False, enhance_lite=False, brightness_threshold=100.0, imgsz=640,
                                 output_smoothing_l=1, bg_subtract_enabled=False),
        osc_enabled=True, osc_ip="127.0.0.1", osc_port=9000,
        _health=HealthMonitor(gpu_stats_fn=None),
        _readiness_setup_rows=lambda: [],
        bus=bus)


def _remote(bus):
    from services.remote_api import RemoteApi
    return RemoteApi(api.RuntimeAPI(), bus, token="t", roots={}, state_fn=lambda: "run")


def _within(win, t):
    return bool(win) and win[0] <= t < win[1]


def replay(rows, monkeypatch, centre_group=True, standby=None, osc_off=None):
    """Run the episode through the main loop's own tick stages, in its order, once per frame: _ops_heartbeat
    (signal (a); once a second the health tick with the points and the line), _tick_process (the processor; in
    STANDBY the preview path), _tick_events (StatsTick).  A status poll follows each 1 Hz heartbeat.  ``standby`` /
    ``osc_off``: (t0, t1) seconds of the episode run in STANDBY / with OSC off.  Returns per-second records of the
    last processed frame (what was sent then) and what each human-facing place said, and the alerts raised."""
    ml = _import_main_loop()
    clk = _Clock()
    monkeypatch.setattr(ml, "time", SimpleNamespace(monotonic=clk, time=clk, perf_counter=clk,
                                                    sleep=lambda s: None))
    bus = api.EventBus()
    remote = _remote(bus)
    app = _app(_Bus(bus), ml)
    loop = ml.MainLoop(app)
    for name in ("_poll_osc_alert", "_note_project_for_crash_marker", "_plate_tick", "_reference_tick",
                 "_update_gpu_stats_if_due"):              # sources the points do not touch: quiet
        setattr(loop, name, lambda *a, **k: None)
    loop._light_median = lambda now: (None, "")
    loop._picture_level = lambda now: None
    proc = app.processor
    frame = np.zeros((4, 4, 3), np.uint8)
    out, alerts = [], []
    # the group the point is held on is on the wall from the start of RUN, where the point first appears
    last_xy = next(r["xy"] for r in rows if r["xy"] is not None)
    since, prev_st, sent_since, rec = 0.0, None, None, None
    for r in rows:
        t = r["i"] / FPS
        clk.t = 1000.0 + t
        tick_before, n_alerts = loop._last_ops_tick, len(app.bus.alerts)
        loop._ops_heartbeat()
        alerts += [(rec["t"], rec["row"], a) for a in app.bus.alerts[n_alerts:]]
        if loop._last_ops_tick != tick_before and rec is not None:        # the 1 Hz part ran: a status poll
            out.append(dict(rec, status=remote.status(), line=app.bus.line))
        if r["st"] != prev_st:
            since, prev_st = t, r["st"]
        if r["xy"] is not None:
            last_xy = r["xy"]
            sent_since = t if sent_since is None else sent_since
        else:
            sent_since = None
        held_g = (last_xy[0] + 15.0, last_xy[1] - 10.0, HELD_GROUP[2])     # the group stays where it was held
        groups = [held_g] + ([CENTRE_GROUP] if centre_group and 0.3 <= t < 29.0 else [])
        in_standby, off = _within(standby, t), _within(osc_off, t)
        app.system_state = ml.SystemState.STANDBY if in_standby else ml.SystemState.RUN
        app.osc_enabled = app.settings.osc_enabled = proc.osc_on = not off
        proc.frame(groups, r["st"], round(t - since, 3), r["xy"], r["reported"])
        tick = ml._Tick(frame=frame)
        assert loop._tick_process(tick)
        loop._tick_events(tick)
        sent = r["xy"] is not None and not in_standby and not off            # what went out on OSC this frame
        rec = {"t": t, "row": r, "standby": in_standby, "osc_off": off, "sent": sent,
               "sent_for": None if sent_since is None else t - sent_since}
    return out, alerts, app


def _rows():
    log = os.environ.get("WD_F4H_LOG")
    return [("synthetic", _synthetic())] + ([("log", _from_log(log))] if log and Path(log).exists() else [])


@pytest.mark.parametrize("name,rows", _rows(), ids=lambda v: v if isinstance(v, str) else "")
def test_a_sent_point_is_counted_named_and_never_called_missing(name, rows, monkeypatch):
    out, alerts, app = replay(rows, monkeypatch)
    sent_polls = [o for o in out if o["row"]["xy"] is not None]
    assert len(sent_polls) >= 50                   # the episode: about a minute of polls with a point sent
    for o in sent_polls:
        st, word = o["row"]["st"], PR.slot_word(o["row"]["st"])
        p = o["status"]["latest"]["StatsTick"]["payload"]
        assert p["num_dancers"] == 1, (o["t"], p)                               # the status counts it ...
        assert p["points"]["sent"] == 1 and p["points"]["slots"][0]["state"] == st
        assert p["points"]["held"] == (1 if st in PR.HELD_STATES else 0)
        assert f"D1 {word}" in p["slots_text"] and "D1 lost" not in p["slots_text"]   # ... and names its state
        if st in PR.HELD_STATES:
            assert "held" in word or word == "coasting"
        h = o["status"]["health"]
        assert h["points"]["points"] == 1                                       # the line counts it too
        assert h["points"]["held"] == (1 if st in PR.HELD_STATES else 0)
        if not _h2_tail(o):                                                     # never « that body has no point »
            assert "middle right" not in h["line"], (o["t"], h["line"])
        assert "no dancer" not in h["line"] and "No dancers" not in h["line"]
    # the alert: fired while the point was held, it says what is sent, never « No dancers tracked »
    during = [(t, r, a) for t, r, a in alerts if r["xy"] is not None]
    assert during, "the no-detection alert fires during the 30 s fg hold (as on the laptop, 10:33:44)"
    for t, r, a in during:
        assert "No dancers tracked" not in a.message and a.kind == "no_detection"
        assert a.message.startswith("Nobody seen by the detector for ") and "1 point sent: D1 " in a.message
        assert a.data["points_sent"] == 1
    assert any("held on the foreground" in a.message for _t, _r, a in during)
    _numbers(f"a_{name}", polls=len(out), polls_sent=len(sent_polls),
             polls_held=sum(1 for o in sent_polls if o["row"]["st"] in PR.HELD_STATES),
             frames_sent=sum(1 for r in rows if r["xy"] is not None),
             status_counted=sum(1 for o in sent_polls
                                if o["status"]["latest"]["StatsTick"]["payload"]["num_dancers"] == 1),
             line_named_held_body=sum(1 for o in sent_polls if "middle right" in o["status"]["health"]["line"]),
             line_named_held_body_after_h2_tail=sum(1 for o in sent_polls if not _h2_tail(o)
                                                    and "middle right" in o["status"]["health"]["line"]),
             alerts_while_sent=[f"{t:.1f}s {a.message}" for t, _r, a in during],
             alerts_all=[f"{t:.1f}s {a.message}" for t, _r, a in alerts])


def _numbers(key, **kw):
    """The gate runner's numbers (gates/fgh/run_gate.sh sets WD_F4H_NUMBERS)."""
    path = os.environ.get("WD_F4H_NUMBERS")
    if not path:
        return
    p = Path(path)
    d = json.loads(p.read_text()) if p.exists() else {}
    d[key] = kw
    p.write_text(json.dumps(d, indent=1))


@pytest.mark.parametrize("name,rows", _rows(), ids=lambda v: v if isinstance(v, str) else "")
def test_the_held_group_alone_never_turns_the_points_row_red_while_sent(name, rows, monkeypatch):
    out, _alerts, _app_ = replay(rows, monkeypatch, centre_group=False)
    tails = 0
    for o in out:
        if o["row"]["xy"] is not None:
            assert o["status"]["health"]["points"]["bare"] == 0
            if _h2_tail(o):
                tails += 1
                continue
            assert o["status"]["health"]["source"] != "points" or o["status"]["health"]["state"] != "fail", o["t"]
    assert tails <= 2 * 2      # H2's 1 s tail, at the start of each of the two episodes: at most 2 polls each


def _h2_tail(o) -> bool:
    """H2's pinned rule (test_h2_health_line.test_a_back_when_the_group_gets_a_point: « a point for a frame is
    not enough »): a group past 3 s without a point stays named for BARE_GAP_S after a point reaches it -- live or
    held alike.  The first BARE_GAP_S of a sent point are exempt here, nothing after."""
    from core import ops_monitor as OM
    return o["sent_for"] is not None and o["sent_for"] <= OM.BARE_GAP_S + 0.5 / FPS


def test_where_nothing_is_sent_everything_reads_as_before(monkeypatch):
    out, alerts, app = replay(_synthetic(), monkeypatch)
    end = out[-1]
    assert end["row"]["xy"] is None
    p = end["status"]["latest"]["StatsTick"]["payload"]
    assert p["num_dancers"] == 0 and p["points"] == {"sent": 0, "held": 0, "slots": []}
    assert p["slots_text"] == "ids: D1 lost | D2 lost   belt: ready"
    h = end["status"]["health"]
    assert h["line"].startswith("FAIL - points: a body on the wall has had no point for ")
    assert h["line"].split(" s (", 1)[1].startswith("middle right) - if it is a dancer: check Max dancers (3 Live)")
    after = [a for t, r, a in alerts if r["xy"] is None and t > 100.0]
    assert after and all(a.message.startswith("No dancers tracked for ") and a.message.endswith("s while RUNNING")
                         for a in after)
    assert all(set(a.data) == {"seconds"} for a in after)
    # the alert's row in the line, when nothing is sent: the old « no dancer » wording
    from core.ops_monitor import health_alert_rows
    rows = {r.name: r for r in health_alert_rows(app._health, app._health._no_det_since + 40.0)}
    assert "detector" not in rows
    assert rows["no dancer"].detail == ("no dancer tracked for 40 s - if dancers are on the wall: check the IR "
                                        "and the picture")


def test_the_words_pinned():
    assert PR.slots_text([(1, "live", 0.2), (2, "lost", 3.0)], "ready", 2) == "ids: D1 live | D2 lost   belt: ready"
    assert PR.slots_text([(1, "coasting", 1.25), (2, "lost", 0.0)], "off", 2) == \
        "ids: D1 coasting 1.2s | D2 lost   belt: off"                      # the old coasting form, unchanged
    assert PR.slots_text([(1, "fg", 21.6), (2, "belt", 0.3)], "ready", 2) == \
        "ids: D1 held on the foreground 21.6s | D2 held on the belt 0.3s   belt: ready"
    assert PR.slots_text([], "ready", 2) == "ids: waiting for dancers (max 2)"
    pts = PR.points_summary([SimpleNamespace(track_id=1, slot_state="fg", slot_age_s=12.34)])
    assert pts == {"sent": 1, "held": 1, "slots": [{"id": 1, "state": "fg", "slot": True, "age_s": 12.3}]}
    assert PR.points_text(pts) == "1 point sent: D1 held on the foreground"
    assert PR.points_text(PR.points_summary([])) == "no point sent"
    assert PR.dancers_text(1, pts) == "1 (1 held)" and PR.dancers_text(2, PR.points_summary([])) == "2"
    raw = PR.points_summary([SimpleNamespace(track_id=7)])                # stable ids off: a raw tracker track
    assert raw["held"] == 0 and PR.points_text(raw) == "1 point sent: id 7 live"


def test_the_health_row_and_alert_say_held_not_missing():
    mon = HealthMonitor(gpu_stats_fn=None, no_detection_s=30.0)
    pts = PR.points_summary([SimpleNamespace(track_id=1, slot_state="fg", slot_age_s=31.0)])
    got = []
    for k in range(40):
        got += mon.tick(float(k), fps=20.0, n_tracked=0, in_run=True, model_ready=True, camera_open=True,
                        camera_reconnecting=False, playback_active=False, points=pts)
    assert [a.message for a in got] == ["Nobody seen by the detector for 30s while RUNNING - 1 point sent: D1 "
                                        "held on the foreground"]
    from core.ops_monitor import health_alert_rows, health_line
    rows = {r.name: r for r in health_alert_rows(mon, 39.0)}
    assert "no dancer" not in rows
    assert rows["detector"] == CheckResult("detector", "warn", "nobody seen for 39 s, 1 point sent: D1 held on the "
                                                               "foreground - if dancers are on the wall: check the "
                                                               "IR and the picture")
    line = health_line(list(rows.values()))
    assert line.text == ("WARN - detector: nobody seen for 39 s, 1 point sent: D1 held on the foreground - if "
                         "dancers are on the wall: check the IR and the picture")
    assert line.instruction == "if dancers are on the wall: check the IR and the picture"


def test_the_points_row_counts_held_points():
    from core.ops_monitor import BareGroupWatch
    w = BareGroupWatch()
    w.update(0.0, [(500.0, 900.0, 4000.0)], [(510.0, 880.0, H, "fg")], body_h=H)
    assert w.snapshot(0.0)["held"] == 1 and w.snapshot(0.0)["bare"] == 0
    assert w.state(0.0).detail == "1 body on the wall, 1 point (1 held): none without a point for more than 3 s"
    w.update(0.1, [(500.0, 900.0, 4000.0)], [(510.0, 880.0, H)], body_h=H)     # 3-tuples: as before
    assert w.snapshot(0.1)["held"] == 0
    assert w.state(0.1).detail == "1 body on the wall, 1 point: none without a point for more than 3 s"
    w.not_checked(0.2, "no foreground on this frame")
    assert w.snapshot(0.2)["held"] == 0


# ---------------------------------------------------------------------------------------------- the watch tool
def _wdwatch():
    root = Path(__file__).resolve().parents[2] / "extra"
    if "wdwatch" in sys.modules:
        return sys.modules["wdwatch"]
    spec = importlib.util.spec_from_file_location("wdwatch", root / "wdwatch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wdwatch"] = mod
    spec.loader.exec_module(mod)
    return mod


def _records(out, wd, strip_points=False, every_s=10.6):
    recs = [{"kind": "start", "run": "f4h", "t": 0.0, "host": "tango-lenovo"}]
    nxt = 0.0
    for o in out:
        if o["t"] < nxt:
            continue
        nxt = o["t"] + every_s
        st = json.loads(json.dumps(o["status"]))
        if strip_points:
            st["latest"]["StatsTick"]["payload"].pop("points", None)
        recs.append({"kind": "poll", "ok": True, "t": 1000.0 + o["t"], "status": wd._slim(dict(kind="poll",
                     status=st))["status"], "health": wd.health_of(st), "hit_rate": None, "interval_s": 10.0,
                     "bytes": 6000, "dur_s": 0.4})
    return recs


def test_the_watch_summary_shows_points_sent_and_held(monkeypatch):
    wd = _wdwatch()
    out, _alerts, _app_ = replay(_synthetic(), monkeypatch)
    recs = _records(out, wd)
    # during the hold (the laptop's polls 3-5: 10:33:35-57)
    mid = [r for r in recs if r["kind"] == "start" or r["t"] <= 1000.0 + 45.0]
    screen = wd.render_summary(mid, mid[-1]["t"] + 1.0, minutes=15.0)
    line = next(x for x in screen.splitlines() if x.startswith("POINTS "))
    assert line.startswith("POINTS now 1 sent, 1 held: D1 held on the foreground "), line
    assert " | 15 min: sent at " in line and ", held at " in line
    n_sent = sum(1 for r in mid[1:] if r["health"]["points"]["sent"])
    n_held = sum(1 for r in mid[1:] if r["health"]["points"]["held"])
    assert line.endswith(f"sent at {n_sent} of {len(mid) - 1} polls, held at {n_held}")
    assert all(len(x) <= 100 for x in screen.splitlines()) and len(screen.splitlines()) <= 24
    # at the end (nothing sent for 90 s): the window still shows the minute of points
    end = wd.render_summary(recs, recs[-1]["t"] + 1.0, minutes=15.0)
    last = next(x for x in end.splitlines() if x.startswith("POINTS "))
    assert last.startswith("POINTS now none sent | 15 min: sent at ")
    # an older build (no « points » in its StatsTick): the screen is exactly the one it was, no POINTS line
    old = _records(out, wd, strip_points=True)
    a = wd.render_summary(old, old[-1]["t"] + 1.0, minutes=15.0)
    assert "POINTS" not in a
    b = [x for x in end.splitlines() if not x.startswith("POINTS ")]
    assert a.splitlines() == b
    # nothing sent in the window: no POINTS line either
    quiet = [recs[0]] + [r for r in recs[1:] if r["t"] > 1000.0 + 100.0]
    assert "POINTS" not in wd.render_summary(quiet, quiet[-1]["t"] + 1.0, minutes=1.0)
    _numbers("c_watch", polls=len(recs) - 1, mid_line=line, end_line=last,
             old_build_screen_identical=a.splitlines() == b)


# ------------------------------------------------------------------------- where nothing goes out (review F4h-2)
def test_standby_mid_hold_says_nothing_is_sent(monkeypatch):
    """STANDBY in the middle of the hold: no frame is processed, so the processor's record stays frozen on « D1 held
    on the foreground » and OSC sends nothing.  Nothing may then say a point is sent: Dancers reads as before (the
    tracker's 0), points none, the ids readout says « (nothing sent) » with no running time, the watch « none sent »,
    no alert.  Back in RUN the held point is counted again."""
    wd = _wdwatch()
    out, alerts, _app_ = replay(_synthetic(), monkeypatch, standby=(20.0, 30.0))
    frozen = [o for o in out if o["standby"]]
    assert len(frozen) == 10 and all(o["row"]["st"] == "fg" for o in frozen)      # mid-hold, the record on fg
    for o in frozen:
        p = o["status"]["latest"]["StatsTick"]["payload"]
        assert p["num_dancers"] == 0 and p["points"] == {"sent": 0, "held": 0, "slots": []}, (o["t"], p)
        assert PR.dancers_text(p["num_dancers"], p["points"]) == "0"
        assert p["slots_text"] == "ids: D1 held on the foreground | D2 lost   belt: ready   (nothing sent)"
        line = o["status"]["health"]["line"]
        assert "point sent" not in line and "points sent" not in line and "detector:" not in line, line
    assert not [a for t, _r, a in alerts if 20.0 <= t < 30.0]
    recs = [r for r in _records(out, wd, every_s=1.0) if r["kind"] == "start" or r["t"] <= 1000.0 + 29.95]
    screen = wd.render_summary(recs, recs[-1]["t"] + 0.5, minutes=15.0)        # now: the last STANDBY poll
    assert next(x for x in screen.splitlines() if x.startswith("POINTS ")).startswith("POINTS now none sent | ")
    back = [o for o in out if 31.0 <= o["t"] < 60.0]
    assert back and all(o["status"]["latest"]["StatsTick"]["payload"]["points"]["held"] == 1 for o in back)
    _numbers("b_standby", polls=len(frozen), dancers=[o["status"]["latest"]["StatsTick"]["payload"]["num_dancers"]
                                                      for o in frozen], slots_text=frozen[-1]["status"]["latest"]
             ["StatsTick"]["payload"]["slots_text"], watch=next(x for x in screen.splitlines()
                                                              if x.startswith("POINTS ")))


def test_osc_off_mid_hold_reads_as_before(monkeypatch):
    """RUN with OSC off over the hold (and the 30 s no-detection mark): the slot table runs but nothing goes out.
    The alert keeps its old wording and data, Dancers the tracker's count, points none, no « detector » row."""
    out, alerts, app = replay(_synthetic(), monkeypatch, osc_off=(20.0, 62.0))
    off = [o for o in out if o["osc_off"]]
    assert len(off) == 42 and sum(1 for o in off if o["row"]["st"] in PR.HELD_STATES) >= 40
    for o in off:
        p = o["status"]["latest"]["StatsTick"]["payload"]
        assert p["num_dancers"] == 0 and p["points"]["sent"] == 0, (o["t"], p)
        if o["row"]["st"] != "lost":
            assert p["slots_text"].endswith("belt: ready   (nothing sent)"), p["slots_text"]
        assert "point sent" not in o["status"]["health"]["line"] and "detector:" not in o["status"]["health"]["line"]
    during = [a for t, _r, a in alerts if 20.0 <= t < 62.0]
    assert during and all(a.message.startswith("No dancers tracked for ") and a.message.endswith("s while RUNNING")
                          and set(a.data) == {"seconds"} for a in during), [a.message for a in during]
    _numbers("b_osc_off", polls=len(off), alerts=[a.message for a in during])


def test_emit_records_what_went_out():
    """pipeline._emit keeps ``last_sent`` = the very list it handed OSC this frame, None when nothing went out (OSC
    off, the lag's warm-up at L > 1); reset_output forgets it; a sender that could not open sends nothing.  The
    reports (points_report.sent_tracks) read only that."""
    pipeline = pytest.importorskip("core.pipeline")
    FP, ST = pipeline.FrameProcessor, pipeline.ScaledTrack

    class Osc:
        enabled = True

        def __init__(self):
            self.frames = []

        def send_frame(self, tracks, w, h):
            self.frames.append(tracks)

    fp = object.__new__(FP)
    fp.osc, fp.settings, fp._slots, fp._output_smoother, fp._osc_last = Osc(), SimpleNamespace(osc_send_state=False), \
        None, None, None
    fp.stall, fp.tracker, fp._belt_detector = SimpleNamespace(frames=0), SimpleNamespace(logger=None, max_age=30), None

    def out(x):
        c = np.array([x, 875.0])
        return [ST(track_id=1, keypoints=np.zeros((17, 2)), confidence=np.ones(17),
                   bbox=np.array([x - 30.0, 800.0, 60.0, H]), history=[], velocity=np.zeros(2), smoothed_centroid=c,
                   frames_since_skeleton=0, centroid_raw=c.copy(), slot_state="fg", slot_age_s=1.0)]

    o = out(877.0)
    fp._emit(o, 1260, 1824, 1, True, False, True)                      # L = 1, OSC on: the slot stream goes out
    assert fp.osc.frames[-1] is o and fp.last_sent is o and PR.sent_tracks(fp) == o
    fp._emit(out(878.0), 1260, 1824, 1, False, False, True)           # OSC off: nothing goes out
    assert len(fp.osc.frames) == 1 and fp.last_sent is None and PR.sent_tracks(fp) is None
    warm = sent = 0
    for k in range(6):                                                 # L = 3: the warm-up, then the lagged stream
        n = len(fp.osc.frames)
        fp._emit(out(880.0 + k), 1260, 1824, 3, True, True, True)
        if len(fp.osc.frames) == n:
            warm += 1
            assert fp.last_sent is None and PR.sent_tracks(fp) is None
        else:
            sent += 1
            assert fp.last_sent is fp.osc.frames[-1] and PR.points_summary(PR.sent_tracks(fp))["held"] == 1
    assert warm >= 1 and sent >= 1
    fp.osc.enabled = False                                             # a sender that could not open
    assert PR.sent_tracks(fp) is None
    fp.osc.enabled = True
    fp.reset_output()
    assert fp.last_sent is None and PR.sent_tracks(fp) is None


def test_stats_fields_and_ids_words_when_nothing_is_sent():
    held = [SimpleNamespace(track_id=1, slot_state="fg", slot_age_s=12.34)]
    assert PR.stats_fields(held, []) == {"num_dancers": 1, "points": PR.points_summary(held)}
    assert PR.stats_fields(None, [object(), object()]) == {"num_dancers": 2, "points": PR.points_summary([])}
    assert PR.stats_fields([], [object()])["num_dancers"] == 0          # OSC sends an empty frame: 0 sent
    assert PR.slots_text([(1, "fg", 12.34), (2, "lost", 0.0)], "ready", 2, sending=False) == \
        "ids: D1 held on the foreground | D2 lost   belt: ready   (nothing sent)"
    assert PR.slots_text([(1, "lost", 1.0), (2, "lost", 0.0)], "ready", 2, sending=False) == \
        "ids: D1 lost | D2 lost   belt: ready"                         # nothing claims a point: as before
