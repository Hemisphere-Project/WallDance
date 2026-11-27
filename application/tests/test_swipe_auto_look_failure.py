"""P6 (Plan Cordee): a look that raises (the blind re-read of 4a30f39, lens B2, finding 7).

It wrote « error: <Class>: <message> » into /status ``last_look`` and had no back-off: its frames stayed fresh and
the next tick ran another 150 inferences -- 1,410 looks in a two-hour fake evening.  Now the class and the message
go to the log line, the status row says a plain sentence, the looks back off as after a « keep » (300 s, then
600 s), and the third failure in a row puts the setting aside as a run that failed does (the same ban, the same
sentences: ``BackgroundSwipe._not_run``), with its own cause: « its looks keep failing », not « the
engine stopped during a run ».  CPU, with the fakes of ``test_swipe_auto_live``."""
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


LOOKS_SAID = ("the automatic swipe could not run l @ 1280 (its looks keep failing): x @ 1280 still runs "
              "- set Auto to off")
LOOKS_ROW = ("the automatic swipe could not run l @ 1280 (its looks keep failing) and will not try it "
             "again until you press Dismiss: x @ 1280 still runs - set Auto to off")
LOOKS_WAITS = ("the automatic swipe waits: it could not run l @ 1280 (its looks keep failing) and will "
               "not try it again until you press Dismiss - set Auto to off")


def test_the_third_failed_look_in_a_row_says_its_own_cause_not_the_engines(monkeypatch, capsys):
    """The setting put aside after three looks that raised: the alert and the red line, the readiness row and the
    look's row say the looks' own failure -- « (its looks keep failing) » -- with the one thing to do,
    Auto to off; the error itself stays for the log line and /status's detail.  Fails before: « (the engine stopped
    during a run) », while the engine ran and the looker's own code raised."""
    bg, proc, loader, live = _evening_bg()
    _raising(monkeypatch)
    tap = _Tap(range(3000))
    assert [t for t in range(1, 1400) if _look(bg, proc, tap, t) == "run"] == [150, 450, 1050]
    assert bg.switch.fault == LOOKS_SAID, bg.switch.fault
    assert bg.switch.health() == LOOKS_ROW, bg.switch.health()
    assert bg.status()["last_look"]["outcome"] == LOOKS_WAITS and bg.health() == LOOKS_WAITS
    error = "AttributeError: '_BgProc' object has no attribute 'swipe_lost_share'"
    assert error in bg.switch.fault.detail and error in capsys.readouterr().out


def test_looks_that_raise_under_a_suggestion_stop_its_invitation_and_withdraw_it(monkeypatch):
    """b3r/probe_raise_under_sug.py: shown at 300 s, confirmed at 600 s (the looks back off 600 s), then every look
    raises.  The first that raises (1200 s) does not confirm it, as any other look: « Apply now? » and the Apply
    button leave the line, the reason under it says the look failed (not the confirming look's verdict), and the
    next look comes on a full fresh pool (150 s later), not after the keep's wait; that second one withdraws it.
    Each still counts toward the three in a row: the third, after the keep's wait, puts the setting aside.  Fails
    before: the raising looks sent nothing, the line kept « seen 3 times: Apply now? » and its button, and the
    second look came 1,200 s after the first (the reviewer's « Apply now » from 700 s to 2,405 s)."""
    import runtime.swipe_background as sb
    from test_swipe_auto_suggestion import WITHDRAWN, _events
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(6000))
    assert [t for t in range(1, 601) if _look(bg, proc, tap, t) == "run"] == [150, 300, 600]
    assert bg.suggestion is not None and bg.suggestion["text"].endswith(", seen 3 times: Apply now?")
    _raising(monkeypatch)
    n = len(events)
    first = next(t for t in range(601, 3000) if _look(bg, proc, tap, t) == "run")
    assert first == 1200 and bg.status()["last_look"]["outcome"] == LOOK_FAILED
    lines = [p for k, p in events[n:] if k == "suggest" and p.get("again")]
    assert lines, "the look that raised left « Apply now? » on the line (no line sent)"
    assert lines[-1].get("winner") is None, "the look that raised left the Apply button on the line"
    text = lines[-1]["text"]
    assert " - not confirmed at the last look (" in text and "Apply now" not in text, text
    assert lines[-1]["result"]["reason"] == sb.BackgroundSwipe.LOOK_RAISED
    assert bg.suggestion is not None and bg.suggestion["unconfirmed"] == 1
    second = next(t for t in range(first + 1, 4000) if _look(bg, proc, tap, t) == "run")
    assert second - first == 150, f"the second look came {second - first} s after the first"
    assert bg.suggestion is None and [p["text"] for k, p in events if k == "cleared"] == [WITHDRAWN]
    assert bg.status()["auto_banned"] == [] and not bg.switch.fault, "put aside after two failures"
    third = next(t for t in range(second + 1, 6000) if _look(bg, proc, tap, t) == "run")
    assert third - second >= 300, f"the third look came {third - second} s after the second (no keep's wait)"
    assert bg.status()["auto_banned"] == ["yolo11l-pose_1280.engine"]
    assert bg.switch.fault == LOOKS_SAID, bg.switch.fault
