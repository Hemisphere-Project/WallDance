"""P6 (Plan Cordee): the gate driver ends its frames as the app does, and can show a release in RUN (re-read
5de60e5, lens b1 finding 5).

``tests/switch_replay.py`` never called ``FrameProcessor.frame_ended``: in its runs every paced release waited its
full 0.2 s and went at whatever phase, so no gate run could show where a release lands.  Now each frame that ran
the network ends after its output (where ``MainLoop._guarded_tick`` ends it), ``--mode-off-at-s`` sets the
automatic mode to off in RUN (the second engine then goes through the paced release), and each paced release is
reported with the frame it followed and the ms from that frame's end to its start and end.

CPU: the driver itself needs the GPU (the bench run is its proof); here, its source and its report.
"""
import ast
from pathlib import Path

DRIVER = Path(__file__).resolve().parent / "switch_replay.py"


def _func(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(node, attr):
    return [c for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            and c.func.attr == attr]


def test_the_driver_ends_each_frame_after_its_output_as_the_app_does():
    """In the frame loop, ``proc.frame_ended()`` once, after ``proc.process`` and the frame's bookkeeping."""
    play = _func(ast.parse(DRIVER.read_text()), "play")
    loop = next(n for n in ast.walk(play) if isinstance(n, ast.While))
    ended, process = _calls(loop, "frame_ended"), _calls(loop, "process")
    assert len(ended) == 1, "the driver never ends a frame: every paced release waits its full 0.2 s"
    assert process and ended[0].lineno > max(c.lineno for c in process)
    assert ended[0].lineno > max(c.lineno for c in _calls(loop, "append") if c.func.value.id == "frames")


def test_the_driver_can_set_the_mode_off_in_run():
    """``--mode-off-at-s``: one option, and ``set_mode("off")`` in the frame loop."""
    tree = ast.parse(DRIVER.read_text())
    opts = [c.args[0].value for c in _calls(_func(tree, "main"), "add_argument") if c.args]
    assert "--mode-off-at-s" in opts, "no way to cause a release in RUN"
    loop = next(n for n in ast.walk(_func(tree, "play")) if isinstance(n, ast.While))
    assert [c.args[0].value for c in _calls(loop, "set_mode")] == ["off"]


def test_a_release_is_reported_with_the_frame_it_followed_and_the_ms_after_its_end():
    import switch_replay
    ends = [(10, 100.000), (11, 100.059), (12, 100.118)]
    releases = [{"t": 250.1, "what": "second engine", "thread": "SwipeAuto", "in_take": True,
                 "start": 100.0605, "end": 100.1125, "frame_end_t": 100.059}]
    (row,) = switch_replay.release_rows(releases, ends)
    assert row["after_frame"] == 11 and row["waited_for_that_end"]
    assert row["start_ms_after_frame_end"] == 1.5 and row["end_ms_after_frame_end"] == 53.5
    assert row["t"] == 250.1 and row["what"] == "second engine"
