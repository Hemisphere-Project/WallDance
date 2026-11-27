"""The support line (S1b, hub walldance#t-061): a short message on the operator's screen until he acknowledges it.

The one channel from the remote supervisor to the technician during the rehearsal of 7-8/12: Nico is alone at the
laptop, Thomas on the phone part-time, a session on mgr-ran watches (``extra/wdwatch.py``) and, once Thomas has
said yes, sends a message with ``extra/wdremote.py support-send``.

**What governs it: the project key ``support_line``** (``core.config.SUPPORT_LINE``, off by default; on in the
rehearsal project only). The operator's « Allow remote control during RUN » tick does not: a message changes
nothing in the show, and turning the key on in the project is the consent. So it works in RUN with the tick off,
and is refused everywhere the key is off (``services/remote_api.py``, policy class ``support``).

The flow, one thread each step:

- HTTP thread (``RemoteApi``): ``SupportLine.prepare`` refuses (key off, no window yet, too long, markup, too many
  waiting) or cleans the text and gives it an id; ``api.SupportMessage`` is queued on the RuntimeAPI.
- main loop (``RuntimeAPI.drain``): ``SupportLine.show`` puts it on screen (``SupportMessageShown``) or behind the
  one there (``SupportMessageQueued``). Nothing waits: no modal, no pump, the frame loop runs on.
- GUI: the band over the preview (``gui_builder.build_support_band``); its button (``SUPPORT_ACK_LABEL``) submits
  ``api.AckSupportMessage``.
- main loop: ``SupportLine.ack`` -> ``SupportMessageAcked`` (the time it took), then the next waiting message.

**A second message while one shows is queued**, first in first out, never replacing it: each message is read and
acknowledged on its own, so the supervisor knows which ones were read. The band says how many wait; at most
``SUPPORT_MESSAGE_MAX_WAITING`` wait, the next is refused (429).

**Text**: plain text, at most ``SUPPORT_MESSAGE_MAX_CHARS`` characters after cleaning (longer is refused, never cut);
markup (``<b>``, ``&eacute;``) is refused; line breaks and tabs become spaces, other control and format characters
are removed. The app's font is DearPyGui's default, ProggyClean, which draws all of Latin-1 (every French accent,
« », ç) and nothing else of French typography: ’ ‘ “ ” … – — œ Œ € and the thin spaces are transliterated visibly
(' " ... - oe OE EUR, a space) and said back to the sender; any other character becomes « ? » (never a box).

Every step is a line in the app log (``[Support] ...``) and an event in the remote API's ring (``/api/v1/events``);
``/api/v1/status`` carries ``support_line`` (on/off, the message on screen, the waiting ones, the last read ones).
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from core.config import SUPPORT_LINE, SUPPORT_MESSAGE_MAX_CHARS, SUPPORT_MESSAGE_MAX_WAITING
from runtime import api

# What the app's font draws (DearPyGui 2.3's default font, ProggyClean: printable ASCII and Latin-1; checked
# against its character map by tests/test_support_line.py). Everything else is folded below or becomes "?".
_FONT_RANGES = ((0x20, 0x7E), (0xA1, 0xFF))

# French typography the font lacks -> what the band shows instead (visible, close in meaning).
_FOLD = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'", "\u02bc": "'",   # ‘ ’ ‚ ‛ ′ ʼ
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',                # “ ” „ ‟ ″
    "\u2039": "\u00ab", "\u203a": "\u00bb",            # ‹ › -> the Latin-1 guillemets « »
    "\u2026": "...",                                   # …
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-",  # hyphens, – —
    "\u2212": "-", "\u2022": "-",                      # minus sign, bullet
    "\u0153": "oe", "\u0152": "OE",                    # œ Œ
    "\u20ac": "EUR",                                   # €
}
_LINE_BREAKS = "\n\r\t\v\f\x85\u2028\u2029"
_MARKUP = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(\s[^<>]*)?/?>|&(#\d{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z]{2,8});")


def _drawn(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _FONT_RANGES)


def clean_message(text: Any, max_chars: int = SUPPORT_MESSAGE_MAX_CHARS) -> Tuple[str, List[str]]:
    """(the text the band shows, notes on what changed); ValueError with the reason when it is refused.

    Plain text only: markup is refused, line breaks and tabs become spaces, control and format characters are
    removed, characters the font cannot draw are transliterated (``_FOLD``, else their unaccented letter, else
    « ? »), runs of spaces collapse. Idempotent on its own output."""
    if text is None:
        raise ValueError("the message is empty")
    raw = unicodedata.normalize("NFC", str(text))
    out: List[str] = []
    breaks = removed = unknown = 0
    folded: Dict[str, str] = {}
    for ch in raw:
        if ch in _LINE_BREAKS:
            breaks += 1
            out.append(" ")
            continue
        cat = unicodedata.category(ch)
        if ch == "\u00a0" or cat == "Zs":
            out.append(" ")                                     # no-break and thin spaces: a plain space
            continue
        if cat in ("Cc", "Cf", "Cs", "Co", "Cn"):
            removed += 1                                        # controls, zero-width, bidi overrides
            continue
        if _drawn(ch):
            out.append(ch)
            continue
        if ch in _FOLD:
            rep = _FOLD[ch]
        else:
            base = "".join(c for c in unicodedata.normalize("NFKD", ch)
                           if not unicodedata.combining(c) and _drawn(c))
            rep = base or "?"
            if rep == "?":
                unknown += 1
        folded[ch] = rep
        out.append(rep)
    clean = re.sub(r" {2,}", " ", "".join(out)).strip()
    if not clean:
        raise ValueError("the message is empty")
    m = _MARKUP.search(clean)
    if m:
        raise ValueError(f"plain text only, no markup: {m.group(0)!r}")
    if len(clean) > max_chars:
        raise ValueError(f"too long: {len(clean)} characters, the limit is {max_chars} (say it in two messages)")
    notes = []
    if folded:
        pairs = ", ".join(f"{k!r} -> {v!r}" for k, v in sorted(folded.items()))
        notes.append(f"not in the screen font, shown as: {pairs}")
    if unknown:
        notes.append(f"{unknown} character(s) the font cannot draw shown as '?'")
    if breaks:
        notes.append(f"{breaks} line break(s) or tab(s) shown as a space (the band is one paragraph)")
    if removed:
        notes.append(f"{removed} control or format character(s) removed")
    return clean, notes


def _coerce_on(value: Any) -> bool:
    """The project key as config_schema validates it: a real True, 1, or a true-ish word; anything else is off."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return isinstance(value, str) and value.strip().lower() in ("true", "1", "yes", "on")


