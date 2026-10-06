#!/usr/bin/env python3
"""Copy files uploaded next to this script (wdremote py --with FILE) to the logged-on user's Desktop.

  python extra/wdremote.py py --with tmp_analysis/field/TERRAIN_2026-10-07_FR.pdf tmp_analysis/plan25m/to_desktop.py -- TERRAIN_2026-10-07_FR.pdf
On Windows the Desktop is asked from the shell (SHGetKnownFolderPath), so a OneDrive-redirected or localized
("Bureau") Desktop is found; elsewhere ~/Desktop.  --dry-run prints the destination only.
"""
import os, shutil, sys

def desktop():
    if os.name == "nt":
        import ctypes, uuid
        from ctypes import wintypes
        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]
        u = uuid.UUID("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")   # FOLDERID_Desktop
        g = GUID(); g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
            path = p.value; ctypes.windll.ole32.CoTaskMemFree(p)
            return path
    return os.path.join(os.path.expanduser("~"), "Desktop")

here = os.path.dirname(os.path.abspath(__file__))
args = [a for a in sys.argv[1:] if a != "--dry-run"]
dest = desktop()
for name in args:
    src = os.path.join(here, os.path.basename(name))
    if not os.path.exists(src):
        print(f"missing {src} (pass it with --with before the script)"); continue
    print(("would copy" if "--dry-run" in sys.argv else "copied"), os.path.basename(src), "->", dest)
    if "--dry-run" not in sys.argv:
        os.makedirs(dest, exist_ok=True); shutil.copy2(src, os.path.join(dest, os.path.basename(src)))
