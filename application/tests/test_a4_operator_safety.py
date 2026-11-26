"""A4 (Plan Cordée, #t-025 ① ② ③): operator-safety quick fixes, headless.

① gui-operator#1: a single-key shortcut is ignored while a text or number field is being edited
   (« PIR130 » typed into the rig sheet used to freeze the preview and toggle overlays).
② gui-operator#5: RUN/STANDBY has one source of truth, the runtime; a click only asks, and the badge
   and buttons follow StateChanged, so a change made through the remote API shows on screen.
③ gui-operator#3: a red « LECTURE - pas en direct » band whenever RUN plays a recording.

The real WallDanceGUI, built by the real DpgUiAdapter in a viewport-less DearPyGui context, the real
EventBus / RuntimeAPI, the real remote API over loopback HTTP and the real
WallDanceApp._cmd_set_state on a stub app.  Without a viewport DPG never marks an item active, so
« the operator is typing into this field » is the one thing simulated: dpg.is_item_active answers
True for that field.
"""
import ast
import json
import pathlib
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")

from runtime import api  # noqa: E402
from runtime.api import SystemState  # noqa: E402

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
TYPED = "PIR130 BP850 IR pass filter"


@pytest.fixture
def ui():
    from ui.adapter import DpgUiAdapter
    rt, bus = api.RuntimeAPI(), api.EventBus()
    adapter = DpgUiAdapter(rt, bus)
    adapter.create_gui({"camera_type": "IDS_PEAK"})
    try:
        yield SimpleNamespace(rt=rt, bus=bus, adapter=adapter, gui=adapter.gui)
    finally:
        dpg.destroy_context()


def _queued(rt):
    """The commands waiting in the queue, consumed (the queue is the GUI -> runtime seam)."""
    with rt._lock:
        out = list(rt._queue)
        rt._queue.clear()
    return out


def _key(ch):
    return dpg.mvKey_Spacebar if ch == " " else getattr(dpg, f"mvKey_{ch.upper()}")


def _typing_into(monkeypatch, tag):
    active = dpg.get_alias_id(tag) if isinstance(tag, str) else tag
    monkeypatch.setattr(dpg, "is_item_active", lambda item: item == active)


def _keys_down(monkeypatch, *keys):
    monkeypatch.setattr(dpg, "is_key_down", lambda k: k in keys)


# ----------------------------------------------------------------------------------------------
# ① shortcuts while typing
# ----------------------------------------------------------------------------------------------

def test_typing_into_the_rig_sheet_fires_no_shortcut(ui, monkeypatch):
    _typing_into(monkeypatch, "rig_filter_input")
    for ch in TYPED:
        ui.adapter._handle_key(None, _key(ch))
    assert _queued(ui.rt) == []
    # the same keys out of a field are shortcuts again (the guard is the field, not the keys)
    _typing_into(monkeypatch, -1)
    for ch in "TSKBICP":
        ui.adapter._handle_key(None, _key(ch))
    sent = _queued(ui.rt)
    assert [type(c).__name__ for c in sent] == ["ToggleOverlay"] * 6 + ["TogglePreview"]
    assert [c.name for c in sent[:6]] == ["trails", "skeleton", "keypoints", "bbox", "ids", "ball"]


def test_every_text_and_number_field_of_the_window_and_the_save_dialog_is_guarded(ui, monkeypatch):
    from ui.key_guard import TEXT_FIELD_TYPES, text_field_active
    monkeypatch.setattr(ui.gui, "_center_modal", lambda *a: [0, 0])   # centring needs a viewport
    ui.gui.show_save_config_dialog("p")                       # the Save name field (audit: gui.py:2176)
    fields = [i for i in dpg.get_all_items() if dpg.get_item_type(i) in TEXT_FIELD_TYPES]
    tags = {dpg.get_item_alias(i) for i in fields}
    # the three the audit named, and every rig-sheet field
    for tag in ("rig_filter_input", "save_config_name_input", "osc_ip_input", "rig_notes_input",
                "rig_focal_mm_input", "osc_port_input"):
        assert tag in tags, tag
    assert len(fields) >= 16
    assert not text_field_active()
    for item in fields:
        _typing_into(monkeypatch, item)
        assert text_field_active(), dpg.get_item_alias(item)
        ui.adapter._handle_key(None, dpg.mvKey_P)
        assert _queued(ui.rt) == [], dpg.get_item_alias(item)


