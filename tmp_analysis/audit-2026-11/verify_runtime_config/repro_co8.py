"""config-ops#8: 'latest' after renames, and the project identity of a copied folder.
Real ConfigStore; temp dir only."""
import io, contextlib, json, os, shutil, tempfile, time
import core.config_store as cs
from core.config_store import ConfigStore

def fresh():
    tmp = tempfile.mkdtemp(prefix="co8_", dir=os.environ["WD_TMP"])
    return ConfigStore(config_dir=os.path.join(tmp, "projects"),
                       last_project_file=os.path.join(tmp, "projects", "last_project.txt"))

def latest(store, name):
    p = store.latest_for_project(name)
    return os.path.basename(p), json.load(open(p))["marker"]

def scenario(listdir, label):
    real = os.listdir
    store = fresh()
    store.save("test", {"marker": 1}); time.sleep(0.05)
    store.save("test", {"marker": 2}); time.sleep(0.05)
    print(f"[{label}] before any rename           : latest = {latest(store, 'test')}")
    cs.os.listdir = listdir
    try:
        store.rename_project("test", "Show"); time.sleep(0.05)
        print(f"[{label}] after rename test -> Show   : latest = {latest(store, 'Show')}")
        store.save("Show", {"marker": 3}); time.sleep(0.05)      # the newest save
        print(f"[{label}] + a new save (marker 3)     : latest = {latest(store, 'Show')}")
        store.rename_project("Show", "Show2")
        print(f"[{label}] after rename Show -> Show2  : latest = {latest(store, 'Show2')}   (newest save is marker 3)")
    finally:
        cs.os.listdir = real

_real_listdir = os.listdir
scenario(_real_listdir, "this fs, os.listdir order")
scenario(lambda p: sorted(_real_listdir(p), key=str.upper), "alphabetical listdir (NTFS)")

# a project folder duplicated in the file manager
store = fresh()
store.save("ShowA", {"marker": "A"})
shutil.copytree(os.path.join(store.config_dir, "ShowA"), os.path.join(store.config_dir, "ShowB"))
print("picker lists:", store.list_projects())
p = store.latest_for_project("ShowB")
print("launch 'ShowB' -> file", os.path.relpath(p, store.config_dir),
      "-> infer_project_from_config =", repr(store.infer_project_from_config(store.load(p), p)))
