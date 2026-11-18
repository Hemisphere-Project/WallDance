"""gui-operator#7: how long the readiness OSC probe holds its caller (the main-loop thread for
CheckReadiness).  REAL: core.ops_monitor.probe_osc_udp / check_osc, config.OPS_OSC_PROBE_TIMEOUT_S.
Loopback UDP only (a silent listener on 127.0.0.1, as TouchDesigner on the same laptop, D17)."""
import socket, time
from core.ops_monitor import probe_osc_udp, check_osc
from core.config import OPS_OSC_PROBE_TIMEOUT_S, OSC_IP, OSC_PORT

print("OPS_OSC_PROBE_TIMEOUT_S =", OPS_OSC_PROBE_TIMEOUT_S, "| default OSC target", OSC_IP, OSC_PORT)
listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
listener.bind(("127.0.0.1", 0))
port = listener.getsockname()[1]
ts = []
for _ in range(5):
    t0 = time.perf_counter()
    res = probe_osc_udp("127.0.0.1", port, OPS_OSC_PROBE_TIMEOUT_S)
    ts.append(time.perf_counter() - t0)
print(f"UDP listener present (never answers, like a media server): {res}")
print("   probe wall time per call (ms):", [round(t * 1000) for t in ts])
listener.close()
t0 = time.perf_counter()
res = probe_osc_udp("127.0.0.1", port, OPS_OSC_PROBE_TIMEOUT_S)
print(f"nothing listening: {res}  in {(time.perf_counter() - t0) * 1000:.1f} ms")
t0 = time.perf_counter()
r = check_osc(enabled=False, ip="127.0.0.1", port=port, timeout_s=OPS_OSC_PROBE_TIMEOUT_S)
print(f"OSC disabled: {r.status} in {(time.perf_counter() - t0) * 1000:.1f} ms")
print("at 20 fps (50 ms period) a 250 ms hold = 5 camera frames not read, no OSC frame sent")
