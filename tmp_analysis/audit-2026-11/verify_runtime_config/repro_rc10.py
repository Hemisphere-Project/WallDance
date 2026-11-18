"""runtime-core#10: the real job launchers on a duck-typed app (the worker body is a sleep),
then the same subprocess pattern across a parent exit."""
import os, subprocess, sys, threading, time, types
from types import SimpleNamespace
from app import WallDanceApp
from runtime import api
from runtime.api import SystemState

started = []
def worker(*a):
    started.append(threading.current_thread().name); time.sleep(3)
app = SimpleNamespace(system_state=SystemState.STANDBY, bus=SimpleNamespace(publish=lambda e: None),
                      _warn_offline_transform=lambda what: None,
                      _run_known_n=worker, _run_calib_sweep=worker, _run_dry_run_replay=worker,
                      _run_readiness_check=lambda: None)
app._cb_system_state_change = types.MethodType(WallDanceApp._cb_system_state_change, app)
WallDanceApp._cb_run_known_n(app); WallDanceApp._cb_run_known_n(app)      # double click
WallDanceApp._cb_run_calib_sweep(app, 2); WallDanceApp._cb_run_dry_run(app)
time.sleep(0.2)
jobs = lambda: sorted(t.name for t in threading.enumerate() if t.name in ("KnownNTune", "CalibSweep", "DryRunReplay"))
print("STANDBY, after 2x Known-N + sweep + dry-run:", jobs())
WallDanceApp._cmd_set_state(app, api.SetState("run"))                       # operator presses RUN
print("after SetState('run'): state =", app.system_state.name, " jobs still running:", jobs())
WallDanceApp._cb_run_known_n(app)
print("a new job in RUN is refused:", jobs().count("KnownNTune") == 2)

# the child of a daemon thread's subprocess.run across the parent's exit
parent = ("import subprocess,sys,threading,time\n"
          "t=threading.Thread(target=lambda: subprocess.run([sys.executable,'-c','import time;time.sleep(8)'],"
          "capture_output=True,text=True,timeout=2400),daemon=True)\n"
          "t.start(); time.sleep(0.5)\n"
          "import os; print(os.getpid())\n")
p = subprocess.run([sys.executable, "-c", parent], capture_output=True, text=True)
ppid = p.stdout.strip()
time.sleep(0.5)
ps = subprocess.run(["ps", "-eo", "pid,ppid,etimes,cmd"], capture_output=True, text=True).stdout
orphans = [l for l in ps.splitlines() if "time.sleep(8)" in l and "ps -eo" not in l]
print(f"parent pid {ppid} exited rc={p.returncode}; its child after the exit:", orphans)
