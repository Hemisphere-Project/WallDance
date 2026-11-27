"""F4h (#t-056 follow-up, laptop 27/11): what a human or the remote watcher is told about the points OSC sends.

On the laptop on 27/11 a slot HELD on a foreground group (F4: state ``fg``) was sent on OSC for a minute while the
status said 0 dancers and the alert « No dancers tracked »: those read the tracker's reported tracks, not the slot
outputs OSC carries (``FrameProcessor.last_emitted``, the « sent » of the tracking log).  Every count and state
word here is built from those outputs, so the Live panel, /status, the health line, the alerts and the watch tool
agree with what the visuals receive.  Pure functions: no tracker behaviour is read or changed here.
"""
from typing import Any, Dict, Iterable, List, Optional

# The slot states (core/identity_slots.py STATE_*) in the operator's words.  A point no detection backs this frame
# is « held » (and on what) or « coasting » (on its prediction alone).
SLOT_WORDS = {
    "live": "live",
    "fg": "held on the foreground",
    "belt": "held on the belt",
    "weak": "held on a weak detection",
    "coasting": "coasting",
    "lost": "lost",
}
HELD_STATES = ("fg", "belt", "weak", "coasting")


def slot_word(state: Optional[str]) -> str:
    return SLOT_WORDS.get(str(state), str(state))


def points_summary(tracks: Optional[Iterable[Any]]) -> Dict[str, Any]:
    """The points OSC sends this frame (the slot outputs: ``FrameProcessor.last_emitted``): how many, how many
    held (no detection under them this frame), and each one's slot state and time in it.  A raw tracker track
    (stable ids off) carries no slot state: it is a live point."""
    slots: List[Dict[str, Any]] = []
    try:
        items = list(tracks or ())
    except TypeError:          # not a list of tracks (a stand-in processor): no point to report
        items = []
    for t in items:
        st = getattr(t, "slot_state", None)
        age = getattr(t, "slot_age_s", None)
        slots.append({"id": int(getattr(t, "track_id", 0)), "state": st or "live", "slot": st is not None,
                      "age_s": None if age is None else round(float(age), 1)})
    held = sum(1 for s in slots if s["state"] in HELD_STATES)
    return {"sent": len(slots), "held": held, "slots": slots}


def _label(s: Dict[str, Any], ages: bool) -> str:
    who = f"D{s['id']}" if s.get("slot", True) else f"id {s['id']}"
    age = s.get("age_s")
    tail = f" {age:.1f}s" if ages and age is not None and s["state"] in HELD_STATES else ""
    return f"{who} {slot_word(s['state'])}{tail}"


def points_text(summary: Optional[Dict[str, Any]], ages: bool = False) -> str:
    """« 1 point sent: D1 held on the foreground » / « no point sent »."""
    n = int((summary or {}).get("sent") or 0)
    if n <= 0:
        return "no point sent"
    parts = ", ".join(_label(s, ages) for s in (summary or {}).get("slots") or ())
    return f"{n} point{'' if n == 1 else 's'} sent: {parts}"


NOTHING_SENT = "(nothing sent)"


def slots_text(states, belt: str, max_dancers: int, sending: bool = True) -> str:
    """The Live panel's « Dancer IDs » readout (and /status ``slots_text``): each slot's state in words, with the
    time in it for a held or coasting one, and the belt's status.  When nothing goes out on OSC (STANDBY, OSC
    off: ``sending`` False) a slot still in a state is shown without a running time and the line says
    « (nothing sent) »: the table is frozen or not sent, not a point the visuals receive."""
    if not states:
        return f"ids: waiting for dancers (max {max_dancers})"
    parts = [f"D{sid} {slot_word(st)}" + (f" {age:.1f}s" if sending and st in HELD_STATES else "")
             for sid, st, age in states]
    tail = "" if sending or all(st == "lost" for _sid, st, _age in states) else f"   {NOTHING_SENT}"
    return "ids: " + " | ".join(parts) + f"   belt: {belt}" + tail


def dancers_text(num: int, points: Optional[Dict[str, Any]]) -> str:
    """The bottom bar's « Dancers: » figure: the points sent, « 1 (1 held) » when some are held."""
    held = int((points or {}).get("held") or 0)
    return f"{num} ({held} held)" if held else str(num)


def sent_tracks(proc) -> Optional[List[Any]]:
    """What the processor's last processed frame put out on OSC (``FrameProcessor.last_sent``, set by
    pipeline._emit: the slot stream at L = 1, the lagged stream at L > 1), or None when nothing went out: OSC off,
    the lag's warm-up, a sender that could not open, a processor without the record.  Whether a frame was
    processed at all (STANDBY) is the main loop's to say (MainLoop._points_sent)."""
    sent = getattr(proc, "last_sent", None)
    if sent is None or not getattr(getattr(proc, "osc", None), "enabled", True):
        return None
    try:
        return list(sent)
    except TypeError:          # not a list of tracks (a stand-in processor): nothing known to be sent
        return None


def stats_fields(sent: Optional[Iterable[Any]], tracked) -> Dict[str, Any]:
    """StatsTick's point fields (runtime/main_loop.py), from ``sent`` (sent_tracks, None = nothing went out):
    ``points`` (points_summary) and ``num_dancers`` = the points sent; while nothing is sent (STANDBY, OSC off)
    the tracker's reported tracks ``tracked``, as before F4h."""
    pts = points_summary(sent)
    return {"num_dancers": pts["sent"] if sent is not None else len(tracked or ()), "points": pts}
