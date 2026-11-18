"""remote-deploy#9 / #11: what wdslot sends to the laptop (fake transport, nothing leaves this process)."""
import base64
import shlex
import sys
import types

sys.path.insert(0, "/data/WallDance/extra")
import wdremote as wr  # noqa: E402
import wdslot as ws  # noqa: E402

remote = wr.Remote(host="user@laptop", root="C:/WallDance/WallDance", os="windows")
sent = []


class FakeTransport:
    def run(self, cmd, timeout=None):
        sent.append(cmd)
        return types.SimpleNamespace(stdout=b"started", stderr=b"", returncode=0)


ws.run_slot_agent = lambda tr, remote, op, args, timeout=600: {"processes": []}   # nothing running


def decode(cmd):
    enc = cmd.split()[-1].strip('"')
    return base64.b64decode(enc).decode("utf-16-le")


def run(slot, via_launcher):
    sent.clear()
    a = types.SimpleNamespace(slot=slot, force=False, args=[], via_launcher=via_launcher)
    ws.cmd_slot_run(FakeTransport(), remote, a)
    return decode(sent[-1])


for label, slot, via in (("slot run --slot live --via-launcher", "live", True),
                         ("slot run --slot live", "live", False),
                         ("slot run --slot dev", "dev", False)):
    ps = run(slot, via)
    print(f"\n## {label}\n{ps}")
    print("  -> has -Settings:", "-Settings" in ps,
          "| AllowStartIfOnBatteries:", "AllowStartIfOnBatteries" in ps,
          "| DontStopIfGoingOnBatteries:", "DontStopIfGoingOnBatteries" in ps,
          "| ExecutionTimeLimit:", ("New-TimeSpan -Days 3" in ps) and "3 days (explicit)" or "not set (Windows default)",
          "| WD_REMOTE_API_PORT set:", "WD_REMOTE_API_PORT" in ps)

print("\n## DEV slot sharing (wdslot.py:48-49, apply())")
print("SHARED_DIRS =", ws.SHARED_DIRS)
p = wr.build_parser().parse_args(["deploy", "HEAD"])
print("deploy default: isolated_projects =", p.isolated_projects)
