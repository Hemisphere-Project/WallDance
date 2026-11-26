"""S1b (hub walldance#t-061): the support line -- a message on the laptop's screen until acknowledged.

The real WallDanceGUI built by the real DpgUiAdapter in a viewport-less DearPyGui context (the A4 pattern), the
real EventBus / RuntimeAPI, the real SupportLine registered as the app registers it, the real remote API over
loopback HTTP, and extra/wdremote.py's client commands against it (LocalTransport: no ssh, no laptop).

Clauses: shown until acknowledged; not blocking (no modal, no keyboard grab, no pump or wait in the frame loop,
nothing over the readiness and health rows); the second-message rule (queued); accents in the app's font
(checked against the font's own character map, read out of DearPyGui); the API policy (refused with the key off;
accepted in RUN with the key on and the remote-control tick off); the acknowledgement event and its time; the
project key off by default, saved and loaded through the app's own save and load code.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")

from runtime import api  # noqa: E402
from services.support_line import SupportLine, clean_message, _drawn  # noqa: E402

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
FRENCH = "Relance Calibrate, s’il te plaît… « Mur » côté œil – à 20 h, ça ira ? Élan, À, Ô, ù, ë, ï, ÿ, ç"
SHOWN = "Relance Calibrate, s'il te plaît... « Mur » côté oeil - à 20 h, ça ira ? Élan, À, Ô, ù, ë, ï, ÿ, ç"


# ----------------------------------------------------------------------------------------------
# the app's font: DearPyGui's default (imgui's ProggyClean), read out of the binary
# ----------------------------------------------------------------------------------------------

def _stb_decompress(src: bytes) -> bytes:
    """imgui's stb_decompress (the default font is embedded stb-compressed)."""
    def in2(x): return (src[x] << 8) | src[x + 1]
    def in3(x): return (src[x] << 16) | in2(x + 1)
    def in4(x): return (src[x] << 24) | in3(x + 1)
    assert in4(0) == 0x57BC0000 and in4(4) == 0
    olen, out, i = in4(8), bytearray(), 16

    def match(dist, n):
        start = len(out) - dist
        assert start >= 0
        for k in range(n):
            out.append(out[start + k])
    while True:
        b, old = src[i], i
        if b >= 0x80:
            match(src[i + 1] + 1, b - 0x80 + 1); i += 2
        elif b >= 0x40:
            match(in2(i) - 0x4000 + 1, src[i + 2] + 1); i += 3
        elif b >= 0x20:
            n = b - 0x20 + 1; out += src[i + 1:i + 1 + n]; i += 1 + n
        elif b >= 0x18:
            match(in3(i) - 0x180000 + 1, src[i + 3] + 1); i += 4
        elif b >= 0x10:
            match(in3(i) - 0x100000 + 1, in2(i + 3) + 1); i += 5
        elif b >= 0x08:
            n = in2(i) - 0x0800 + 1; out += src[i + 2:i + 2 + n]; i += 2 + n
        elif b == 0x07:
            n = in2(i + 1) + 1; out += src[i + 3:i + 3 + n]; i += 3 + n
        elif b == 0x06:
            match(in3(i + 1) + 1, src[i + 4] + 1); i += 5
        elif b == 0x04:
            match(in3(i + 1) + 1, in2(i + 4) + 1); i += 6
        if i == old:
            assert b == 0x05 and src[i + 1] == 0xFA, f"bad stb token {b:#x}"
            assert len(out) == olen
            return bytes(out)


