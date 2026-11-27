"""P6 (Plan Cordee): a look that raises (the blind re-read of 4a30f39, lens B2, finding 7).

It wrote « error: <Class>: <message> » into /status ``last_look`` and had no back-off: its frames stayed fresh and
the next tick ran another 150 inferences -- 1,410 looks in a two-hour fake evening.  Now the class and the message
go to the log line, the status row says a plain sentence, the looks back off as after a « keep » (300 s, then
600 s), and the third failure in a row puts the setting aside as a run that failed does (the same ban, the same
sentences: ``BackgroundSwipe._not_run``).  CPU, with the fakes of ``test_swipe_auto_live``."""
from test_swipe_auto_rollback import _look, _Tap
from test_swipe_auto_suggestion import _evening_bg

LOOK_FAILED = "the automatic swipe waits: its last look failed, see the log"


def _raising(monkeypatch):
    import runtime.swipe_background as sb

    def score_runs(*a, **k):
        raise AttributeError("'_BgProc' object has no attribute 'swipe_lost_share'")
    monkeypatch.setattr(sb, "score_runs", score_runs)


def test_a_look_that_raises_says_a_plain_sentence_and_the_error_goes_to_the_log(monkeypatch, capsys):
    """Fails before: /status read « error: AttributeError: '_BgProc' object has no attribute ... »."""
    bg, proc, loader, live = _evening_bg()
    _raising(monkeypatch)
    tap = _Tap(range(3000))
    assert _look(bg, proc, tap, 150) == "run"
    assert bg.status()["last_look"]["outcome"] == LOOK_FAILED
    assert "AttributeError: '_BgProc' object has no attribute 'swipe_lost_share'" in capsys.readouterr().out


def test_after_a_failed_look_the_looks_back_off_and_the_third_in_a_row_puts_the_setting_aside(monkeypatch):
    """Failed at 150 s: no look before 450 s (300 s, as after a « keep »), failed again: none before 1050 s (600 s),
    failed a third time: the setting is put aside as after a run that failed -- banned, said once as the switch's
    fault, its resident released -- and no look runs for it any more.  Fails before: a look at every tick (151,
    152, ... s), 150 inferences each, and never put aside."""
    bg, proc, loader, live = _evening_bg()
    _raising(monkeypatch)
    tap = _Tap(range(3000))
    runs = [t for t in range(1, 1400) if _look(bg, proc, tap, t) == "run"]
    assert runs == [150, 450, 1050], runs[:10]
    assert loader.infers == 3 * 150
    assert bg.status()["auto_banned"] == ["yolo11l-pose_1280.engine"] and bg.resident is None
    assert bg.status()["last_look"]["outcome"].startswith("the automatic swipe waits: it could not run l @ 1280")
    assert bg.switch.fault and bg.switch.fault.startswith("the automatic swipe could not run l @ 1280")
    assert _look(bg, proc, tap, 1400) == "banned"


def test_a_look_that_completes_starts_the_count_over(monkeypatch):
    """Two failures, a look that goes through, two failures: nothing put aside (three in a row are needed), the
    looks backing off after each failure.  Fails before: a look at every tick after a failure."""
    import runtime.swipe_background as sb
    bg, proc, loader, live = _evening_bg()
    real = sb.score_runs
    fail = {"on": True}

    def score_runs(*a, **k):
        if fail["on"]:
            raise RuntimeError("scoring failed")
        return real(*a, **k)
    monkeypatch.setattr(sb, "score_runs", score_runs)
    tap = _Tap(range(6000))
    assert [t for t in range(1, 1000) if _look(bg, proc, tap, t) == "run"] == [150, 450]
    fail["on"] = False
    assert next(t for t in range(1000, 2000) if _look(bg, proc, tap, t) == "run") == 1050
    fail["on"] = True
    later = []
    for t in range(1051, 5000):
        if _look(bg, proc, tap, t) == "run":
            later.append(t)
            if len(later) == 2:
                break
    assert len(later) == 2 and bg.status()["auto_banned"] == [], "put aside after two failures in a row"
    assert bg.status()["last_look"]["outcome"] == LOOK_FAILED
