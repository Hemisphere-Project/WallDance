"""config-ops#5: the watchdog's default hang handler with the app's log tee installed.
The tee goes to a temp dir (never the repo's logs/)."""
import faulthandler, os, sys, tempfile, threading, time
from pathlib import Path
from services import app_log
from core.ops_monitor import LoopWatchdog

real_out = sys.stdout
tmp = Path(tempfile.mkdtemp(prefix="co5_", dir=os.environ["WD_TMP"]))
log = app_log.install(tmp, keep=40, header="[AppLog] test header")       # what app.main() does
def say(*a): real_out.write(" ".join(str(x) for x in a) + "\n"); real_out.flush()

say("sys.stderr is", type(sys.stderr).__name__, "| has usable fileno():", end := None) if False else None
try:
    sys.stderr.fileno(); say("sys.stderr.fileno(): ok")
except Exception as e:
    say("sys.stderr.fileno() ->", type(e).__name__, e)
try:
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True); say("direct dump_traceback(file=sys.stderr): ok")
except Exception as e:
    say("direct dump_traceback(file=sys.stderr) ->", type(e).__name__, e)

LoopWatchdog._default_on_hang(12.3)                     # the handler app.py:735 uses
sys.stdout.flush(); sys.stderr.flush()
text = log.read_text()
say("--- log file after the hang handler ---"); say(text.rstrip())
say("--- stack frames in the log:", ("File \"" in text) or ("Thread 0x" in text) or ("Current thread" in text))

# what the suggested fix would write (the real file handle)
with open(tmp / "probe.txt", "w") as fh:
    faulthandler.dump_traceback(file=fh, all_threads=True)
say("with a real file handle: stack lines =", sum(1 for l in open(tmp / 'probe.txt') if 'File "' in l))

# second half: a Python watchdog thread vs a main thread inside one C call that keeps the GIL
events = []
t0 = time.monotonic()
wd = LoopWatchdog(hang_s=1.0, poll_s=0.2,
                  on_hang=lambda age: events.append(("hang", round(time.monotonic() - t0, 2), round(age, 2))),
                  on_resume=lambda d: events.append(("resume", round(time.monotonic() - t0, 2))))
wd.start(); wd.beat()
t0 = time.monotonic(); time.sleep(3.0); wd.beat(); time.sleep(0.5)        # block that RELEASES the GIL
say("3 s block releasing the GIL (sleep):   ", events); events.clear()
wd.beat(); t0 = time.monotonic()
n = 10**8
while time.monotonic() - t0 < 0.01: pass
t0 = time.monotonic(); s = sum(range(4 * 10**8)); dur = time.monotonic() - t0   # one C call, GIL held
wd.beat(); time.sleep(0.5)
say(f"{dur:.1f} s block holding the GIL (one C call):", events)
wd.stop()
