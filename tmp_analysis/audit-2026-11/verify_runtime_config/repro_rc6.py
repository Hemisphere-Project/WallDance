"""runtime-core#6: a remote SetState against the real DpgUiAdapter + real handlers."""
import types, threading
from types import SimpleNamespace
from app import WallDanceApp
from runtime import api
from runtime.api import SystemState
from ui.adapter import DpgUiAdapter
import inspect

class FakeGui:
    def __init__(self):
        self._system_state = SystemState.RUN      # gui.py:316
        self.calls = []
    def __getattr__(self, name):
        def rec(*a, **k): self.calls.append(name)
        return rec

bus, rt = api.EventBus(), api.RuntimeAPI()
adapter = DpgUiAdapter(rt, bus)
adapter.gui = FakeGui(); bus.ui_ready = True
readiness = []
app = SimpleNamespace(system_state=SystemState.RUN, bus=bus,     # app.py:521
                      _run_readiness_check=lambda: readiness.append("ran"))
app._cb_system_state_change = types.MethodType(WallDanceApp._cb_system_state_change, app)
rt.register(api.SetState, lambda c: WallDanceApp._cmd_set_state(app, c))

def step(label, cmd):
    rt.submit(cmd); rt.drain()
    for t in threading.enumerate():
        if t.name == "OpsReadiness": t.join()
    print(f"{label:38s} runtime={app.system_state.name:8s} gui._system_state={adapter.gui._system_state.name:8s} "
          f"gui calls={adapter.gui.calls} readiness runs={len(readiness)}")

print("boot:                                  runtime=%s      gui._system_state=%s      readiness runs=%d"
      % (app.system_state.name, adapter.gui._system_state.name, len(readiness)))
step("remote SetState('standby')", api.SetState("standby"))
step("remote SetState('run')", api.SetState("run"))
print("adapter handler for StateChanged:", inspect.getsource(adapter._build_event_handlers).split("api.StateChanged:")[1].strip().splitlines()[0])