class _Font:
    """The few TrueType tables needed: the character map (format 4) and each glyph's outline size."""

    def __init__(self, ttf: bytes):
        self.ttf = ttf
        n = struct.unpack_from(">H", ttf, 4)[0]
        self.tables = {ttf[12 + 16 * k:16 + 16 * k].decode("latin-1"):
                       struct.unpack_from(">II", ttf, 20 + 16 * k) for k in range(n)}
        self.name = ttf[self.tables["name"][0]:sum(self.tables["name"])]
        self.cmap = self._cmap()
        head = self.tables["head"][0]
        long_loca = struct.unpack_from(">h", ttf, head + 50)[0] == 1
        glyphs = struct.unpack_from(">H", ttf, self.tables["maxp"][0] + 4)[0]
        loca = self.tables["loca"][0]
        self.loca = ([struct.unpack_from(">I", ttf, loca + 4 * g)[0] for g in range(glyphs + 1)] if long_loca
                     else [2 * struct.unpack_from(">H", ttf, loca + 2 * g)[0] for g in range(glyphs + 1)])

    def _cmap(self):
        ttf, base = self.ttf, self.tables["cmap"][0]
        records = [struct.unpack_from(">HHI", ttf, base + 4 + 8 * k)
                   for k in range(struct.unpack_from(">H", ttf, base + 2)[0])]
        off = next(base + o for p, e, o in records if (p, e) == (3, 1)
                   and struct.unpack_from(">H", ttf, base + o)[0] == 4)
        seg = struct.unpack_from(">H", ttf, off + 6)[0] // 2
        ends = struct.unpack_from(f">{seg}H", ttf, off + 14)
        starts = struct.unpack_from(f">{seg}H", ttf, off + 16 + 2 * seg)
        deltas = struct.unpack_from(f">{seg}h", ttf, off + 16 + 4 * seg)
        ro_pos = off + 16 + 6 * seg
        ranges = struct.unpack_from(f">{seg}H", ttf, ro_pos)
        out = {}
        for k in range(seg):
            for c in range(starts[k], ends[k] + 1):
                if c == 0xFFFF:
                    continue
                if ranges[k] == 0:
                    g = (c + deltas[k]) & 0xFFFF
                else:
                    g = struct.unpack_from(">H", ttf, ro_pos + 2 * k + ranges[k] + 2 * (c - starts[k]))[0]
                    g = (g + deltas[k]) & 0xFFFF if g else 0
                if g:
                    out[c] = g
        return out

    def outline(self, ch: str):
        """(the glyph's box (yMin, yMax), its bytes) for ``ch``."""
        g = self.cmap[ord(ch)]
        a, b = self.loca[g], self.loca[g + 1]
        data = self.ttf[self.tables["glyf"][0] + a:self.tables["glyf"][0] + b]
        box = struct.unpack_from(">hhhhh", data, 0) if data else (0, 0, 0, 0, 0)
        return (box[2], box[4]), data


@pytest.fixture(scope="module")
def font():
    """DearPyGui's default font, the one the app draws its text with (no font is bound globally)."""
    import dearpygui
    here = pathlib.Path(dearpygui.__file__).parent
    binary = next(p for p in here.iterdir() if p.name.startswith("_dearpygui") and p.suffix in (".so", ".pyd"))
    blob = binary.read_bytes()
    at = blob.find(b"\x57\xbc\x00\x00\x00\x00\x00\x00")
    while at != -1:
        try:
            f = _Font(_stb_decompress(blob[at:]))
        except (AssertionError, IndexError, StopIteration, struct.error, KeyError):
            f = None
        if f is not None and b"ProggyClean" in f.name:
            return f
        at = blob.find(b"\x57\xbc\x00\x00\x00\x00\x00\x00", at + 1)
    pytest.fail("DearPyGui's default font (ProggyClean) was not found in its binary")


def test_the_app_draws_with_the_default_font_and_binds_no_other():
    """The default font is the app's font: only the icon font is ever bound, item by item."""
    src = (SRC / "gui_builder.py").read_text(encoding="utf-8") + (SRC / "gui.py").read_text(encoding="utf-8")
    assert "dpg.bind_font(" not in src
    assert src.count("dpg.add_font(") == 1 and "fa-solid.otf" in src


