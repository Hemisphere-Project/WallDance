"""P6 (Plan Cordee): a new project starts with the automatic swipe at the build's default -- since 28/11 suggest
while the build releases it, else off (``core.swipe_auto.default_mode``) -- whatever the session ran before.

Start blank and New project: the manager's own door (``ConfigManager._cb_project_blank``, which New runs) sets the
mode once its load succeeded, before New's first Save, so the picker's commands and a caller going straight to the
manager get it alike, and the first Save writes the key.  Said once when the default is on, as a project's load says
its own mode.  A failed start keeps the mode; a project switch or a camera switch -- the same clean start -- keeps
it too.  On a real ``WallDanceApp`` (no GUI, CPU): the model load and the camera rig of the blank start are stubbed,
the looker thread is not started, the rest is the app's."""
import pytest

from test_swipe_auto_live import swipe_auto_off_only, swipe_auto_unlocked  # noqa: F401 - the fixtures
from test_swipe_auto_lock import _toasts, real_app  # noqa: F401 - the fixture

BY_DEFAULT = "Automatic swipe: {} (the default)"


def _stub_blank_start(app, ok=True):
    app.models._load_default_model_startup = lambda: ok
    app.configs._inherit_camera_rig = lambda: None
    starts = []
    app.swipe_bg.start = lambda: starts.append("start")     # the looker's thread: asked, not run here
    saved = []
    app.configs._cb_do_save_config = lambda name: saved.append((name, app._get_saveable_config()))
    return saved, starts


def _start(app, door, via):
    """Start blank / New project: the picker's command (ui.adapter submits it), or the manager's door itself."""
    from runtime import api
    if via == "picker":
        app.api.submit(api.StartBlankProject() if door == "blank" else api.NewProject(name="c2_new_project_probe"))
        app.api.drain()
    elif door == "blank":
        app.configs._cb_project_blank()
    else:
        app.configs._cb_project_new("c2_new_project_probe")


@pytest.mark.parametrize("before", ["off", "suggest"])
@pytest.mark.parametrize("via", ["picker", "manager"])
@pytest.mark.parametrize("door", ["blank", "new"])
def test_start_blank_and_new_project_start_at_the_default_and_save_it(real_app, door, via, before, capsys):
    """The build as it ships (off and suggest released): the new project runs suggest whatever the session ran,
    said once, and its first Save writes ``swipe_auto: suggest``.  Fails before: the new project started off,
    silently, and its first Save wrote no key; called on the manager directly, the mode stayed the session's."""
    app, events = real_app
    saved, starts = _stub_blank_start(app)
    app._cb_swipe_auto(before)
    assert app.swipe_bg.mode == before
    capsys.readouterr()
    events.clear()
    starts.clear()
    _start(app, door, via)
    assert app.swipe_bg.mode == "suggest", f"the new project starts {app.swipe_bg.mode}, not at the default"
    assert [t for t in _toasts(events) if t.startswith("Automatic swipe")] == [BY_DEFAULT.format("suggest")], \
        "the default is not said once at the new project"
    assert capsys.readouterr().out.count("[SwipeAuto] mode suggest (the default)") == 1
    assert starts == ([] if before == "suggest" else ["start"])   # from off: the looker asked to start, once
    if door == "blank":
        app.configs._cb_do_save_config("c2_blank_probe")            # its first Save
    assert len(saved) == 1 and saved[0][1].get("swipe_auto") == "suggest", \
        f"the new project's first Save wrote {saved[0][1].get('swipe_auto', 'no key') if saved else 'nothing'}"


@pytest.mark.parametrize("via", ["picker", "manager"])
@pytest.mark.parametrize("door", ["blank", "new"])
def test_from_auto_a_new_project_starts_at_the_default(real_app, swipe_auto_unlocked, door, via):  # noqa: F811
    """Every mode released (the ``swipe_auto_unlocked`` fixture): a session in auto starts the new project in
    suggest, the default.  Fails before: off by the picker, auto kept by the manager's door."""
    app, events = real_app
    saved, _ = _stub_blank_start(app)
    app._cb_swipe_auto("auto")
    _start(app, door, via)
    assert app.swipe_bg.mode == "suggest", f"the new project starts {app.swipe_bg.mode}, not at the default"


@pytest.mark.parametrize("via", ["picker", "manager"])
@pytest.mark.parametrize("door", ["blank", "new"])
def test_regression_guard_off_only_a_new_project_starts_off_and_starts_nothing(real_app, swipe_auto_off_only,  # noqa: F811
                                                                               door, via, capsys):
    """Off the only mode released: the default is off, the new project runs off, nothing of P6 is asked to start,
    nothing is said about the automatic swipe -- as before."""
    app, events = real_app
    saved, starts = _stub_blank_start(app)
    capsys.readouterr()
    _start(app, door, via)
    assert app.swipe_bg.mode == "off" and starts == [] and app.swipe_bg._thread is None
    assert not [t for t in _toasts(events) if t.startswith("Automatic swipe")]
    assert "SwipeAuto" not in capsys.readouterr().out


@pytest.mark.parametrize("door", ["blank", "new"])
def test_off_only_a_new_projects_first_save_writes_off(real_app, swipe_auto_off_only, door):  # noqa: F811
    """The one change for a locked build: the first Save writes ``swipe_auto: off`` (the key is written whatever the
    mode, 28/11).  Fails before: no key."""
    app, events = real_app
    saved, _ = _stub_blank_start(app)
    _start(app, door, "manager")
    if door == "blank":
        app.configs._cb_do_save_config("c2_blank_probe")
    assert saved[0][1].get("swipe_auto", "no key") == "off"


@pytest.mark.parametrize("via", ["picker", "manager"])
def test_a_blank_start_that_fails_keeps_the_mode(real_app, via):  # noqa: F811
    """Regression guard: a failed model load keeps what was there (``_failed_switch_not_live``), by either door."""
    app, events = real_app
    _stub_blank_start(app, ok=False)
    app.configs._failed_switch_not_live = lambda: None
    app._cb_swipe_auto("off")
    events.clear()
    _start(app, "blank", via)
    assert app.swipe_bg.mode == "off", "a failed start set the default"
    assert not [t for t in _toasts(events) if t.startswith("Automatic swipe")]


def test_a_project_switch_or_a_camera_switch_keeps_the_mode(real_app):  # noqa: F811
    """Regression guard: the same clean start after a project's full apply (its key) or a camera switch."""
    app, events = real_app
    app.swipe_bg.start = lambda: None
    app._apply_config_without_model({"swipe_auto": "off"})
    app._cb_project_start_clean()
    assert app.swipe_bg.mode == "off"
    app._apply_config_without_model({"swipe_auto": "suggest"})
    app._cb_project_start_clean()
    assert app.swipe_bg.mode == "suggest"
