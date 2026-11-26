"""H2 (#t-040, follow-up): the health line and the Alerts line fitted to the window -- the instruction is never cut.

Both lines are « fact - instruction » for a technician alone at a gig.  When a line is wider than the window, what
gives way, in this order:

1. the tail « (+N: camera stalls, light, points) » (the other sources): its names, to « (+N: ...) » (the count
   stays: there is more to fix after this);
2. the fact's detail: each bracket of the fact (dates, numbers) becomes « (...) »;
3. the fact itself, cut word by word from its end, marked « ... ».

Never the head (« FAIL - enhancement: ») nor anything after the fact's FIRST separator (« - » or « : »): every
source writes « fact SEP instruction » with SEP the first one or a later one, so that part always holds the whole
instruction (« turn it on - Ctrl+Shift+E, ... » included).  The fewest lines win, then the fullest text: one line
when the instruction fits on one, else the line wraps at words onto a second (the health band holds two), a third
only if the window is narrower still.  Only what is DRAWN is fitted: /status, the logs and the watch get the line
whole.

Pure: the GUI hands ``measure`` (its own text metrics, gui.WallDanceGUI._text_measure) -- never a character count.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

ELLIPSIS = "..."
TAIL_GAP = "   "
_HEAD = re.compile(r"^(?:FAIL|WARN|NOT CHECKED|OK) - (?:[^:]{1,40}: )?")
_TAIL = re.compile(r"   \(\+(\d+): [^()]*\)$")
_SEPS = (" - ", ": ")
_BRACKET = re.compile(r"\([^()]*\)")

Measure = Callable[[str], float]


@dataclass
class LineParts:
    """A line cut into what may give way and what may not."""
    head: str           # « FAIL - enhancement: » -- kept
    fact: str           # « OFF, but Calibrate chose ... (2026-10-14 10:01) » -- gives way second
    sep: str            # the fact's first separator, « : » or « - » -- kept
    rest: str           # everything after it, the instruction included -- kept
    tail: str = ""      # « (+3: camera stalls, light, points) » or the other alerts -- gives way first
    more: int = 0       # how many sources / alerts the tail stands for

    @property
    def whole(self) -> str:
        return self.head + self.fact + self.sep + self.rest + self.tail


def _split_body(head: str, body: str, tail: str = "", more: int = 0) -> LineParts:
    cut = [(body.find(s), s) for s in _SEPS if body.find(s) > 0]
    if not cut:
        return LineParts(head, body, "", "", tail, more)
    i, sep = min(cut)
    return LineParts(head, body[:i], sep, body[i + len(sep):], tail, more)


def split_health_line(text: str) -> LineParts:
    """The health line (core/ops_monitor.health_line: « WORD - source: detail   (+N: names) ») in its parts."""
    m = _HEAD.match(text)
    head = m.group(0) if m else ""
    body, tail, more = text[len(head):], "", 0
    t = _TAIL.search(body)
    if t:
        body, tail, more = body[:t.start()], body[t.start():], int(t.group(1))
    return _split_body(head, body, tail, more)


def split_alerts(messages: Sequence[str]) -> LineParts:
    """The Alerts line: the first alert is the line (its fact may give way, never its instruction); the others
    are its tail (shown whole when there is room, else « (+N: ...) », else not)."""
    msgs = [m for m in messages if m]
    if not msgs:
        return LineParts("", "", "", "")
    tail = (TAIL_GAP + TAIL_GAP.join(msgs[1:])) if len(msgs) > 1 else ""
    return _split_body("", msgs[0], tail, len(msgs) - 1)


def candidates(p: LineParts) -> List[str]:
    """Every text the line may show, in the order things give way (the first is the whole line)."""
    tail = f"{TAIL_GAP}(+{p.more}: {ELLIPSIS})" if p.tail else ""     # the tail's names go; its count stays
    keep = p.sep + p.rest + tail
    out = [p.whole, p.head + p.fact + keep]
    fact = _BRACKET.sub(f"({ELLIPSIS})", p.fact)
    if fact != p.fact:
        out.append(p.head + fact + keep)
    words = fact.split(" ")
    for i in range(len(words) - 1, 0, -1):
        cut = " ".join(words[:i]).rstrip(" ,;:-/(")
        if cut:
            out.append(p.head + cut + ELLIPSIS + keep)
    if p.fact:
        out.append(p.head + ELLIPSIS + keep)
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def wrap(text: str, width: float, measure: Measure) -> List[str]:
    """``text`` broken at spaces into lines no wider than ``width`` (a word wider than a line stands alone)."""
    lines: List[str] = []
    cur, gap = "", ""
    for tok in re.findall(r"\S+|\s+", text):
        if tok.isspace():
            gap = tok
            continue
        trial = cur + gap + tok if cur else tok
        if cur and measure(trial) > width:
            lines.append(cur)
            cur = tok
        else:
            cur = trial
        gap = ""
    if cur or not lines:
        lines.append(cur)
    return lines


def fit(p: LineParts, width: float, measure: Measure) -> List[str]:
    """The lines to draw: the fewest lines, then the fullest candidate.  ``width``: the px the text may take."""
    best: Optional[List[str]] = None
    for c in candidates(p):
        lines = [c] if measure(c) <= width else wrap(c, width, measure)
        if best is None or len(lines) < len(best):
            best = lines
        if len(best) == 1:
            break
    return best or [p.whole]


def fit_text(parts: LineParts, width: Optional[float], measure: Optional[Measure]) -> str:
    """``fit`` joined with newlines; the whole line when the width or the metrics are not known yet."""
    if not width or width <= 0 or measure is None:
        return parts.whole
    return "\n".join(fit(parts, width, measure))