def test_ctrl_combinations_and_f8_still_work_while_typing(ui, monkeypatch):
    _typing_into(monkeypatch, "osc_ip_input")
    _keys_down(monkeypatch, dpg.mvKey_LControl)
    ui.adapter._handle_key(None, dpg.mvKey_S)
    assert [type(c).__name__ for c in _queued(ui.rt)] == ["SaveConfig"]
    _keys_down(monkeypatch, dpg.mvKey_LControl, dpg.mvKey_LShift)
    expert = ui.gui.expert_mode
    ui.adapter._handle_key(None, dpg.mvKey_E)
    assert ui.gui.expert_mode is (not expert)
    _keys_down(monkeypatch)
    ui.adapter._handle_key(None, dpg.mvKey_F8)
    assert [type(c).__name__ for c in _queued(ui.rt)] == ["RequestIssueReport"]


# ----------------------------------------------------------------------------------------------
# ② one RUN/STANDBY, the badge follows the runtime
# ----------------------------------------------------------------------------------------------

def _app_state_on(rt, bus, start=SystemState.RUN):
    """A stub app carrying the real WallDanceApp._cmd_set_state, registered on the queue."""
    import app as app_mod
    entered = []
    app = SimpleNamespace(system_state=start, bus=bus,
                          _cb_system_state_change=lambda s, o: entered.append((s, o)))
    rt.register(api.SetState, lambda c: app_mod.WallDanceApp._cmd_set_state(app, c))
    return app, entered


def _assert_shows(gui, state):
    from gui_builder import STATE_COLORS, STATE_LABELS
    assert gui.get_system_state() == state
    assert dpg.get_value("state_badge") == STATE_LABELS[state]
    assert tuple(dpg.get_item_configuration("state_badge")["color"]) == pytest.approx(
        tuple(c / 255 for c in STATE_COLORS[state][0]), abs=1e-3)
    standby = dpg.get_item_info("state_standby_btn")["theme"]
    run = dpg.get_item_info("state_run_btn")["theme"]
    if state == SystemState.RUN:
        assert (standby, run) == (gui._btn_standby_theme, gui._btn_run_active_theme)
    else:
        assert (standby, run) == (gui._btn_standby_active_theme, gui._btn_run_theme)


@pytest.fixture
def remote(ui, tmp_path):
    from services.remote_api import RemoteApi, load_or_create_token
    app, entered = _app_state_on(ui.rt, ui.bus)
    token = load_or_create_token(tmp_path / "tok")
    server = RemoteApi(ui.rt, ui.bus, token=token, roots={"projects": tmp_path},
                       state_fn=lambda: app.system_state.name.lower(), host="127.0.0.1", port=0)
    assert server.start()

    def post(type_name, **args):
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/api/v1/command",
            data=json.dumps({"type": type_name, "args": args}).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        yield SimpleNamespace(server=server, app=app, entered=entered, post=post)
    finally:
        server.stop()


def test_a_remote_state_change_turns_the_badge_and_the_buttons(ui, remote):
    _assert_shows(ui.gui, SystemState.RUN)
    # RUN, and the operator has not allowed remote control: refused, both sides stay RUN
    assert remote.post("SetState", state="standby") == 403
    ui.rt.drain()
    assert remote.app.system_state == SystemState.RUN
    _assert_shows(ui.gui, SystemState.RUN)
    # allowed (phase 6 Live): `wdremote state standby` before a heavy remote job
    remote.server.control_enabled = True
    assert remote.post("SetState", state="standby") == 200
    _assert_shows(ui.gui, SystemState.RUN)              # queued, not applied: the badge waits
    ui.rt.drain()
    assert remote.app.system_state == SystemState.STANDBY
    _assert_shows(ui.gui, SystemState.STANDBY)          # used to stay a green RUN
    assert remote.post("SetState", state="run") == 200
    ui.rt.drain()
    assert remote.app.system_state == SystemState.RUN
    _assert_shows(ui.gui, SystemState.RUN)
    assert remote.entered == [(SystemState.STANDBY, SystemState.RUN),
                              (SystemState.RUN, SystemState.STANDBY)]


