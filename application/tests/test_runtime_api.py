"""Tests for the Phase 3 command/event seam (runtime/api.py + ui/adapter.py).

Headless: imports runtime.api (stdlib-only) and inspects gui.py /
ui/adapter.py / app.py as *source text* -- never imports dpg. The static
checks encode the DECOMPOSITION_PLAN rules:

* every callback name gui.py consumes is provided by the adapter's dict
  (a silently dropped GUI sync path is the #1 Phase 3 risk);
* every command the adapter can emit exists in runtime.api and has a
  handler registered in app.py;
* import purity: core/ never imports runtime//ui//dpg, runtime/ never
  imports ui//dpg, and app.py no longer imports dpg at all.
"""
import json
import pathlib
import re
import threading

import pytest

from runtime import api

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


# ======================================================================
# RuntimeAPI (command queue)
# ======================================================================

def test_drain_dispatches_fifo():
    rt = api.RuntimeAPI()
    seen = []
    rt.register(api.SetSensitivity, lambda c: seen.append(("sens", c.value)))
    rt.register(api.SaveConfig, lambda c: seen.append(("save", c.name)))
    rt.submit(api.SetSensitivity(30.0))
    rt.submit(api.SaveConfig())
    rt.submit(api.SetSensitivity(70.0))
    assert rt.pending() == 3
    assert rt.drain() == 3
    assert seen == [("sens", 30.0), ("save", None), ("sens", 70.0)]
    assert rt.pending() == 0
    assert rt.drain() == 0


def test_drain_snapshots_queue():
    """Commands submitted by a handler run on the *next* drain (bounds
    re-entrancy from handlers that pump the GUI)."""
    rt = api.RuntimeAPI()
    rt.register(api.BgCapture, lambda c: rt.submit(api.BgClear()))
    rt.register(api.BgClear, lambda c: None)
    rt.submit(api.BgCapture())
    assert rt.drain() == 1
    assert rt.pending() == 1
    assert rt.drain() == 1


def test_unregistered_command_dropped_loudly(capsys):
    rt = api.RuntimeAPI()
    rt.submit(api.Quit())
    assert rt.drain() == 1
    assert "no handler registered for Quit" in capsys.readouterr().out


def test_handler_exception_contained(capsys):
    rt = api.RuntimeAPI()
    rt.register(api.Quit, lambda c: 1 / 0)
    rt.register(api.BgClear, lambda c: c)
    rt.submit(api.Quit())
    rt.submit(api.BgClear())
    assert rt.drain() == 2  # the second handler still ran
    assert "handler failed for Quit" in capsys.readouterr().out


def test_duplicate_registration_rejected():
    rt = api.RuntimeAPI()
    rt.register(api.Quit, lambda c: None)
    with pytest.raises(ValueError):
        rt.register(api.Quit, lambda c: None)


def test_submit_thread_safe():
    rt = api.RuntimeAPI()
    count = [0]
    rt.register(api.SetSensitivity, lambda c: count.__setitem__(0, count[0] + 1))
    threads = [threading.Thread(
        target=lambda: [rt.submit(api.SetSensitivity(1.0)) for _ in range(200)])
        for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    drained = 0
    while rt.pending():
        drained += rt.drain()
    assert drained == 8 * 200
    assert count[0] == 8 * 200


# ======================================================================
# EventBus
# ======================================================================

def test_bus_fan_out_in_subscribe_order():
    bus = api.EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(("a", e)))
    bus.subscribe(lambda e: seen.append(("b", e)))
    ev = api.Toast("hi")
    bus.publish(ev)
    assert seen == [("a", ev), ("b", ev)]


def test_bus_subscriber_exception_contained(capsys):
    bus = api.EventBus()
    seen = []
    bus.subscribe(lambda e: 1 / 0)
    bus.subscribe(seen.append)
    bus.publish(api.GpuStats())
    assert len(seen) == 1
    assert "subscriber failed on GpuStats" in capsys.readouterr().out


def test_bus_ui_ready_default_false():
    assert api.EventBus().ui_ready is False


# ======================================================================
# Command validation
# ======================================================================

