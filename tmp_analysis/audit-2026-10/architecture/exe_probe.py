import sys, zlib
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
exe = "/data/WallDance/launcher/release/WallDanceLauncher.exe"
car = CArchiveReader(exe)
names = list(car.toc.keys())
print("CArchive entries:", len(names))
pyz_name = [n for n in names if n.endswith(".pyz") or n == "PYZ-00.pyz" or "PYZ" in n]
print("pyz:", pyz_name)
scripts = [n for n in names if car.toc[n][-1] in ('s',) ] if False else None
for n in names:
    if n in ("main", "gui", "git_manager") or n.startswith("pyi"):
        pass
# raw search across all entries
import os
for n in names:
    try:
        data = car.extract(n)
    except Exception as e:
        continue
    if data is None: continue
    for key in (b"can_fast_forward", b"DirtyWorkingTreeError", b"UpdateStatus", b"has_updates", b"Force-sync"):
        if key in data:
            print("HIT", n, key)
pyz = [n for n in names if n.endswith('.pyz') or 'PYZ' in n]
for p in pyz:
    tmp = os.path.abspath("pyz.bin")
    open(tmp,"wb").write(car.extract(p))
    z = ZlibArchiveReader(tmp)
    for mod in ("git_manager","gui","process_runner","install_manager","dulwich"):
        if mod in z.toc:
            try:
                co = z.extract(mod, raw=True)
            except TypeError:
                co = None
            print("PYZ has", mod)
    # raw-scan of git_manager
    for mod in ("git_manager","gui"):
        if mod in z.toc:
            try:
                raw = z.extract(mod, raw=True)
                for key in (b"can_fast_forward", b"DirtyWorkingTreeError", b"UpdateStatus", b"has_updates", b"Force-sync"):
                    print(mod, key, key in raw)
            except Exception as e:
                print("extract err", mod, e)
    dv = [m for m in z.toc if m.startswith("dulwich")]
    print("dulwich modules:", len(dv))
