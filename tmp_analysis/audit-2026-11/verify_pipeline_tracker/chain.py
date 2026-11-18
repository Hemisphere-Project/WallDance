"""Full post-YOLO chain without GPU: FrameProcessor(model=None).replay_gpu_cached on fake detections."""
from common import *
from core.pipeline import FrameProcessor, ProcessingSettings
from core.osc_output import OSCSender


class Rec:
    def __init__(self):
        self.msgs = []
        self.frame = -1

    def send_message(self, address, args):
        self.msgs.append((self.frame, address, list(args) if isinstance(args, (list, tuple)) else [args]))


def mk_proc(osc=True, **kw):
    s = ProcessingSettings(confidence=0.15, imgsz=1280, use_fp16=False, enhance_enabled=False,
                           enhance_lite=False, enhance_force=False, person_height_px=int(H),
                           use_gpu_path=False, use_ir_belt=False)
    for k, v in kw.items():
        setattr(s, k, v)
    p = FrameProcessor(model=None, settings=s)
    p.tracker.logger.start_session(tempfile.mkdtemp())
    p.tracker.reset()
    clock = {"f": 0}
    p.set_output_clock(lambda: clock["f"] / 20.0)
    rec = None
    if osc:
        o = OSCSender.__new__(OSCSender)
        o.enabled, o.ip, o.port = True, "none", 0
        o.send_errors, o.alert_interval_s, o._errors_since_report = 0, 1e9, 0
        o._last_report, o._pending_alert = None, None
        rec = Rec()
        o.client = rec
        p.attach_osc(o)
    return p, clock, rec


def space(ph=H):
    return dict(person_height=int(ph), scale=1.0, pad_x=0.0, pad_y=0.0, roi_x=0, roi_y=0, frame_width=1280)


def step(p, clock, rec, dets, f, sp=None):
    clock["f"] = f
    if rec is not None:
        rec.frame = f
    return p.replay_gpu_cached(dets, sp or space(), None, 1280, 1280, f, {})
