import sys
sys.dont_write_bytecode = True
from core.calibration import ExposureServo

def run(label, exp_us, gain_db, brightness_fn, blur=25.0, clip=0.0):
    s = ExposureServo(exp_us, gain_db, blur_budget_ms=blur)
    cam = {"exposure": exp_us, "gain": gain_db}   # what the camera really has
    cmds = []
    for i in range(400):
        if s.done:
            break
        cmd = s.feed(brightness_fn(cam), clip, "ROI")
        if cmd is not None:
            cam[cmd[0]] = cmd[1]
            cmds.append((cmd[0], round(cmd[1], 1)))
    r = s.result()
    print(f"--- {label}")
    print(f"  camera started at {exp_us/1000:.1f} ms / {gain_db:.1f} dB, blur budget {blur} ms, cap {s.exposure_cap_us/1000:.1f} ms")
    print(f"  commands issued: {cmds}")
    print(f"  camera REALLY at: {cam['exposure']/1000:.1f} ms / {cam['gain']:.1f} dB")
    print(f"  report: {r.summary_line()}")

# 1. in band at the start: no command at all
run("in band, camera above the budget (51.6 ms, the corpus hangar value)", 51600.0, 10.0, lambda cam: 70.0)
# 2. dark scene: brightness ~ linear in exposure x 10^(gain/20); scene needs more light
def dark(cam):
    return min(255.0, 8.0 * (cam["exposure"] / 51600.0) * 10 ** (cam["gain"] / 20.0) / 10 ** (10.0 / 20.0))
run("dark scene (median 8 at 51.6 ms / 10 dB)", 51600.0, 10.0, dark)
# 3. Calib2 lowered the blur budget to 12 ms, camera at 25 ms, in band
run("blur budget lowered to 12 ms by Calib2, camera at 25 ms, in band", 25000.0, 6.0, lambda cam: 66.0, blur=12.0)
# 4. control: camera below the budget, in band
run("control: camera at 20 ms, in band", 20000.0, 6.0, lambda cam: 66.0)