def test_the_font_draws_every_french_accent_and_the_line_folds_the_rest(font):
    # the cleaner's idea of « drawn » is the font's own character map
    for cp in range(0x20, 0x100):
        assert _drawn(chr(cp)) == (cp in font.cmap and cp not in range(0x7F, 0xA1)), hex(cp)
    # accented letters have their own glyph (not the bare letter's, not « ? », not another accent's), the
    # accent drawn above the letter (the cedilla below it; on i the accent takes the dot's place)
    unknown = font.outline("?")[1]
    families = {}
    for accented, bare in zip("éèêëàâäçùûüôöîïÿÉÈÊËÀÂÇÙÛÔÎÏ«»", "eeeeaaacuuuooiiyEEEEAACUUOII<>"):
        (box_a, da), (box_b, db) = font.outline(accented), font.outline(bare)
        assert da and da != db and da != unknown, accented
        families.setdefault(bare, []).append(da)
        if accented in "çÇ":
            assert box_a[0] < box_b[0], (accented, box_a, box_b)
        elif accented not in "«»îï":
            assert box_a[1] > box_b[1], (accented, box_a, box_b)
    assert all(len(set(glyphs)) == len(glyphs) for glyphs in families.values())
    # French typography the font lacks is not in its map: the line folds it, visibly
    for ch in "’‘“”…–—œŒ\u202f":
        assert ord(ch) not in font.cmap
    text, notes = clean_message(FRENCH)
    assert text == SHOWN
    assert all(ord(c) in font.cmap for c in text)
    assert any("not in the screen font" in n and "'œ' -> 'oe'" in n for n in notes)


def test_whatever_is_sent_the_band_shows_only_what_the_font_draws(font):
    import random
    rng = random.Random(61)
    pools = [range(0x20, 0x7F), range(0xA0, 0x100), range(0x100, 0x180), range(0x2000, 0x2070),
             range(0x0, 0x20), range(0x80, 0xA0), range(0x4E00, 0x4E40), range(0x1F600, 0x1F610),
             range(0x0300, 0x0310), range(0x202A, 0x202F), range(0x0400, 0x0450)]
    checked = 0
    for _ in range(3000):
        text = "".join(chr(rng.choice(rng.choice(pools))) for _ in range(rng.randint(1, 60)))
        try:
            clean, _ = clean_message(text)
        except ValueError:
            continue
        assert all(ord(c) in font.cmap and 0x20 <= ord(c) for c in clean), repr(clean)
        assert clean_message(clean)[0] == clean               # idempotent
        checked += 1
    assert checked > 2000


def test_plain_text_only_controls_stripped_markup_refused_a_length_limit():
    assert clean_message("\x00a\x07b\u200bc\u202ed\n\te  f\r\n")[0] == "abcd e f"
    text, notes = clean_message("ligne 1\nligne 2")
    assert text == "ligne 1 ligne 2" and any("line break" in n for n in notes)
    assert clean_message("x < 3 et y > 2, R&D; 5 € - fin")[0] == "x < 3 et y > 2, R&D; 5 EUR - fin"
    for markup in ("<b>Attention</b>", "Voir <a href='x'>ici</a>", "caf&eacute;", "&#233;", "<br/>"):
        with pytest.raises(ValueError, match="no markup"):
            clean_message(markup)
    for empty in ("", "   ", "\n\t\x00", None):
        with pytest.raises(ValueError, match="empty"):
            clean_message(empty)
    assert len(clean_message("é" * 280)[0]) == 280
    with pytest.raises(ValueError, match="too long: 281 characters, the limit is 280"):
        clean_message("é" * 281)
    assert clean_message("a" + " " * 400 + "b")[0] == "a b"      # spaces collapse before counting


# ----------------------------------------------------------------------------------------------
# the runtime side: the queue, the acknowledgement and its time, the log
# ----------------------------------------------------------------------------------------------

class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _events(bus):
    seen = []
    bus.subscribe(lambda e: seen.append(e) if type(e).__name__.startswith("Support") else None)
    return seen