def _stamp(t: Any, after: Optional[float] = None) -> float:
    """One rounding for the three stamps of a message (sent_at, shown_at, acked_at): the millisecond, read on the
    line's one clock. A stamp that would read earlier than the one before it (``after``, already a stamp: a
    wall clock stepped back, a click time behind) is set equal to it, so sent_at <= shown_at <= acked_at holds
    by construction and the durations read from them (``seconds``, ``on_screen_s``) are never negative.
    (hub walldance#t-061: a show inside the send's millisecond read earlier than it, sent_at rounded, shown_at not.)"""
    s = round(float(t), 3)
    return after if after is not None and s < after else s


class SupportLine:
    """The runtime side of the support line: the project key, the message on screen, the queue, the history.

    ``prepare`` runs on the remote API's HTTP thread; ``show`` / ``ack`` are RuntimeAPI handlers (main loop).
    One lock; events are published outside it."""

    def __init__(self, bus: api.EventBus, enabled: bool = SUPPORT_LINE, *,
                 max_chars: int = SUPPORT_MESSAGE_MAX_CHARS, max_waiting: int = SUPPORT_MESSAGE_MAX_WAITING,
                 clock: Callable[[], float] = time.time):
        self.bus = bus
        self.enabled = bool(enabled)
        self.max_chars = int(max_chars)
        self.max_waiting = int(max_waiting)
        self._clock = clock
        self._lock = threading.Lock()
        self._n = 0
        self._pending = 0                       # accepted by prepare, not drained yet
        self._showing: Optional[Dict[str, Any]] = None
        self._waiting: Deque[Dict[str, Any]] = deque()
        self._read: Deque[Dict[str, Any]] = deque(maxlen=20)

    # -- the project key ---------------------------------------------------------------------------------
    def configure(self, config: Dict[str, Any]) -> None:
        """A full project load (or a config carrying the key): ``support_line``, off when absent."""
        was, self.enabled = self.enabled, _coerce_on(config.get("support_line", SUPPORT_LINE))
        if self.enabled != was:
            print(f"[Support] the support line is {'ON' if self.enabled else 'off'} (project key support_line)")

    # -- remote API (HTTP thread) ------------------------------------------------------------------------
    def prepare(self, args: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """(200, {text, msg_id, sent_at, notes}) for an ``api.SupportMessage``, or (4xx, {"error": reason}).
        The caller has already checked the key (the policy)."""
        extra = sorted(set(args) - {"text"})
        if extra:
            return 400, {"error": f"SupportMessage takes only text, not {ascii(extra)}"}
        if not getattr(self.bus, "ui_ready", False):
            return 409, {"error": "SupportMessage refused: the app's window is not up yet, nobody would see it"}
        try:
            text, notes = clean_message(args.get("text"), self.max_chars)
        except ValueError as e:
            return 400, {"error": f"SupportMessage refused: {e}"}
        now = self._clock()
        with self._lock:
            in_flight = (self._showing is not None) + len(self._waiting) + self._pending
            if in_flight > self.max_waiting:
                return 429, {"error": (f"SupportMessage refused: {in_flight - 1} message(s) already wait behind "
                                       f"the one on screen (limit {self.max_waiting}); wait for an "
                                       "acknowledgement")}
            self._n += 1
            self._pending += 1
            msg_id = f"{time.strftime('%H%M%S', time.localtime(now))}-{self._n}"
        return 200, {"text": text, "msg_id": msg_id, "sent_at": _stamp(now), "notes": notes}

    # -- RuntimeAPI handlers (main loop) -----------------------------------------------------------------
    def show(self, c: api.SupportMessage) -> None:
        """Put the message on screen, or behind the one there. Never blocks."""
        now = self._clock()
        with self._lock:
            self._pending = max(0, self._pending - 1)
        if not self.enabled:
            print(f"[Support] message {c.msg_id or '?'} dropped: the support line is off in this project")
            return
        try:
            text, _ = clean_message(c.text, self.max_chars)     # a command that did not come through prepare
        except ValueError as e:
            print(f"[Support] message {c.msg_id or '?'} dropped: {e}")
            return
        with self._lock:
            if not c.msg_id:
                self._n += 1
            entry = {"id": c.msg_id or f"{time.strftime('%H%M%S', time.localtime(now))}-{self._n}",
                     "text": text, "sent_at": _stamp(c.sent_at or now)}
            if self._showing is None:
                entry["shown_at"] = _stamp(now, entry["sent_at"])
                self._showing = entry
                event: api.Event = api.SupportMessageShown(entry["id"], text, entry["sent_at"], entry["shown_at"],
                                                           len(self._waiting))
            else:
                self._waiting.append(entry)
                event = api.SupportMessageQueued(entry["id"], text, entry["sent_at"], len(self._waiting))
        if isinstance(event, api.SupportMessageShown):
            print(f"[Support] message {entry['id']} on screen: {text!r}")
        else:
            print(f"[Support] message {entry['id']} waits behind {self._showing_id()} "
                  f"({event.waiting} waiting): {text!r}")
        self.bus.publish(event)

    def ack(self, c: api.AckSupportMessage) -> None:
        """The operator's click: the message is read; the next waiting one comes on screen."""
        now = self._clock()
        with self._lock:
            cur = self._showing
            if cur is None or cur["id"] != c.msg_id:
                stale = c.msg_id
                cur = None
            else:
                # The click's own time (the GUI's, rounded like every stamp) when it falls between the message's
                # coming on screen and a second after this drain; else this drain's time, on the line's clock.
                # A click time earlier than shown_at is not believed (the band was not up yet: a clock behind
                # the line's, a stepped wall clock); the drain's time is when the runtime took the click.
                click = _stamp(c.clicked_at) if c.clicked_at else None
                believed = click is not None and cur["shown_at"] <= click <= now + 1.0
                acked_at = click if believed else _stamp(now, cur["shown_at"])
                early = click is not None and click < cur["shown_at"]
                rec = dict(cur, acked_at=acked_at,
                           seconds=round(max(0.0, acked_at - cur["sent_at"]), 1),
                           on_screen_s=round(max(0.0, acked_at - cur["shown_at"]), 1))
                self._read.append(rec)
                nxt = self._waiting.popleft() if self._waiting else None
                if nxt is not None:
                    nxt["shown_at"] = _stamp(now, nxt["sent_at"])
                self._showing = nxt
                left = len(self._waiting)
        if cur is None:
            print(f"[Support] acknowledgement of {stale} ignored: it is not the message on screen")
            return
        if early:
            print(f"[Support] message {rec['id']}: click time {click:.3f} is before the message came on screen: "
                  f"the drain's time counts ({rec['acked_at']:.3f})")
        print(f"[Support] message {rec['id']} acknowledged after {rec['seconds']:.1f} s "
              f"({rec['on_screen_s']:.1f} s on screen)")
        self.bus.publish(api.SupportMessageAcked(rec["id"], rec["text"], rec["sent_at"], rec["shown_at"],
                                                 rec["acked_at"], rec["seconds"], rec["on_screen_s"],
                                                 left + (1 if nxt is not None else 0)))
        if nxt is not None:
            print(f"[Support] message {nxt['id']} on screen: {nxt['text']!r}")
            self.bus.publish(api.SupportMessageShown(nxt["id"], nxt["text"], nxt["sent_at"], nxt["shown_at"], left))

    # -- observation -------------------------------------------------------------------------------------
    def _showing_id(self) -> Optional[str]:
        cur = self._showing
        return cur["id"] if cur else None

    def status(self) -> Dict[str, Any]:
        """For /api/v1/status: small (no texts but the one on screen), safe from any thread."""
        with self._lock:
            cur = self._showing
            return {
                "enabled": self.enabled,
                "showing": ({"id": cur["id"], "text": cur["text"], "sent_at": cur["sent_at"],
                             "shown_at": cur["shown_at"]} if cur else None),
                "waiting": [e["id"] for e in self._waiting],
                "read": [{k: r[k] for k in ("id", "sent_at", "acked_at", "seconds", "on_screen_s")}
                         for r in list(self._read)[-10:]],
            }
