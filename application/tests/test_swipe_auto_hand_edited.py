"""P6 (Plan Cordee), 28/11: a project FILE whose ``swipe_auto`` was edited by hand into no mode loads off and says so.

``test_swipe_auto_lock`` proves it through the schema's functions on a flat dict.  This one takes the road a show
night takes: a real WallDanceApp, headless and on the CPU, its real ConfigManager and ConfigStore in tmp_path (the
fixture of ``test_new_project_defaults``), a project file on disk, edited by hand, then read by the real project
switch -- the store's load, the schema's migrate / flatten / validate, the D33 fill, the app's full apply.  With
« suggest » the default, an unreadable setting must never turn the background job on: off, one log line, one toast,
and the next Save writes off.
"""
from __future__ import annotations

import json

import pytest

from test_new_project_defaults import _app

UNREADABLE = "this project's setting could not be read"


@pytest.mark.parametrize("value", ["always", "Suggest please", 1, None])
def test_a_project_file_edited_by_hand_into_no_mode_loads_off_and_says_so(tmp_path, monkeypatch, capsys, value):
    """The project ran on suggest; its file is edited by hand (« always », a typo, a number, null) and loaded again
    through the real switch: the mode is off, the log and a toast say the setting could not be read, the schema's
    console line quotes the value, and Save writes off."""
    pytest.importorskip("core.swipe_auto")
    s = _app(tmp_path, monkeypatch)
    events = []
    s.app.bus.subscribe(events.append)
    try:
        from core import config_schema
        flat = dict(s.app._get_saveable_config(), swipe_auto="suggest")
        path = s.store.save("handed", config_schema.structure(flat, {}, "show"))
        assert s.mgr._execute_project_switch(path)
        assert s.app.swipe_bg.mode == "suggest"            # the session runs suggest: off must be read, not kept
        with open(path) as f:
            raw = json.load(f)
        assert raw.get("swipe_auto") == "suggest", "the project file does not hold the key where Save writes it"
        raw["swipe_auto"] = value                          # the hand edit
        with open(path, "w") as f:
            json.dump(raw, f, indent=2)
        capsys.readouterr()
        events.clear()
        assert s.mgr._execute_project_switch(path)
        out = capsys.readouterr().out
        assert s.app.swipe_bg.mode == "off", f"a setting nobody could read runs {s.app.swipe_bg.mode}"
        assert out.count(f"[SwipeAuto] mode off - {UNREADABLE}") == 1
        assert f"[Config] swipe_auto: invalid value {value!r} read as off" in out
        assert "(the default)" not in out and "(from the project)" not in out
        toasts = [e.message for e in events if type(e).__name__ == "Toast"]
        assert toasts.count(f"Automatic swipe: off - {UNREADABLE}") == 1, toasts
        assert s.app._get_saveable_config().get("swipe_auto", "no key") == "off"
    finally:
        s.app.swipe_bg.stop(1.0)
        s.app.swipe_switch.stop(1.0)