def test_a_second_message_waits_its_turn_and_each_is_acknowledged_with_its_time(capsys):
    bus, clock = api.EventBus(), _Clock()
    bus.ui_ready = True
    line, seen = SupportLine(bus, enabled=True, clock=clock), _events(bus)
    ids = []
    for text in ("premier", "deuxième", "troisième"):
        code, ready = line.prepare({"text": text})
        assert code == 200
        ids.append(ready["msg_id"])
        line.show(api.SupportMessage(ready["text"], ready["msg_id"], ready["sent_at"]))
        clock.t += 5
    a, b, c = ids
    assert [(type(e).__name__, e.msg_id, getattr(e, "waiting")) for e in seen] == [
        ("SupportMessageShown", a, 0), ("SupportMessageQueued", b, 1), ("SupportMessageQueued", c, 2)]
    assert line.status()["showing"]["id"] == a and line.status()["waiting"] == [b, c]
    seen.clear()
    line.ack(api.AckSupportMessage(b, clicked_at=clock.t))       # not the one on screen: ignored
    assert seen == []
    clock.t = 1042.5
    line.ack(api.AckSupportMessage(a, clicked_at=1042.0))        # the click's own time counts
    acked, shown = seen
    assert (type(acked).__name__, acked.msg_id, acked.seconds, acked.on_screen_s, acked.waiting) == (
        "SupportMessageAcked", a, 42.0, 42.0, 2)
    assert (type(shown).__name__, shown.msg_id, shown.text, shown.waiting, shown.shown_at) == (
        "SupportMessageShown", b, "deuxième", 1, 1042.5)
    clock.t = 1100.0
    line.ack(api.AckSupportMessage(b))                           # no click time: the drain's
    assert seen[-2].seconds == round(1100.0 - 1005.0, 1) and seen[-2].on_screen_s == 57.5
    line.ack(api.AckSupportMessage(c, clicked_at=1101.0))
    assert line.status()["showing"] is None and line.status()["waiting"] == []
    assert [r["id"] for r in line.status()["read"]] == [a, b, c]
    log = capsys.readouterr().out
    assert f"[Support] message {a} on screen: 'premier'" in log
    assert f"[Support] message {b} waits behind {a}" in log
    assert f"[Support] message {a} acknowledged after 42.0 s" in log
    assert f"acknowledgement of {b} ignored" in log


def test_the_queue_has_a_limit_and_nothing_shows_without_a_window_or_with_the_key_off():
    bus = api.EventBus()
    line = SupportLine(bus, enabled=True, max_waiting=2)
    assert line.prepare({"text": "x"})[0] == 409                 # no window yet: nobody would see it
    bus.ui_ready = True
    for _ in range(3):                                           # one on screen + two waiting
        code, r = line.prepare({"text": "x"})
        assert code == 200
        line.show(api.SupportMessage(r["text"], r["msg_id"], r["sent_at"]))
    code, r = line.prepare({"text": "x"})
    assert code == 429 and "2 message(s) already wait" in r["error"]
    assert line.prepare({"text": "x", "color": "red"})[0] == 400
    seen = _events(bus)
    off = SupportLine(bus, enabled=False)
    off.show(api.SupportMessage("x", "m1", 1.0))                 # the key went off before the drain
    assert seen == [] and off.status()["showing"] is None


def test_the_project_key_is_off_unless_the_file_turns_it_on():
    from core import config
    assert config.SUPPORT_LINE is False
    line = SupportLine(api.EventBus())
    assert line.enabled is False
    for value, on in ((True, True), ("true", True), (1, True), ("on", True), (False, False), ("maybe", False),
                      (None, False), (2, False)):
        line.configure({"support_line": value})
        assert line.enabled is on, value
    line.configure({"support_line": True})
    line.configure({})                                           # a full project without the key: off
    assert line.enabled is False


def test_the_key_is_a_validated_shared_key_and_round_trips_through_the_app_save_and_load(tmp_path):
    import app as app_mod
    from core import config_schema
    from core.config_store import ConfigStore
    assert "support_line" in config_schema._BOOL_KEYS and "support_line" not in config_schema.PROFILE_KEYS
    flat, warnings = config_schema.validate_flat({"support_line": "true"})
    assert flat["support_line"] is True and not warnings
    flat, warnings = config_schema.validate_flat({"support_line": "please"})
    assert "support_line" not in flat and warnings

    def stub(enabled):
        s = mock.MagicMock()
        s.support_line = SupportLine(api.EventBus(), enabled=enabled)
        s.processor.get_exclusion_state.return_value = ([], [], [], [])
        s.roi.state.stored_roi.return_value = (0, 0, 10, 10, 10, 10)
        s.models.model_manager.shape = None
        return s
    # save: the app's own saveable config carries the key, both ways
    assert app_mod.WallDanceApp._get_saveable_config(stub(True))["support_line"] is True
    assert app_mod.WallDanceApp._get_saveable_config(stub(False))["support_line"] is False
    # ... through the schema and the store, as a project save does
    store = ConfigStore(config_dir=str(tmp_path))
    saved = {"model": "yolo11l-pose", "camera_source": "ids", "confidence": 0.3, "support_line": True}
    path = store.save("rehearsal-cordee", config_schema.structure(saved, {}, "show"))
    assert json.loads(pathlib.Path(path).read_text())["support_line"] is True
    loaded, warnings = config_schema.validate_flat(config_schema.flatten(config_schema.migrate(store.load(path))))
    # load: the app's own apply path turns the line on, and a project without the key turns it off
    app = stub(False)
    app_mod.WallDanceApp._apply_config_without_model(app, loaded)
    assert app.support_line.enabled is True
    other = dict(loaded)
    other.pop("support_line")
    app_mod.WallDanceApp._apply_config_without_model(app, other)
    assert app.support_line.enabled is False
    # a lighting-profile switch (a profile bundle) leaves it alone
    app.support_line.enabled = True
    app_mod.WallDanceApp._apply_config_without_model(app, {"gamma": 1.2, "confidence": 0.3})
    assert app.support_line.enabled is True


