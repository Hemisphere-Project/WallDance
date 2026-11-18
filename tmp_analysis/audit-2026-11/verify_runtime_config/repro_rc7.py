"""runtime-core#7 / config-ops#3: keys the live apply reads vs keys Save writes, then a
Known-N Apply followed by an operator Save, through the real code. Temp dir only."""
import ast, inspect, io, contextlib, json, os, tempfile, textwrap, types
from types import SimpleNamespace
from unittest.mock import MagicMock

from app import WallDanceApp
from core import config_schema
from core.config_store import ConfigStore
from runtime.config_manager import ConfigManager

# ---- 1. static: keys read by the apply vs keys in the saved dict ----------------
def keys_read(fn):
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare) and isinstance(n.left, ast.Constant) and isinstance(n.left.value, str) \
                and any(isinstance(o, ast.In) for o in n.ops) \
                and any(isinstance(c, ast.Name) and c.id == "config" for c in n.comparators):
            out.add(n.left.value)
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == "config" \
                and isinstance(n.slice, ast.Constant):
            out.add(n.slice.value)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" \
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "config" \
                and n.args and isinstance(n.args[0], ast.Constant):
            out.add(n.args[0].value)
        # the identity-slot loop: (("key", DEFAULT), ...)
        if isinstance(n, ast.For) and isinstance(n.iter, ast.Tuple):
            for el in n.iter.elts:
                if isinstance(el, ast.Tuple) and el.elts and isinstance(el.elts[0], ast.Constant):
                    out.add(el.elts[0].value)
    return out

mock = MagicMock()
mock.processor.get_exclusion_state.return_value = ((16, 10), [], [], [])
mock.roi.state.stored_roi.return_value = (0, 0, 100, 100, 1920, 1080)
mock.calibration.calibration_state = {}
mock.rig_sheet = {}
saved_dict = WallDanceApp._get_saveable_config(mock)
saved = set(saved_dict)
applied = keys_read(WallDanceApp._apply_config_without_model)
print(f"saved keys: {len(saved)}   keys the apply reads: {len(applied)}")
print("read by apply, NOT saved :", sorted(applied - saved))
print("saved, NOT read by apply :", sorted(saved - applied))

# ---- 2. dynamic: Known-N Apply, then operator Save ------------------------------
tmp = tempfile.mkdtemp(prefix="rc7_", dir=os.environ["WD_TMP"])
store = ConfigStore(config_dir=os.path.join(tmp, "projects"),
                    last_project_file=os.path.join(tmp, "projects", "last_project.txt"))
jsonable = lambda v: v if isinstance(v, (int, float, str, bool, list, dict, type(None))) else 0
live = {k: jsonable(v) for k, v in saved_dict.items()}        # what Save reads back from the live objects
mgr = ConfigManager(models=MagicMock(), cameras=MagicMock(), recording=MagicMock(), recorder=MagicMock(),
                    camera=MagicMock(), unified_camera=None, use_unified=False, settings=MagicMock(),
                    ui=SimpleNamespace(available=False), watchdog=lambda: MagicMock(),
                    apply_config=lambda c: None, saveable_config=lambda: dict(live),
                    request_reprocess=lambda: None)
mgr.config_store = store
mgr._current_project = "Show"
def newest():
    return config_schema.flatten(store.load(store.latest_for_project("Show")))
KEYS = ("crossval_skel_min_kpts", "crossval_motion_min_ratio", "tracker_swap_correctors",
        "tracker_intermittent_confirm", "tracker_ghost_skeleton_age")
def show(label):
    f = newest()
    print(f"{label:34s}", {k: f.get(k, '<absent>') for k in KEYS})

with contextlib.redirect_stdout(io.StringIO()):
    mgr._cb_do_save_config("Show")                            # the project's first save
show("1. first Save")
import time; time.sleep(1.1)
applied_live = []
fake = SimpleNamespace(_known_n_tuned={"confidence": 0.25, "crossval_skel_min_kpts": 10,
                                       "crossval_motion_min_ratio": 0.04, "tracker_max_age": 60,
                                       "tracker_intermittent_confirm": True, "tracker_ghost_skeleton_age": 15},
                       configs=mgr, bus=SimpleNamespace(publish=lambda e: None),
                       _apply_config_without_model=applied_live.append)
with contextlib.redirect_stdout(io.StringIO()):
    WallDanceApp._cb_apply_known_n(fake)                      # the real Apply (app.py:2617-2640)
show("2. after Known-N Apply")
# a hand edit, as the code comment at app.py:1447-1452 describes for a contact duo
p = store.latest_for_project("Show"); d = json.load(open(p)); d["tracker_swap_correctors"] = True
json.dump(d, open(p, "w"), indent=2)
show("3. + hand-set swap correctors")
# the live objects now hold the tuned values for the two keys Save knows about
live.update(tracker_intermittent_confirm=True, tracker_ghost_skeleton_age=15, tracker_max_age=60, confidence=0.25)
time.sleep(1.1)
with contextlib.redirect_stdout(io.StringIO()):
    mgr._cb_do_save_config("Show")                            # operator presses Save / confirms a Calibrate
show("4. after the next Save")