@pytest.mark.parametrize("bad", [
    lambda: api.SetState("paused"),
    lambda: api.SetEnhanceParam("contrast", 1.0),
    lambda: api.SetIdsParam("fps", 1.0),
    lambda: api.ToggleOverlay("halo"),
    lambda: api.PlaybackControl("rewind"),
    lambda: api.ControlSync("dial", "x", 1),
])
def test_member_validation(bad):
    with pytest.raises(ValueError):
        bad()


# ======================================================================
# Event JSON-serializability (PreviewFrame is the documented exception)
# ======================================================================

EVENT_SAMPLES = {
    api.StateChanged: dict(state="run"),
    api.StatsTick: dict(payload={"fps": 19.7, "timing": {"yolo": 31.0}}),
    api.OutputLatency: dict(latency_ms=150.0, enabled=True),
    api.GpuStats: {},
    api.CameraStatus: dict(is_open=True, source="ids"),
    api.CameraSources: dict(sources=["ids", "0"], current="ids", unavailable=["1"]),
    api.CameraType: dict(camera_type="IDS_PEAK"),
    api.CameraDimensions: dict(width=1920, height=1080),
    api.EngineBadge: dict(is_trt=True),
    api.TrtBanner: dict(text=None),
    api.TrtCheckbox: dict(enabled=False),
    api.ComputeModeBadge: dict(reason=""),
    api.ModelDropdown: dict(name="yolo11x-pose"),
    api.ModelLoadModal: dict(message="Preparing..."),
    api.ModelLoadProgress: dict(message="Loading", progress=0.5),
    api.ModelLoadModalHide: {},
    api.CalibProgress: dict(text="Calibrating 40%"),
    api.CalibReportCard: dict(summary="median height 212px"),
    api.Calib2PoolChanged: dict(rows=[{"path": "p", "label": "l", "stale": False}],
                                proposal="height 210px"),
    api.Calib2ProposalUpdated: dict(proposal="height 210px (2 runs)"),
    api.AimCalibStateChanged: dict(text="Last calibrated · Aim: 5 min ago (gamma, var)"),
    api.ConfigSaved: dict(message="Saved!"),
    api.ConfigList: dict(configs=[("today 12:00", "/p/c.json")]),
    api.CurrentConfig: dict(display="today 12:00"),
    api.ProjectList: dict(projects=["a", "b"], current="a"),
    api.ProjectPicker: dict(rows=[("a", "today", 3)], last_project="a"),
    api.SaveConfigDialog: dict(project="default"),
    api.LoadConfigDialog: dict(config_dir="/cfg", project="default"),
    api.ActiveProfile: dict(name="show"),
    api.RecordingUi: dict(payload={"state": "live", "current_slot": 0,
                                   "slots_info": [(1, True)]}),
    api.SlotHistory: dict(slot=3, recordings=[("today", "/r/x.avi")]),
    api.BgStatus: dict(has_reference=True, enabled=True),
    api.ControlSync: dict(kind="slider", name="confidence", value=0.35),
    api.Toast: dict(message="hello"),
    api.ReadinessResult: dict(rows=[
        {"name": "osc", "status": "warn", "detail": "probe sent (UDP)"}]),
    api.DryRunResult: dict(summary={"frames_processed": 300, "real_tracks": 1}),
    api.CalibSweepResult: dict(result={"best_clahe": 6.0, "best_conf": 0.5}),
    api.DialBVisible: dict(visible=False),
    api.Alert: dict(kind="fps_low", message="FPS 9.8", data={"fps": 9.8}),
    api.IssueReportContext: dict(context={"frame": 12, "slot": 3}),
    api.QrDialog: dict(url="http://x", matrix=[[True, False]]),
    api.PreviewResize: dict(width=960, height=540),
}


def _all_event_types():
    return [cls for cls in vars(api).values()
            if isinstance(cls, type) and issubclass(cls, api.Event)
            and cls is not api.Event]


def test_every_event_type_has_a_sample():
    missing = [cls.__name__ for cls in _all_event_types()
               if cls not in EVENT_SAMPLES and cls is not api.PreviewFrame]
    assert not missing, f"add EVENT_SAMPLES for: {missing}"


@pytest.mark.parametrize("cls", sorted(EVENT_SAMPLES, key=lambda c: c.__name__),
                         ids=lambda c: c.__name__)