def test_the_app_registers_the_line_and_hands_it_to_the_remote_api():
    src = (SRC / "app.py").read_text(encoding="utf-8")
    assert "reg(api.SupportMessage, self.support_line.show)" in src
    assert "reg(api.AckSupportMessage, self.support_line.ack)" in src
    loop = (SRC / "runtime" / "main_loop.py").read_text(encoding="utf-8")
    assert 'app._remote.support = getattr(app, "support_line", None)' in loop


# ----------------------------------------------------------------------------------------------
# the band, headless, through the remote API
# ----------------------------------------------------------------------------------------------

@pytest.fixture
def line(tmp_path):
    from services.remote_api import RemoteApi, load_or_create_token
    from ui.adapter import DpgUiAdapter
    rt, bus = api.RuntimeAPI(), api.EventBus()
    adapter = DpgUiAdapter(rt, bus)
    adapter.create_gui({"camera_type": "IDS_PEAK"})
    support = SupportLine(bus, enabled=True)
    rt.register(api.SupportMessage, support.show)                # as app.py registers them
    rt.register(api.AckSupportMessage, support.ack)
    others = []
    rt.register(api.SetState, others.append)
    rt.register(api.ToggleOverlay, others.append)
    state = {"v": "run"}
    token = load_or_create_token(tmp_path / "tok")
    server = RemoteApi(rt, bus, token=token, roots={"projects": tmp_path},
                       state_fn=lambda: state["v"], host="127.0.0.1", port=0)
    server.support = support                                     # as MainLoop._startup does
    assert server.start()

    def call(path, body=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.port}{path}", method="POST" if body is not None else "GET",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def send(text):
        return call("/api/v1/command", {"type": "SupportMessage", "args": {"text": text}})
    try:
        yield SimpleNamespace(rt=rt, bus=bus, adapter=adapter, gui=adapter.gui, support=support, state=state,
                              server=server, call=call, send=send, others=others, token=token)
    finally:
        server.stop()
        dpg.destroy_context()


def _band():
    return bool(dpg.get_item_configuration("support_band_window")["show"])


def _click(gui, past_guard=True):
    if past_guard:
        gui._support_shown_t -= 5.0
    dpg.get_item_callback("support_band_ack_btn")()


def test_the_message_shows_until_he_acknowledges_it(line, capsys):
    from gui_builder import SUPPORT_ACK_LABEL, SUPPORT_BAND_TITLE
    assert not _band()
    code, res = line.send("Bonjour Nico, relance Calibrate s'il te plaît")
    assert code == 200 and res["id"] and res["text"] == "Bonjour Nico, relance Calibrate s'il te plaît"
    assert not _band()                                           # queued: the main loop shows it
    line.rt.drain()
    assert _band()
    assert dpg.get_value("support_band_text") == res["text"]
    assert dpg.get_value("support_band_title") == SUPPORT_BAND_TITLE
    assert dpg.get_item_configuration("support_band_ack_btn")["label"] == SUPPORT_ACK_LABEL == "J'ai lu"
    assert "reçu à" in dpg.get_value("support_band_meta")
    for _ in range(50):                                          # ticks go by: it stays
        line.rt.drain()
        line.gui.render_frame()
        assert _band()
    assert line.call("/api/v1/status")[1]["support_line"]["showing"]["id"] == res["id"]
    _click(line.gui)
    assert _band()                                               # the click asks; the runtime decides
    line.rt.drain()
    assert not _band()
    code, ev = line.call(f"/api/v1/events?since={res['event_seq']}&types=SupportMessageAcked")
    (ack,) = ev["events"]
    assert ack["msg_id"] == res["id"] and ack["text"] == res["text"]
    assert ack["seconds"] == round(ack["acked_at"] - ack["sent_at"], 1) >= 0
    assert ack["acked_at"] >= ack["shown_at"] >= ack["sent_at"]
    st = line.call("/api/v1/status")[1]["support_line"]
    assert st["showing"] is None and st["read"][-1]["id"] == res["id"]
    log = capsys.readouterr().out
    assert f"[RemoteApi] SupportMessage {res['id']} queued" in log
    assert f"[Support] message {res['id']} on screen" in log and "acknowledged after" in log


