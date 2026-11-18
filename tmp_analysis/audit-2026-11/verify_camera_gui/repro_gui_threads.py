"""gui-operator#10 (Toast from worker threads runs DPG code on that thread),
gui-operator#5 (StateChanged is dropped by the adapter),
gui-operator#1 (key handler has no text-focus guard: what bare E / P submit).

REAL: runtime.api.EventBus / RuntimeAPI, ui.adapter.DpgUiAdapter (handler tables, _handle_key),
gui.WallDanceGUI.show_toast / set_system_state (unbound, on a headless DPG context: no viewport,
no window on screen, the app is not launched).
"""
from __future__ import annotations

import collections
import sys
import threading
import time
from types import SimpleNamespace

import dearpygui.dearpygui as dpg

from runtime import api
from runtime.api import SystemState
from ui.adapter import DpgUiAdapter
import gui as gui_module
from gui import WallDanceGUI


def part_threads():
    print("=== #10  which thread runs gui.show_toast for a Toast published by a worker ===")
    bus, rt = api.EventBus(), api.RuntimeAPI()
    ad = DpgUiAdapter(rt, bus)
    seen = []
    ad.gui = SimpleNamespace(show_toast=lambda msg, duration, color: seen.append(
        (msg, threading.current_thread().name)))
    bus.publish(api.Toast("from the main loop", 2.0, (1, 2, 3)))
    t = threading.Thread(target=lambda: bus.publish(api.Toast("from OpsReadiness", 6.0, (1, 2, 3))),
                         name="OpsReadiness")
    t.start(); t.join()
    for row in seen:
        print("   ", row)

    print("=== #10  the real show_toast body from two threads at once (headless DPG, no render) ===")
    dpg.create_context()
    stub = SimpleNamespace(_toast_deadline=0.0)
    errors = collections.Counter()
    done = collections.Counter()

    def hammer(name, n):
        for i in range(n):
            try:
                WallDanceGUI.show_toast(stub, f"{name} {i}", 2.0, (255, 255, 255))
                done[name] += 1
            except Exception as e:  # noqa: BLE001
                errors[f"{name}: {type(e).__name__}: {str(e).splitlines()[0][:90]}"] += 1

    items0 = len(dpg.get_all_items())
    worker = threading.Thread(target=hammer, args=("worker", 3000), name="OpsReadiness")
    worker.start()
    hammer("main", 3000)
    worker.join()
    items1 = len(dpg.get_all_items())
    print(f"    calls completed: {dict(done)}  (3000 asked per thread)")
    print(f"    exceptions: {sum(errors.values())}")
    for k, v in errors.most_common(6):
        print(f"      {v:5d} x {k}")
    print(f"    DPG items alive: before {items0}, after {items1} (one toast = 2 items)")
    dpg.destroy_context()


def part_state():
    print("=== #5   a remote SetState and the operator's badge ===")
    dpg.create_context()
    with dpg.window(tag="w"):
        dpg.add_text("RUN", tag="state_badge")
        dpg.add_button(label="STANDBY", tag="state_standby_btn")
        dpg.add_button(label="RUN", tag="state_run_btn")
    with dpg.theme() as th:
        pass
    bus, rt = api.EventBus(), api.RuntimeAPI()
    ad = DpgUiAdapter(rt, bus)
    g = SimpleNamespace(_system_state=SystemState.RUN, callbacks={},
                        _btn_standby_active_theme=th, _btn_standby_theme=th,
                        _btn_run_active_theme=th, _btn_run_theme=th)
    ad.gui = g
    runtime_state = {"s": "run"}
    # the runtime's handler (app.py:1056-1062): mirror + StateChanged on the bus
    def cmd_set_state(state):
        runtime_state["s"] = state
        bus.publish(api.StateChanged(state))
    cmd_set_state("standby")                          # e.g. wdremote cmd SetState state=standby
    print(f"    runtime state = {runtime_state['s']!r};  gui._system_state = {g._system_state.name}, "
          f"badge text = {dpg.get_value('state_badge')!r}")
    print(f"    adapter handler for StateChanged returns: "
          f"{ad._event_handlers[api.StateChanged](api.StateChanged('standby'))!r}")
    # the operator's own click goes through set_system_state and does update the badge
    WallDanceGUI.set_system_state(g, SystemState.STANDBY)
    print(f"    after a LOCAL click on STANDBY: gui._system_state = {g._system_state.name}, "
          f"badge text = {dpg.get_value('state_badge')!r}")
    dpg.destroy_context()


def part_keys():
    print("=== #1   what the global key handler submits (no check for a focused text input) ===")
    dpg.create_context()
    with dpg.window(tag="w2"):
        dpg.add_input_text(tag="rig_notes", default_value="")
    bus, rt = api.EventBus(), api.RuntimeAPI()
    ad = DpgUiAdapter(rt, bus)
    ad.gui = SimpleNamespace(project_picker_visible=lambda: False, expert_mode=False)
    ad.wizard = SimpleNamespace(active=False)
    got = []
    rt.submit = lambda c: got.append(c)
    ad.api = SimpleNamespace(submit=lambda c: got.append(c))
    for ch in "IR pass filter":
        key = getattr(dpg, f"mvKey_{ch.upper()}", None)
        if key is not None:
            ad._handle_key(None, key)
    print(f"    typing 'IR pass filter' -> {len(got)} commands:")
    for c in got:
        print("     ", c)
    src = open(gui_module.__file__.replace("gui.py", "ui/adapter.py")).read()
    body = src.split("def _handle_key", 1)[1].split("def _on_event", 1)[0]
    print("    guards in _handle_key: is_item_active/is_item_focused used:",
          "is_item_active" in body or "is_item_focused" in body)
    dpg.destroy_context()


if __name__ == "__main__":
    which = sys.argv[1:] or ["threads", "state", "keys"]
    if "threads" in which:
        part_threads()
    if "state" in which:
        part_state()
    if "keys" in which:
        part_keys()
