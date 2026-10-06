# -*- mode: python ; coding: utf-8 -*-


# Path-independent (was hard-coded to one developer's checkout): resolve
# customtkinter from the build venv, and bundle build_info.json (commit/ref/
# build stamp written by `wdremote launcher build`) so the exe can say what it is.
import os
import customtkinter

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[(os.path.dirname(customtkinter.__file__), 'customtkinter/'),
           ('build_info.json', '.')],
    hiddenimports=['win32timezone'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WallDanceLauncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