def test_a_click_only_asks_and_the_badge_follows_the_runtime(ui):
    app, entered = _app_state_on(ui.rt, ui.bus)
    ui.gui._on_state_standby()                          # the STANDBY button's callback
    assert ui.rt.pending() == 1
    _assert_shows(ui.gui, SystemState.RUN)              # the GUI does not flip on its own
    ui.rt.drain()
    assert app.system_state == SystemState.STANDBY
    _assert_shows(ui.gui, SystemState.STANDBY)
    ui.gui._on_state_run()
    ui.rt.drain()
    assert app.system_state == SystemState.RUN
    _assert_shows(ui.gui, SystemState.RUN)
    assert entered == [(SystemState.STANDBY, SystemState.RUN), (SystemState.RUN, SystemState.STANDBY)]


def test_the_gui_starts_on_the_runtime_state():
    from ui.adapter import DpgUiAdapter
    rt, bus = api.RuntimeAPI(), api.EventBus()
    adapter = DpgUiAdapter(rt, bus)
    bus.publish(api.StateChanged("standby"))            # the runtime's, before the GUI exists
    adapter.create_gui({"camera_type": "IDS_PEAK"})
    try:
        _assert_shows(adapter.gui, SystemState.STANDBY)
    finally:
        dpg.destroy_context()


def _writers(path, cls, attr):
    """The methods of class ``cls`` in ``path`` that assign self.<attr>."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    found = set()
    for fn in klass.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else [])
            for t in targets:
                if (isinstance(t, ast.Attribute) and t.attr == attr
                        and isinstance(t.value, ast.Name) and t.value.id == "self"):
                    found.add(fn.name)
    return found


def test_one_writer_of_run_standby_on_each_side():
    assert _writers(SRC / "app.py", "WallDanceApp", "system_state") == {"__init__", "_cmd_set_state"}
    assert _writers(SRC / "gui.py", "WallDanceGUI", "_system_state") == {"__init__", "show_system_state"}
    # the runtime publishes its starting state right where it sets it (the GUI starts on it)
    init = ast.get_source_segment(
        (SRC / "app.py").read_text(encoding="utf-8"),
        next(f for n in ast.parse((SRC / "app.py").read_text(encoding="utf-8")).body
             if isinstance(n, ast.ClassDef) and n.name == "WallDanceApp"
             for f in n.body if isinstance(f, ast.FunctionDef) and f.name == "__init__"))
    assert "api.StateChanged(self.system_state" in init


# ----------------------------------------------------------------------------------------------
# ③ the playback banner
# ----------------------------------------------------------------------------------------------

def _source(bus, state, paused=False):
    bus.publish(api.RecordingUi(dict(
        state=state, current_slot=3, slots_info=[(i, i == 3) for i in range(1, 10)],
        recording_frames=0, playback_frame=120, playback_total=900, playback_fps=30.0,
        paused=paused, playback_speed=1.0)))


def _banner():
    return bool(dpg.get_item_configuration("playback_banner_window")["show"])


def test_the_banner_shows_whenever_run_plays_a_recording(ui):
    text = dpg.get_value("playback_banner_text")
    assert "LECTURE" in text and "pas en direct" in text
    assert not _banner()                                # boot: RUN on the camera
    _source(ui.bus, "playing")                          # one click on a slot, in RUN
    assert _banner()
    _source(ui.bus, "playing", paused=True)             # paused on the take: still not the camera
    assert _banner()
    ui.bus.publish(api.StateChanged("standby"))         # STANDBY: the Calibrate-from-a-take flow
    assert not _banner()
    ui.bus.publish(api.StateChanged("run"))             # RUN again, still on the take
    assert _banner()
    _source(ui.bus, "live")                             # LIVE: the camera again
    assert not _banner()
    _source(ui.bus, "recording")                        # recording is the camera
    assert not _banner()
    _source(ui.bus, "armed")
    assert not _banner()


def test_the_banner_follows_a_remote_run_and_its_button_returns_to_the_camera(ui, remote):
    remote.server.control_enabled = True
    assert remote.post("SetState", state="standby") == 200
    ui.rt.drain()
    _source(ui.bus, "playing")
    assert not _banner()
    assert remote.post("SetState", state="run") == 200
    ui.rt.drain()
    assert _banner()
    dpg.get_item_callback("playback_banner_live_btn")()
    assert [(type(c).__name__, c.action) for c in _queued(ui.rt)] == [("PlaybackControl", "live")]