def test_the_band_never_blocks_the_operator(line, monkeypatch):
    from ui.key_guard import TEXT_FIELD_TYPES

    def boom(*a, **k):
        raise AssertionError("the support line must not pump, sleep or wait in the frame loop")
    assert line.send("Tout va bien, continue")[0] == 200
    monkeypatch.setattr(dpg, "render_dearpygui_frame", boom)
    monkeypatch.setattr(dpg, "split_frame", boom)
    monkeypatch.setattr(time, "sleep", boom)
    t0 = time.perf_counter()
    line.rt.drain()                                              # the show and the paint, in the tick
    shown_ms = (time.perf_counter() - t0) * 1000
    monkeypatch.undo()
    assert _band() and shown_ms < 50, shown_ms
    # no modal, no popup, no window of its own: a child window inside the preview's panel
    band = dpg.get_alias_id("support_band_window")
    assert dpg.get_item_type(band) == "mvAppItemType::mvChildWindow"
    for item in dpg.get_all_items():
        if dpg.get_item_type(item) == "mvAppItemType::mvWindowAppItem":
            cfg = dpg.get_item_configuration(item)
            assert not (cfg.get("show") and (cfg.get("modal") or cfg.get("popup"))), dpg.get_item_alias(item)

    def ancestors(item):
        out = []
        while item:
            item = dpg.get_item_parent(item)
            if item:
                out.append(dpg.get_item_alias(item) or item)
        return out
    assert ancestors(band)[0] == "video_panel"
    # in the panel's flow, above the preview (no absolute position: it pushes the picture down, covers nothing)
    order = [dpg.get_item_alias(k) for k in dpg.get_item_children("video_panel", 1)]
    assert order.index("support_band_window") < order.index("video_image")
    # the readiness rows, the alerts strip, the health figures and the operator's controls are elsewhere
    for tag in ("readiness_rows_container", "alerts_text", "bottom_stats_group", "state_standby_btn",
                "state_run_btn", "remote_control_checkbox"):
        assert "video_panel" not in ancestors(dpg.get_alias_id(tag)), tag
    # nothing in it takes the keyboard: shortcuts still fire, the buttons still ask
    def walk(item):
        for kids in (dpg.get_item_children(item) or {}).values():
            for k in kids:
                yield k
                yield from walk(k)
    assert not [k for k in walk(band) if dpg.get_item_type(k) in TEXT_FIELD_TYPES]
    monkeypatch.setattr(dpg, "is_item_active", lambda item: False)
    monkeypatch.setattr(dpg, "is_key_down", lambda k: False)
    line.adapter._handle_key(None, dpg.mvKey_T)
    line.gui._on_state_standby()
    line.rt.drain()
    assert [type(c).__name__ for c in line.others] == ["ToggleOverlay", "SetState"]
    assert _band()                                               # and the message is still there
    t0 = time.perf_counter()
    _click(line.gui)
    line.rt.drain()
    assert not _band() and (time.perf_counter() - t0) * 1000 < 50


