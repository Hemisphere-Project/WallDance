import os
import subprocess
import winshell
from win32com.client import Dispatch

def restart_and_delete(current_exe, target_exe, args=""):
    """Starts the target executable and deletes the current one."""
    try:
        bat_path = os.path.join(os.environ.get('TEMP', ''), "walldance_restart.bat")
        with open(bat_path, "w") as f:
            f.write(f"""@echo off
timeout /t 2 /nobreak > NUL
del "{current_exe}"
start "" "{target_exe}" {args}
del "%~f0"
""")
        subprocess.Popen(bat_path, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception as e:
        print(f"Failed to restart: {e}")
        return False

def create_desktop_shortcut(target_exe):
    """Creates a desktop shortcut for the given executable."""
    try:
        desktop = winshell.desktop()
        path = os.path.join(desktop, "WallDance.lnk")
        
        shell = Dispatch('WScript.Shell')
        shortcut = shell.CreateShortCut(path)
        shortcut.Targetpath = target_exe
        shortcut.WorkingDirectory = os.path.dirname(target_exe)
        shortcut.IconLocation = target_exe
        shortcut.save()
        return True
    except Exception as e:
        print(f"Failed to create shortcut: {e}")
        return False
