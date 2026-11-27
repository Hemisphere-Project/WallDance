"""P6 (Plan Cordee): a new project starts with the automatic swipe at the build's default (off).

Start blank and New project (the picker's commands) do a partial apply that never names ``swipe_auto`` (the key
is saved only when not off): a session in suggest stayed in suggest in the new project, and New's first Save
wrote it.  Now their clean start (``_cb_project_start_clean``) sets ``core.config.SWIPE_AUTO``; a project switch
or a camera switch -- the same clean start -- keeps the mode.  On a real ``WallDanceApp`` (no GUI, CPU): the
model load and the camera rig of the blank start are stubbed, the rest is the app's."""
import pytest

from test_swipe_auto_live import swipe_auto_unlocked  # noqa: F401 - the fixture
from test_swipe_auto_lock import real_app  # noqa: F401 - the fixture


def _stub_blank_start(app, ok=True):
    app.models._load_default_model_startup = lambda: ok
    app.configs._inherit_camera_rig = lambda: None
    saved = []
    app.configs._cb_do_save_config = lambda name: saved.append((name, app._get_saveable_config()))
    return saved


@pytest.mark.parametrize("which", ["blank", "new"])
def test_start_blank_and_new_project_start_with_the_swipe_off(real_app, swipe_auto_unlocked, which):  # noqa: F811
    """Fails before: the mode stays suggest, and New's first Save writes ``swipe_auto: suggest``."""
    from runtime import api
    app, events = real_app
    app._cb_swipe_auto("suggest")
    assert app.swipe_bg.mode == "suggest"
    saved = _stub_blank_start(app)
    app.api.submit(api.StartBlankProject() if which == "blank" else api.NewProject(name="c2_new_project_probe"))
    app.api.drain()
    assert app.swipe_bg.mode == "off"
    if which == "new":
        assert [name for name, _ in saved] == ["c2_new_project_probe"]
        assert "swipe_auto" not in saved[0][1]                         # its first Save: no key, off
    assert getattr(app, "_swipe_blank_start", False) is False


def test_a_blank_start_that_fails_keeps_the_mode(real_app, swipe_auto_unlocked):  # noqa: F811
    """Regression guard: a failed model load keeps what was there (``_failed_switch_not_live``)."""
    from runtime import api
    app, events = real_app
    app._cb_swipe_auto("suggest")
    _stub_blank_start(app, ok=False)
    app.configs._failed_switch_not_live = lambda: None
    app.api.submit(api.StartBlankProject())
    app.api.drain()
    assert app.swipe_bg.mode == "suggest" and getattr(app, "_swipe_blank_start", False) is False


def test_a_project_switch_or_a_camera_switch_keeps_the_mode(real_app, swipe_auto_unlocked):  # noqa: F811
    """Regression guard: the same clean start after a project's full apply (its key) or a camera switch."""
    app, events = real_app
    app._apply_config_without_model({"swipe_auto": "suggest"})
    app._cb_project_start_clean()
    assert app.swipe_bg.mode == "suggest"