def test_event_json_serializable(cls):
    event = cls(**EVENT_SAMPLES[cls])
    payload = event.to_dict()
    assert payload["type"] == cls.__name__
    json.dumps(payload)


def test_preview_frame_to_dict_summarizes():
    class _Arr:
        shape = (540, 960, 3)
    payload = api.PreviewFrame(_Arr()).to_dict()
    assert payload == {"type": "PreviewFrame", "shape": [540, 960, 3]}
    json.dumps(payload)


# ======================================================================
# Static wiring checks (source-text level; never imports dpg)
# ======================================================================

# gui.py guards this one with `if ... in self.callbacks` and app.py never
# provided it -- a pre-existing dead callback (expert max-persons widget),
# deliberately NOT wired through the seam to stay behavior-neutral.
KNOWN_UNWIRED = {"on_max_persons_change"}


def _gui_consumed_callback_names():
    src = (SRC / "gui.py").read_text(encoding="utf-8")
    names = set(re.findall(r"self\.callbacks\[['\"](\w+)['\"]\]", src))
    names |= set(re.findall(r"self\.callbacks\.get\(['\"](\w+)['\"]", src))
    names |= set(re.findall(r"['\"](\w+)['\"] (?:not )?in self\.callbacks", src))
    return names


def _adapter_provided_callback_names():
    src = (SRC / "ui" / "adapter.py").read_text(encoding="utf-8")
    block = src[src.index("def _build_callbacks"):]
    block = block[:block.index("def _handle_key")]
    return set(re.findall(r"['\"]([\w]+)['\"]\s*:", block))


def test_adapter_covers_gui_callback_surface():
    consumed = _gui_consumed_callback_names()
    provided = _adapter_provided_callback_names()
    missing = consumed - provided - KNOWN_UNWIRED
    assert not missing, f"gui.py consumes callbacks the adapter never provides: {sorted(missing)}"
    stale = provided - consumed
    assert not stale, f"adapter provides callbacks gui.py never consumes: {sorted(stale)}"


def test_adapter_commands_exist_and_are_registered():
    adapter_src = (SRC / "ui" / "adapter.py").read_text(encoding="utf-8")
    app_src = (SRC / "app.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r"(?<![.\w])api\.(\w+)\(", adapter_src))
    for name in sorted(emitted):
        assert hasattr(api, name), f"ui/adapter.py references api.{name} which does not exist"
    command_types = {name for name in emitted
                     if isinstance(getattr(api, name), type)
                     and issubclass(getattr(api, name), api.Command)}
    unregistered = {name for name in command_types
                    if not re.search(rf"api\.{name}\b", app_src)}
    assert not unregistered, (
        f"adapter emits commands app.py never registers: {sorted(unregistered)}")


def test_every_command_type_is_registered_in_app():
    app_src = (SRC / "app.py").read_text(encoding="utf-8")
    commands = [cls.__name__ for cls in vars(api).values()
                if isinstance(cls, type) and issubclass(cls, api.Command)
                and cls is not api.Command]
    missing = [n for n in commands if not re.search(rf"api\.{n}\b", app_src)]
    assert not missing, f"commands with no handler registration in app.py: {missing}"


# ======================================================================
# Import purity (DECOMPOSITION_PLAN §4 rules)
# ======================================================================

def _violations(directory: pathlib.Path, patterns):
    bad = []
    for path in sorted(directory.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            if re.search(pat, text, re.MULTILINE):
                bad.append(f"{path.name}: {pat}")
    return bad


def test_core_imports_nothing_above_it():
    assert not _violations(SRC / "core", [
        r"^\s*import dearpygui", r"^\s*from dearpygui",
        r"^\s*from runtime", r"^\s*import runtime",
        r"^\s*from ui", r"^\s*import ui\b",
    ])


def test_runtime_never_imports_ui_or_dpg():
    assert not _violations(SRC / "runtime", [
        r"^\s*import dearpygui", r"^\s*from dearpygui",
        r"^\s*from ui", r"^\s*import ui\b",
    ])


def test_app_no_longer_imports_dpg():
    src = (SRC / "app.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import dearpygui|^\s*from dearpygui", src,
                         re.MULTILINE), "app.py must reach dpg only via ui/adapter.py"
    assert not re.search(r"\bdpg\.", src), "app.py must reach dpg only via ui/adapter.py"