def test_a_second_message_waits_and_a_double_click_never_reads_the_next(line):
    code, first = line.send("Message un")
    code2, second = line.send("Message deux")
    assert code == code2 == 200
    line.rt.drain()
    assert dpg.get_value("support_band_text") == "Message un"
    assert "encore 1 message après celui-ci" in dpg.get_value("support_band_meta")
    _click(line.gui)
    line.rt.drain()
    assert _band() and dpg.get_value("support_band_text") == "Message deux"   # its turn
    assert "encore" not in dpg.get_value("support_band_meta")
    _click(line.gui, past_guard=False)                           # the second click of a double click
    line.rt.drain()
    assert _band() and dpg.get_value("support_band_text") == "Message deux"
    _click(line.gui)
    line.rt.drain()
    assert not _band()
    _, ev = line.call(f"/api/v1/events?since={first['event_seq']}&types=SupportMessageShown,"
                      "SupportMessageQueued,SupportMessageAcked")
    assert [(e["type"], e["msg_id"]) for e in ev["events"]] == [
        ("SupportMessageShown", first["id"]), ("SupportMessageQueued", second["id"]),
        ("SupportMessageAcked", first["id"]), ("SupportMessageShown", second["id"]),
        ("SupportMessageAcked", second["id"])]


def test_accents_reach_the_band_as_typed(line, font):
    code, res = line.send(FRENCH)
    assert code == 200 and res["text"] == SHOWN
    assert any("not in the screen font" in n for n in res["notes"])
    line.rt.drain()
    shown = dpg.get_value("support_band_text")
    assert shown == SHOWN and "é" in shown and "ç" in shown and "à" in shown and "«" in shown
    assert all(ord(c) in font.cmap for c in shown + dpg.get_value("support_band_meta")
               + dpg.get_value("support_band_title"))


# ----------------------------------------------------------------------------------------------
# the API policy
# ----------------------------------------------------------------------------------------------

def test_refused_when_the_key_is_off_and_nothing_shows(line):
    line.support.enabled = False
    code, res = line.send("Bonjour")
    assert code == 403 and "support line is off in this project (project key support_line)" in res["error"]
    assert line.rt.pending() == 0
    line.rt.drain()
    assert not _band()
    assert line.call("/api/v1/status")[1]["support_line"]["enabled"] is False
    line.server.support = None                                   # an app without the line at all
    code, res = line.send("Bonjour")
    assert code == 403 and "no support line" in res["error"]


def test_accepted_in_run_with_the_key_on_and_the_remote_control_tick_off(line):
    from services.remote_api import POLICY, SUPPORT, check_policy
    line.state["v"] = "run"
    line.server.control_enabled = False
    code, res = line.call("/api/v1/command", {"type": "SetState", "args": {"state": "standby"}})
    assert code == 403 and "Allow remote control" in res["error"]         # control stays gated
    code, res = line.send("Pendant le RUN")
    assert code == 200
    line.rt.drain()
    assert _band()
    line.state["v"] = "standby"                                  # and in STANDBY
    assert line.send("En STANDBY")[0] == 200
    # the key governs it, the tick does not, in every state
    assert POLICY["SupportMessage"] == SUPPORT
    for state in ("run", "standby"):
        for tick in (False, True):
            assert check_policy("SupportMessage", {}, state, tick, support_line=True) is None
            assert "support line is off" in check_policy("SupportMessage", {}, state, tick, support_line=False)


def test_the_remote_cannot_acknowledge_and_bad_text_is_refused(line):
    line.send("Un message")
    line.rt.drain()
    msg_id = line.support.status()["showing"]["id"]
    code, res = line.call("/api/v1/command", {"type": "AckSupportMessage", "args": {"msg_id": msg_id}})
    assert code == 403 and "never allowed remotely" in res["error"]
    for text, why in (("<b>Vite</b>", "no markup"), ("x" * 281, "too long"), ("  \n ", "empty")):
        code, res = line.send(text)
        assert code == 400 and why in res["error"], text
    assert line.call("/api/v1/command", {"type": "SupportMessage", "args": {"text": "a", "msg_id": "x"}})[0] == 400
    assert line.rt.pending() == 0
    _, schema = line.call("/api/v1/commands")
    assert schema["SupportMessage"]["policy"] == "support" and "AckSupportMessage" not in schema


# ----------------------------------------------------------------------------------------------
# the client: extra/wdremote.py support-send / support-ack (no ssh: LocalTransport on loopback)
# ----------------------------------------------------------------------------------------------

