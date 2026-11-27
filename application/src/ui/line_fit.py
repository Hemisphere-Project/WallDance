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

H2c (27/11, the show laptop): a line that NAMES its cause -- « name: explanation - instruction » (« the empty-wall
picture is not this wall: nothing in it lines up with the camera - the wall is empty now: STANDBY, then ... ») or
« name - instruction » -- read « the...: nothing in it lines up ... »: the name was cut, its explanation kept.  Such
a line now gives way in this order: the tail's names; the explanation's brackets, then the explanation; a lead-in
of the instruction that only states (« the wall is empty now: »); the name's brackets -- each whole, never a word
cut -- and then it wraps: the name and the instruction are never cut.  Separators inside brackets do not count.  A
part that states opens with a determiner, a pronoun or a number (``_STATES``); one that says when (« when the wall
is empty: », « at the next break: ») or what to do (the enhancement's « turn it on - Ctrl+Shift+E ... ») is the
instruction's own: such a line (« fact: instruction ») is fitted as before.
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
    # H2c: a line that names its cause (``named``): ``fact`` is the NAME, never cut; ``rest`` is
    # [expl « - »] [lead « : »] ins
    expl: str = ""      # « nothing in it lines up with the camera » -- gives way whole
    lead: str = ""      # « the wall is empty now » (it only states) -- gives way whole, after ``expl``
    ins: str = ""       # « STANDBY, then CALIBRATE (2 Calibrate) on it, then Save to project » -- kept
    named: bool = False

    @property
    def whole(self) -> str:
        return self.head + self.fact + self.sep + self.rest + self.tail


# H2c: the first word of a part that STATES (an explanation, « the wall is empty now: »), not one that says when
# or what to do.
_STATES = frozenset("the a an no nothing none only it its this that these those there some all every each both "
                    "neither either one".split())


def _top_seps(s: str, seps: Sequence[str] = _SEPS) -> List[tuple]:
    """[(index, separator)] of ``seps`` in ``s`` outside brackets, in order."""
    out, depth = [], 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            sep = next((x for x in seps if s.startswith(x, i)), None)
            if sep and i > 0:
                out.append((i, sep))
    return out


def _states(part: str) -> bool:
    words = part.strip().split(" ", 1)
    first = words[0].lower() if words and words[0] else ""
    return first in _STATES or first[:1].isdigit()


def _split_body(head: str, body: str, tail: str = "", more: int = 0) -> LineParts:
    seps = _top_seps(body)
    if not seps:
        return LineParts(head, body, "", "", tail, more)
    i, sep = seps[0]
    name, rest = body[:i], body[i + len(sep):]
    expl, ins = "", rest
    if sep == ": ":
        dash = _top_seps(rest, (" - ",))
        if not dash or not _states(rest[:dash[0][0]]):
            return LineParts(head, name, sep, rest, tail, more)     # « fact: instruction »: as before
        expl, ins = rest[:dash[0][0]], rest[dash[0][0] + 3:]
    lead = ""
    first = _top_seps(ins)
    if first and first[0][1] == ": " and _states(ins[:first[0][0]]):
        lead, ins = ins[:first[0][0]], ins[first[0][0] + 2:]
    return LineParts(head, name, sep, rest, tail, more, expl=expl, lead=lead, ins=ins, named=True)


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
    if p.named:
        return _named_candidates(p, tail)
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
    return _unique(out)


def _unique(out: List[str]) -> List[str]:
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _named_candidates(p: LineParts, tail: str) -> List[str]:
    """H2c: a line that names its cause -- the tail's names, the explanation's brackets, the explanation, a lead-in
    that only states, the name's brackets give way, each whole; never a word of the name or of the instruction."""
    lead = p.lead + ": " if p.lead else ""
    out = [p.whole, p.head + p.fact + p.sep + p.rest + tail]
    if p.expl:
        expl = _BRACKET.sub(f"({ELLIPSIS})", p.expl)
        out.append(p.head + p.fact + p.sep + expl + " - " + lead + p.ins + tail)
        out.append(p.head + p.fact + " - " + lead + p.ins + tail)
    if p.lead:
        out.append(p.head + p.fact + " - " + p.ins + tail)
    name = _BRACKET.sub(f"({ELLIPSIS})", p.fact)
    out.append(p.head + name + " - " + p.ins + tail)
    return _unique(out)


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