def _wdremote():
    spec = importlib.util.spec_from_file_location(
        "wdremote_s1b", pathlib.Path(__file__).resolve().parents[2] / "extra" / "wdremote.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wdremote_s1b"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def client(line, tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("LocalTransport emulates the laptop with a posix shell")
    wd = _wdremote()
    monkeypatch.setattr(wd, "DEFAULT_CONFIG", tmp_path / "cfg" / "remote.json")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "remote_token.local").write_text(line.token + "\n")
    remote = wd.Remote(host="local", root=str(tmp_path), os="posix", api_port=line.server.port)
    return SimpleNamespace(wd=wd, tr=wd.LocalTransport(remote), remote=remote)


def _the_laptop(line, stop, click_after_s=0.3):
    """The app's main loop (drains) and Nico (clicks a while after each message shows)."""
    def run():
        while not stop.is_set():
            line.rt.drain()
            if line.gui._support_msg_id and time.monotonic() - line.gui._support_shown_t > click_after_s:
                dpg.get_item_callback("support_band_ack_btn")()
            time.sleep(0.01)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_wdremote_sends_then_reads_the_acknowledgement(line, client, capsys):
    wd = client.wd
    a = wd.build_parser().parse_args(["support-send", "Relance Calibrate, s’il te plaît"])
    assert a.cmd == "support-send" and a.wait == 0.0
    assert wd.cmd_support_send(client.tr, client.remote, a) == 0
    out = capsys.readouterr().out
    msg_id = out.split("[support] ")[1].split(" sent at")[0]
    assert "Relance Calibrate, s'il te plaît" in out and "not in the screen font" in out
    # not read yet: on screen, exit 2
    line.rt.drain()
    a = wd.build_parser().parse_args(["support-ack", msg_id])
    assert wd.cmd_support_ack(client.tr, client.remote, a) == 2
    assert "NOT READ yet: on screen since" in capsys.readouterr().out
    # Nico reads it; support-ack --wait sees the event and the time it took
    stop = threading.Event()
    laptop = _the_laptop(line, stop, click_after_s=1.2)
    try:
        a = wd.build_parser().parse_args(["support-ack", msg_id, "--wait", "20", "--json"])
        assert wd.cmd_support_ack(client.tr, client.remote, a) == 0
    finally:
        stop.set()
        laptop.join(5)
    out = capsys.readouterr().out
    ack = json.loads(out[out.index("\n{") + 1:])                # after the laptop thread's log line
    assert ack["type"] == "SupportMessageAcked" and ack["msg_id"] == msg_id and ack["seconds"] >= 1.0
    # the line's state, and the same message read back later
    a = wd.build_parser().parse_args(["support-ack", msg_id])
    assert wd.cmd_support_ack(client.tr, client.remote, a) == 0
    assert f"{msg_id} READ at" in capsys.readouterr().out
    a = wd.build_parser().parse_args(["support-ack"])
    assert wd.cmd_support_ack(client.tr, client.remote, a) == 0
    out = capsys.readouterr().out
    assert "the line is ON" in out and f"read: {msg_id}" in out
    assert wd.cmd_support_ack(client.tr, client.remote, wd.build_parser().parse_args(["support-ack", "nope"])) == 1


def test_wdremote_send_and_wait_from_a_file_and_a_refusal(line, client, capsys, tmp_path):
    wd = client.wd
    msg = tmp_path / "msg.txt"
    msg.write_text("Coupe le RUN, on regarde l'image ensemble", encoding="utf-8")
    stop = threading.Event()
    laptop = _the_laptop(line, stop)
    try:
        a = wd.build_parser().parse_args(["support-send", "--file", str(msg), "--wait", "20"])
        assert wd.cmd_support_send(client.tr, client.remote, a) == 0
    finally:
        stop.set()
        laptop.join(5)
    out = capsys.readouterr().out
    assert "sent at" in out and "READ at" in out and "s after it was sent" in out
    line.support.enabled = False
    a = wd.build_parser().parse_args(["support-send", "Bonjour"])
    assert wd.cmd_support_send(client.tr, client.remote, a) == 1
    assert "refused (403)" in capsys.readouterr().out


def test_wdwatch_stays_get_only():
    src = (pathlib.Path(__file__).resolve().parents[2] / "extra" / "wdwatch.py").read_text(encoding="utf-8")
    assert "SupportMessage" not in src and "support-send" not in src
