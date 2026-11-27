#!/usr/bin/env python3
"""A/B the IDS camera stalls against the sensor crop, without the app.

The 2026-10-06/07 laptop logs: 91 stalls (1651-1715 ms, every buffer queued) in
~74 min of live acquisition at the landscape crops (1776x1300/1304, 1824x1264),
none in ~28 min at 1488x1528 -- same exposure (10-25 ms), gain and processing
load. This alternates crops (ABAB...) on the bare camera and counts stalls with
the camera's own frame id across each one (ids_camera._track_frame):
"camera_silent" (id +1) vs "lost_in_transfer" (id +N), plus the stream counters.

Run it on the laptop with the app CLOSED (the camera is exclusive), e.g.::

    wdremote --slot dev py extra/ids_stall_ab.py -- --minutes 5 --reps 2
    wdremote --slot dev py extra/ids_stall_ab.py -- --geom 0.974 --geom 1.37 \\
        --geom 1.37@1600000 --gpu --minutes 5

or from the slot's own deployed copy::

    wdremote --slot dev run -- python ..\\extra\\ids_stall_ab.py --minutes 5

``--geom RATIO[@PIXELS]`` = the app's crop model (W/H ratio, pixel budget; default
budget = the app's IDS_CROP_PIXELS). ``--gpu`` reads frames through read_gpu() (the
app's pinned upload path) instead of read(). Results: stdout + stall_ab.json in
$WD_REMOTE_OUT (or --out). Per run: the stalls (a hole still open when the run ends
counts too, kind "open_at_end"), the camera's frame-id gaps / skipped ids and
incomplete buffers (get_stream_diagnostics deltas) and the GenTL counter deltas.

**Conditions (U2, 2026-11-26).** ``--cond NAME:key=val,key=val`` (repeatable) names one
condition of the alternation; the keys change ONE thing at a time from the global
options: ``geom=RATIO[@PIXELS]`` or ``roi=WxH[+X+Y]`` (an exact sensor crop, centred
when no offset is given), ``pf=Mono12g24IDS`` (pixel format), ``fps=F``, ``exp=US``,
``gain=DB``, ``tl=MBPS`` (DeviceLinkThroughputLimit), ``buf=N`` (buffers),
``tmo=MS`` (the acquisition loop's WaitForFinishedBuffer timeout, 150-500), ``gpu=0|1``.
The same keys exist as global options (``--roi``, ``--pixel-format``,
``--throughput-mbps``, ``--buffers``, ``--wait-timeout-ms``). Everything is set at
RUNTIME through the IDS peak API: nothing is saved into the camera, and the
throughput limit is put back to the value read at open when the run ends. Each run
also records what the camera says it runs at (crop, offsets, format, frame rate,
exposure, gain, throughput limit, link speed, payload size) and the host's GPU
temperature at its start and end (the U3-34ExXCP exposes no temperature node), and
is appended to ``stall_ab_runs.jsonl`` as soon as it ends. ``--plan-only`` prints
the run order and exits. ``--dump-nodes PATH`` writes, once before the first run's
stream starts, every readable node of the data stream, the transport-layer device and
the camera (read only: what the GenTL producer says about transfer errors).

**What the app does beside the camera (U2c, 2026-11-26).** Four more keys, each one
thing the WallDance app does that the bare tool did not: ``xf=90`` (the app's input
transform: a 90/180/270 turn of every frame IN the acquisition thread, as the app's
``input_rotation``), ``trt=1`` (a TensorRT inference of the show engine ``--engine`` on
each frame read through ``read_gpu()``, in-process, resized on the GPU to
``--engine-hw HxW``: the app's RUN; ``trt=2`` = the same inference on a constant tensor kept on the
GPU, the frame read on the CPU path: the GPU's work without the upload), ``d2h=1`` (a preview-sized GPU->CPU download of each
frame, ``--preview-scale``, uint8: the app's preview), ``cpu=N`` (a second thread that,
20 times a second, allocates a frame-sized BGR array, copies the latest frame into it
and converts it to float N times: the app's unpack/convert traffic), ``mog=S`` (the app's
motion stage on each frame in a worker thread: blur, downscale by S, OpenCV MOG2, opening,
contours, with the app's 4 OpenCV threads, or ``cvt=N``). ``trt`` and ``d2h``
read through ``read_gpu()`` (the pinned upload, i.e. ``gpu=1``). Each run records what
the load did (calls, mean ms, errors) under ``"load"``.

The app code (application/src) is found from ``--repo``, ``$WD_REPO_ROOT``, else the
first ancestor of the cwd or of this file holding application/src/core: an uploaded
copy (``wdremote py``) sits in a scratch dir, so there the cwd (the slot's
application/) decides.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

OPEN_AT_END = "open_at_end"      # stall kind: the hole was still open when the run ended
DIAG_KEYS = ("frame_id_gaps", "frame_ids_skipped", "incomplete")
# What the camera says it runs at, read back after the condition is applied.
READBACK_NODES = ("Width", "Height", "OffsetX", "OffsetY", "PixelFormat", "AcquisitionFrameRate",
                  "ExposureTime", "Gain", "DeviceLinkThroughputLimit", "DeviceLinkCurrentThroughput",
                  "DeviceLinkSpeed", "DeviceLinkAcquisitionFrameRateLimit", "PayloadSize")
COND_KEYS = ("geom", "roi", "pf", "fps", "exp", "gain", "tl", "buf", "tmo", "gpu",
             "xf", "trt", "d2h", "cpu", "mog", "cvt")     # U2c: what the app does beside the camera
# The IDS U3V producer's data-stream registers the app does not read: WHY a frame came incomplete
# (buffer overflow / packet error / packet unavailable / stop / timeout) and the frames it started.
EXTRA_STREAM_NODES = ("IncompleteFramesBufferOverflowReg", "IncompleteFramesPacketErrorReg",
                      "IncompleteFramesPacketUnavailableReg", "IncompleteFramesStopReg",
                      "IncompleteFramesTimeoutReg", "StreamStartedFrameCount", "StreamLostFrameCount")


def find_repo_root(explicit=None) -> Path:
    """The checkout root: ``--repo``, ``$WD_REPO_ROOT``, else the first ancestor of the
    cwd or of this file holding application/src/core (an uploaded copy sits in
    tmp_analysis/remote/<stamp>/, where ``parents[1]`` is not the checkout)."""
    cands = [Path(explicit)] if explicit else []
    if os.environ.get("WD_REPO_ROOT"):
        cands.append(Path(os.environ["WD_REPO_ROOT"]))
    cwd = Path.cwd().resolve()
    here = Path(__file__).resolve().parent
    cands += [cwd, *cwd.parents, here, *here.parents]
    for c in cands:
        if (c / "application" / "src" / "core").is_dir():
            return c.resolve()
    raise SystemExit(f"ids_stall_ab: no application/src/core above {cwd} or {here}; "
                     "pass --repo (or set WD_REPO_ROOT)")


def use_app_code(root: Path) -> None:
    src = str(root / "application" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def parse_geom(text: str, default_pixels: int):
    """"1.37" -> (1.37, default_pixels); "1.37@1600000" -> (1.37, 1600000)."""
    ratio, _, pixels = text.partition("@")
    return float(ratio), int(pixels) if pixels else int(default_pixels)


def parse_roi(text: str):
    """"1776x1296" -> (1776, 1296, None, None); "1776x1296+456+120" -> (1776, 1296, 456, 120)."""
    size, *off = text.split("+")
    w, h = (int(v) for v in size.lower().split("x"))
    if off and len(off) != 2:
        raise ValueError(f"roi {text!r}: WxH or WxH+X+Y")
    return (w, h, int(off[0]), int(off[1])) if off else (w, h, None, None)


def parse_cond(text: str) -> dict:
    """"NAME:key=val,key=val" -> {"name": NAME, key: val, ...} (values as strings)."""
    name, _, body = text.partition(":")
    cond = {"name": name.strip()}
    for part in filter(None, (p.strip() for p in body.split(","))):
        k, _, v = part.partition("=")
        if k not in COND_KEYS:
            raise ValueError(f"condition {name!r}: unknown key {k!r} (keys: {', '.join(COND_KEYS)})")
        cond[k] = v.strip()
    return cond


def resolve_cond(cond: dict, args, default_pixels: int) -> dict:
    """One run's full settings: the condition's keys over the global options."""
    r = {"name": cond["name"],
         "fps": float(cond.get("fps", args.fps)),
         "exp": float(cond.get("exp", args.exposure_us)),
         "gain": float(cond.get("gain", args.gain_db)),
         "pf": cond.get("pf", args.pixel_format),
         "tl": float(cond["tl"]) if "tl" in cond else args.throughput_mbps,
         "buf": int(cond.get("buf", args.buffers)),
         "tmo": float(cond["tmo"]) if "tmo" in cond else args.wait_timeout_ms,
         "gpu": bool(int(cond["gpu"])) if "gpu" in cond else bool(args.gpu),
         "roi": None, "ratio": None, "pixels": None}
    r.update(side_keys(cond))
    roi = cond.get("roi") or (None if "geom" in cond else args.roi)
    if roi:
        w, h, ox, oy = parse_roi(roi)
        r.update(roi=[w, h, ox, oy], ratio=w / h, pixels=w * h)
    else:
        ratio, pixels = parse_geom(cond.get("geom", "1.37"), default_pixels)
        r.update(ratio=ratio, pixels=pixels)
    return r


def conditions(args, default_pixels: int):
    """The alternation's conditions: --cond, else one per --geom, else the two show crops."""
    if args.cond:
        conds = [parse_cond(c) for c in args.cond]
    else:
        conds = [{"name": f"{r}@{p}", "geom": f"{r}@{p}"}
                 for r, p in (parse_geom(g, default_pixels) for g in (args.geom or ["0.974", "1.37"]))]
    names = [c["name"] for c in conds]
    if len(set(names)) != len(names):
        raise SystemExit(f"ids_stall_ab: condition names repeat: {names}")
    return [resolve_cond(c, args, default_pixels) for c in conds]


def summarize(runs):
    """Per condition: minutes, frames, stalls, stalls/min, kinds, gap range."""
    out = {}
    for r in runs:
        g = out.setdefault(r["geom"], {"size": r["size"], "minutes": 0.0, "frames": 0, "runs": 0,
                                       "stalls": 0, "kinds": {}, "gaps_ms": [],
                                       **{k: 0 for k in DIAG_KEYS}})
        g["runs"] += 1
        g["minutes"] += r["seconds"] / 60.0
        g["frames"] += r["frames"]
        g["stalls"] += len(r["stalls"])
        for k in DIAG_KEYS:
            g[k] += int(r.get(k) or 0)
        for s in r["stalls"]:
            g["kinds"][s["kind"]] = g["kinds"].get(s["kind"], 0) + 1
            g["gaps_ms"].append(s["gap_ms"])
    for g in out.values():
        g["per_min"] = round(g["stalls"] / g["minutes"], 2) if g["minutes"] else 0.0
        g["minutes"] = round(g["minutes"], 2)
        gaps = sorted(g.pop("gaps_ms"))
        g["gap_ms_min_max"] = [gaps[0], gaps[-1]] if gaps else None
    return out


# --------------------------------------------------------------------------- nodes
def _node_value(nm, name):
    """A node's value (enumeration: its symbolic entry); None when absent/unreadable."""
    try:
        node = nm.FindNode(name)
    except Exception:
        return None
    try:
        return node.CurrentEntry().SymbolicValue()
    except Exception:
        pass
    try:
        v = node.Value()
        return v if isinstance(v, (int, float, bool, str)) else str(v)
    except Exception:
        return None


def readback(cam) -> dict:
    nm = getattr(cam, "_node_map", None)
    if nm is None:
        return {}
    rb = {n: _node_value(nm, n) for n in READBACK_NODES}
    try:        # the sensor's own frame-rate ceiling at this crop/format: its readout time
        rb["AcquisitionFrameRateMax"] = float(nm.FindNode("AcquisitionFrameRate").Maximum())
    except Exception:
        pass
    return rb


def _snap(value, node):
    inc = max(1, int(node.Increment()))
    return max(int(node.Minimum()), min(int(node.Maximum()), (int(value) // inc) * inc))


def apply_condition(cam, c) -> dict:
    """Set at runtime what the app's own open did not: an exact crop, a pixel format, a
    throughput limit. Returns {"restore": {node: value}} for the run's end and "set":
    the nodes written. Never saves a user set. No node map (a fake camera): nothing."""
    nm = getattr(cam, "_node_map", None)
    done = {"set": {}, "restore": {}}
    if nm is None:
        return done
    realloc = False
    if c["pf"] and _node_value(nm, "PixelFormat") != c["pf"]:
        node = nm.FindNode("PixelFormat")
        node.SetCurrentEntry(node.FindEntry(c["pf"]))
        cam.state.pixel_format = c["pf"]
        done["set"]["PixelFormat"] = c["pf"]
        realloc = True
    if c["roi"]:
        w, h, ox, oy = c["roi"]
        ox_n, oy_n = nm.FindNode("OffsetX"), nm.FindNode("OffsetY")
        ox_n.SetValue(ox_n.Minimum())
        oy_n.SetValue(oy_n.Minimum())
        w_n, h_n = nm.FindNode("Width"), nm.FindNode("Height")
        w_n.SetValue(_snap(w, w_n))
        h_n.SetValue(_snap(h, h_n))
        sw, sh = getattr(cam, "_sensor_w", 0) or w_n.Maximum(), getattr(cam, "_sensor_h", 0) or h_n.Maximum()
        want_x = (sw - w_n.Value()) // 2 if ox is None else ox
        want_y = (sh - h_n.Value()) // 2 if oy is None else oy
        ox_n.SetValue(_snap(want_x, ox_n))
        oy_n.SetValue(_snap(want_y, oy_n))
        cam.state.width, cam.state.height = int(w_n.Value()), int(h_n.Value())
        done["set"].update(Width=cam.state.width, Height=cam.state.height,
                           OffsetX=int(ox_n.Value()), OffsetY=int(oy_n.Value()))
        realloc = True
    if realloc:
        # the frame rate's maximum moves with the crop / format: set it again
        try:
            fps_n = nm.FindNode("AcquisitionFrameRate")
            fps_n.SetValue(min(c["fps"], fps_n.Maximum()))
            cam.state.fps = fps_n.Value()
            done["set"]["AcquisitionFrameRate"] = round(cam.state.fps, 3)
        except Exception as e:
            done["set"]["AcquisitionFrameRate_error"] = str(e)[:120]
        cam._allocate_buffers()
    if c["tl"]:
        node = nm.FindNode("DeviceLinkThroughputLimit")
        before = int(node.Value())
        want = int(round(c["tl"] * 1e6))
        if want != before:
            done["restore"]["DeviceLinkThroughputLimit"] = before
            lo, inc = int(node.Minimum()), max(1, int(node.Increment()))     # 10 MB/s + k * 2 MB/s
            node.SetValue(max(lo, min(int(node.Maximum()), lo + round((want - lo) / inc) * inc)))
            done["set"]["DeviceLinkThroughputLimit"] = int(node.Value())
            try:    # the frame rate's ceiling moves with the limit: ask for the condition's rate again
                fps_n = nm.FindNode("AcquisitionFrameRate")
                fps_n.SetValue(min(c["fps"], fps_n.Maximum()))
                cam.state.fps = fps_n.Value()
                done["set"]["AcquisitionFrameRate"] = round(cam.state.fps, 3)
            except Exception as e:
                done["set"]["AcquisitionFrameRate_error"] = str(e)[:120]
    return done


def restore_nodes(cam, restore: dict) -> dict:
    """Put back the nodes apply_condition changed beyond the app's own open."""
    nm = getattr(cam, "_node_map", None)
    out = {}
    if nm is None:
        return out
    for name, value in restore.items():
        try:
            nm.FindNode(name).SetValue(value)
            out[name] = _node_value(nm, name)
        except Exception as e:
            out[name] = f"error: {str(e)[:120]}"
    return out


def dump_nodemap(nm) -> dict:
    """Every readable node of a node map, READ ONLY ({name: value}); categories,
    commands and registers skipped. Never writes a node, never executes."""
    out = {}
    try:
        nodes = nm.Nodes()
    except Exception as e:
        return {"_error": str(e)[:200]}
    for n in nodes:
        try:
            name = n.Name()
        except Exception:
            continue
        v = _node_value(nm, name)
        if v is not None:
            out[name] = v
    return out


def dump_nodes(cam) -> dict:
    """The data-stream, the transport-layer device and the camera node maps of an open
    camera (U2 strand A: what the GenTL producer offers about transfer errors and
    their recovery). Read only."""
    doc = {}
    for key, get in (("stream", lambda: cam._datastream.NodeMaps()[0]),
                     ("tl_device", lambda: cam._device.NodeMaps()[0]),
                     ("remote", lambda: cam._node_map)):
        try:
            doc[key] = dump_nodemap(get())
        except Exception as e:
            doc[key] = {"_error": str(e)[:200]}
    return doc


def read_extra_stream(cam) -> dict:
    """EXTRA_STREAM_NODES of the open data stream ({} without one). Read only, never raises."""
    ds = getattr(cam, "_datastream", None)
    if ds is None:
        return {}
    out = {}
    lock = getattr(cam, "_stream_lock", None)
    try:
        if lock is not None:
            lock.acquire()
        nm = ds.NodeMaps()[0]
        for name in EXTRA_STREAM_NODES:
            try:
                out[name] = int(nm.FindNode(name).Value())
            except Exception:
                pass
    except Exception:
        pass
    finally:
        if lock is not None:
            lock.release()
    return out


def gpu_temp():
    """The laptop GPU's temperature (C) by nvidia-smi; None without one."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20)
        t, u = (int(x) for x in r.stdout.strip().splitlines()[0].split(","))
        return {"gpu_c": t, "gpu_util": u}
    except Exception:
        return None


def start_with_timeout(cam, tmo_ms):
    """start_acquisition(); with ``tmo_ms`` the loop's wait timeout is 5 frame periods of
    ``state.fps`` clamped to [150, 500] ms (ids_camera._acquisition_loop): present a frame
    rate that gives ``tmo_ms`` while the loop starts, then put the real one back."""
    if not tmo_ms:
        return cam.start_acquisition()
    real = cam.state.fps
    cam.state.fps = 5000.0 / max(150.0, min(500.0, float(tmo_ms)))
    try:
        ok = cam.start_acquisition()
        time.sleep(0.5)                   # the loop reads state.fps once, at its start
    finally:
        cam.state.fps = real
    return ok


# --------------------------------------------------------------------------- U2c: the app's work beside the camera
def side_keys(cond: dict) -> dict:
    """The U2c keys of one condition (all off by default): ``xf`` (degrees), ``trt``, ``d2h``
    (booleans), ``cpu`` (conversions per frame). ``trt=1`` / ``d2h`` need the GPU read: they
    force ``gpu``. ``trt=2`` = the same inference on a CONSTANT tensor kept on the GPU, once per frame
    read on the plain CPU path: the GPU's work without the frame's upload."""
    r = {"xf": int(cond.get("xf", 0)), "trt": int(cond.get("trt", 0)),
         "d2h": bool(int(cond.get("d2h", 0))), "cpu": int(cond.get("cpu", 0)),
         "mog": float(cond.get("mog", 0)), "cvt": int(cond.get("cvt", 0))}
    if r["xf"] % 90:
        raise ValueError(f"condition {cond.get('name')!r}: xf={r['xf']} is not a multiple of 90")
    if r["trt"] not in (0, 1, 2):
        raise ValueError(f"condition {cond.get('name')!r}: trt={r['trt']} (0, 1 or 2)")
    if r["trt"] == 1 or r["d2h"]:
        r["gpu"] = True
    return r


def parse_hw(text: str):
    """"1920x1056" -> (1920, 1056): the app's engine shape convention, height x width."""
    h, w = (int(v) for v in str(text).lower().split("x"))
    return h, w


def load_engine(path: str):
    """The show engine through ultralytics (as the app's ModelManager loads it)."""
    from ultralytics import YOLO
    return YOLO(path, task="pose")


class SideLoad:
    """What the app does beside the camera, done by the tool (one condition's ``xf``/``trt``/
    ``d2h``/``cpu``). ``frame(x)`` runs the per-frame part on each frame read (a CPU BGR array
    or, through ``read_gpu``, a (1, 3, H, W) float tensor); ``stop()`` ends the ``cpu`` thread
    and returns the counts. Loads the engine BEFORE the camera's stream starts."""

    def __init__(self, c: dict, args, loader=load_engine):
        self.c = c
        self.info = {"frames": 0, "trt_calls": 0, "trt_ms": 0.0, "d2h_calls": 0, "d2h_bytes": 0,
                     "cpu_loops": 0, "errors": []}
        self._model = None
        self._stop = False
        self._thread = None
        self._src = None
        if c.get("trt"):
            eng = getattr(args, "engine", None)
            if not eng:
                raise SystemExit("ids_stall_ab: trt=1 needs --engine PATH (the show engine)")
            self._hw = parse_hw(getattr(args, "engine_hw", "1920x1056"))
            t0 = time.perf_counter()
            self._model = loader(eng)
            self.info.update(engine=str(eng), engine_hw=list(self._hw))
            self.info["load_s"] = round(time.perf_counter() - t0, 2)
        self._scale = float(getattr(args, "preview_scale", 0.5) or 0.5)
        self._mog = self._mog_pool = self._mog_future = None
        if c.get("mog"):
            import cv2
            from concurrent.futures import ThreadPoolExecutor
            nthr = int(c.get("cvt") or getattr(args, "cv2_threads", 4) or 4)    # the app's CV2_NUM_THREADS
            self._cv2_prev = cv2.getNumThreads()                                  # put back at stop()
            cv2.setNumThreads(nthr)
            self._mog = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=16, detectShadows=True)
            self._mog_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="u2c-mog")
            self.info.update(mog_calls=0, mog_ms=0.0, mog_scale=float(c["mog"]), cv2_threads=cv2.getNumThreads())

    def start(self, cam=None) -> None:
        if self.c.get("xf") and cam is not None:
            from core.input_transform import InputTransform
            cam.input_transform = InputTransform(rotation=int(self.c["xf"]) % 360)
            self.info["xf"] = int(self.c["xf"]) % 360
        if self.c.get("cpu"):
            import threading
            self._thread = threading.Thread(target=self._cpu_loop, name="u2c-cpu", daemon=True)
            self._thread.start()

    def _infer(self, t):
        import torch.nn.functional as F
        if self.c.get("trt") == 2:          # the GPU's work only: a constant tensor already on the GPU
            if getattr(self, "_const", None) is None:
                import torch
                self._const = torch.rand((1, 3) + tuple(self._hw), device="cuda" if torch.cuda.is_available() else "cpu")
            t = self._const
        x = F.interpolate(t, size=self._hw, mode="bilinear", align_corners=False)
        self._model(x, imgsz=list(self._hw), conf=0.25, iou=0.45, half=True, verbose=False)

    def frame(self, x) -> None:
        if x is None:
            return
        self.info["frames"] += 1
        if x is not None and hasattr(x, "shape") and len(x.shape) >= 2:
            self._src = x
        try:
            if self._model is not None:
                t0 = time.perf_counter()
                self._infer(x)
                self.info["trt_ms"] += (time.perf_counter() - t0) * 1000.0
                self.info["trt_calls"] += 1
            if self._mog is not None and hasattr(x, "dtype") and str(x.dtype) == "uint8":
                import numpy as np
                self._mog_wait()
                gray = np.ascontiguousarray(x[:, :, 0]) if x.ndim == 3 else x
                self._mog_future = self._mog_pool.submit(self._mog_feed, gray)
            if self.c.get("d2h"):
                import torch.nn.functional as F
                p = F.interpolate(x, scale_factor=self._scale, mode="bilinear", align_corners=False)
                host = p[0].permute(1, 2, 0).mul(255.0).clamp_(0, 255).byte().contiguous().cpu()
                self.info["d2h_calls"] += 1
                self.info["d2h_bytes"] += int(host.numel())
        except Exception as e:
            if len(self.info["errors"]) < 5:
                self.info["errors"].append(f"{type(e).__name__}: {str(e)[:160]}")

    def _mog_feed(self, gray) -> None:
        """The app's motion stage on one frame (core/motion_detector.py preprocess + feed_preprocessed):
        in the dark (mean < 55) a 5x5 median then a 5x5 Gaussian blur at full size, else a 3x3 blur;
        downscale by S; MOG2 (shadows on); the cleaned mask opened; then contours (its detect)."""
        import cv2
        import numpy as np
        t0 = time.perf_counter()
        if float(np.mean(gray)) < 55:
            g = cv2.GaussianBlur(cv2.medianBlur(gray, 5), (5, 5), 0)
        else:
            g = cv2.GaussianBlur(gray, (3, 3), 0)
        sc = float(self.c["mog"])
        small = cv2.resize(g, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA) if sc < 1.0 else g
        fg = self._mog.apply(small)
        clean = (fg == 255).astype(np.uint8) * 255
        clean = cv2.morphologyEx(clean, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        cv2.findContours(clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        self.info["mog_ms"] += (time.perf_counter() - t0) * 1000.0
        self.info["mog_calls"] += 1

    def _mog_wait(self) -> None:
        fut, self._mog_future = self._mog_future, None
        if fut is not None:
            try:
                fut.result()
            except Exception as e:
                if len(self.info["errors"]) < 5:
                    self.info["errors"].append(f"mog {type(e).__name__}: {str(e)[:160]}")

    def _cpu_loop(self) -> None:
        import numpy as np
        n = int(self.c["cpu"])
        while not self._stop:
            t0 = time.perf_counter()
            src = self._src
            if src is None or not hasattr(src, "dtype") or str(getattr(src, "dtype", "")) != "uint8":
                src = np.zeros((1260, 1824, 3), np.uint8)      # the app's crop as BGR until a CPU frame comes
            for _ in range(n):
                b = np.empty(src.shape[:2] + (3,), np.uint8)
                b[...] = src[..., :1] if src.ndim == 3 else src[..., None]
                b.astype(np.float32)
            self.info["cpu_loops"] += 1
            time.sleep(max(0.0, 0.05 - (time.perf_counter() - t0)))

    def stop(self) -> dict:
        self._stop = True
        if self._mog_pool is not None:
            self._mog_wait()
            self._mog_pool.shutdown(wait=True)
            import cv2
            cv2.setNumThreads(self._cv2_prev)
            self.info["mog_ms"] = round(self.info["mog_ms"] / max(1, self.info["mog_calls"]), 2)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.info["trt_ms_mean"] = round(self.info.pop("trt_ms") / max(1, self.info["trt_calls"]), 2)
        return dict(self.info)


def run_one(c, args):
    """One run of condition ``c`` (resolve_cond) for ``args.minutes``."""
    from camera.ids_camera import IDSCamera, IDSCameraSettings
    from core.config import IDS_USER_SET
    st = IDSCameraSettings(crop_pixels=c["pixels"], crop_ratio=c["ratio"], target_fps=c["fps"],
                           exposure_auto=False, exposure_us=c["exp"],
                           gain_auto=False, gain_db=c["gain"], newest_only=True,
                           buffer_count=c["buf"],
                           prefer_high_bit_depth=False, user_set=IDS_USER_SET)
    load = SideLoad(c, args)               # U2c: the engine loads before the camera opens
    cam = IDSCamera(st)
    stalls = []
    track = cam._track_frame
    t_start = [None]

    def tracked(*a, **k):
        rec = track(*a, **k)
        if rec is not None:
            rec = dict(rec, extra=read_extra_stream(cam))     # why it came incomplete (cumulative)
            if t_start[0] is not None:
                rec = dict(rec, since_start_s=round(time.perf_counter() - t_start[0], 2))
            stalls.append(rec)
        return rec

    cam._track_frame = tracked              # the acquisition thread looks it up per frame
    if not cam.open(None):
        cam.close()
        raise SystemExit("could not open the IDS camera (is the app running?)")
    applied = {"set": {}, "restore": {}}
    restored = {}
    try:
        applied = apply_condition(cam, c)
        if getattr(args, "dump_nodes", None):      # before the stream starts: no USB reads during it
            Path(args.dump_nodes).write_text(json.dumps(dump_nodes(cam), indent=1, default=str))
            args.dump_nodes = None                  # once, at the first run
        if not start_with_timeout(cam, c["tmo"]):
            raise SystemExit("could not start the IDS acquisition")
        size = f"{cam.state.width}x{cam.state.height}"
        rb = readback(cam)
        temps = [gpu_temp()]
        print(f"[ab] {c['name']} -> {size}, {args.minutes} min, "
              f"exp {c['exp']:.0f} us, gain {c['gain']} dB, {'read_gpu' if c['gpu'] else 'read'}"
              f"{', set ' + json.dumps(applied['set']) if applied['set'] else ''}")
        before = cam.get_stream_diagnostics()
        extra0 = read_extra_stream(cam)
        t0 = time.perf_counter()
        t_start[0] = t0
        wall0 = time.time()
        f0 = cam.state.frame_count
        read = cam.read_gpu if c["gpu"] else cam.read
        load.start(cam)
        while time.perf_counter() - t0 < args.minutes * 60.0:
            load.frame(read()[1])
            time.sleep(0.005)
        t_end = time.perf_counter()
        seconds = t_end - t0
        frames = cam.state.frame_count - f0
        after = cam.get_stream_diagnostics()
        extra1 = read_extra_stream(cam)
        cam.stop_acquisition()                  # the acquisition thread is done: no race below
        hole = open_stall(cam, t_end)
        if hole is not None:
            stalls.append(dict(hole, since_start_s=round(seconds, 2)))
        temps.append(gpu_temp())
    finally:
        load_info = load.stop()
        restored = restore_nodes(cam, applied.get("restore", {}))
        cam.close()
    counters = {k: v - before["counters"].get(k, 0) for k, v in after["counters"].items()
                if v != before["counters"].get(k, 0)}
    rec = {"geom": c["name"], "cond": {k: v for k, v in c.items() if k != "name"},
           "size": size, "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(wall0)),
           "seconds": round(seconds, 1),
           "frames": frames, "fps": round(frames / seconds, 2) if seconds else 0.0,
           "stalls": stalls, "stream_counters_delta": counters,
           "stream_extra_delta": {k: v - extra0.get(k, 0) for k, v in extra1.items() if v != extra0.get(k, 0)},
           "readback": rb, "set": applied.get("set", {}), "restored": restored, "temps": temps,
           "load": load_info,
           **{k: int(after.get(k) or 0) - int(before.get(k) or 0) for k in DIAG_KEYS}}
    print(f"[ab]   {frames} frames ({rec['fps']} fps), {len(stalls)} stall(s) "
          f"{[(s['gap_ms'], s['kind']) for s in stalls]}, frame-id gaps "
          f"{rec['frame_id_gaps']} ({rec['frame_ids_skipped']} ids skipped), incomplete "
          f"{rec['incomplete']}, counters {counters}")
    return rec


def open_stall(cam, t_end: float):
    """The hole still open when the run ended (no frame for longer than the
    camera's stall threshold), as a stall record; else None. ``_track_frame``
    records a stall only when the NEXT frame arrives, so a hole that outlived the
    run was lost (a 23.6 s one read "stalls=0")."""
    last = float(getattr(cam, "_last_acq_frame_time", 0.0) or 0.0)
    threshold = float(getattr(cam, "_stall_threshold_s", 0.4))
    gap = t_end - last
    if last <= 0.0 or gap <= threshold:
        return None
    return {"n": None, "t": round(time.time(), 3), "gap_ms": int(round(gap * 1000)),
            "id_delta": None, "cam_dt_ms": None, "kind": OPEN_AT_END,
            "open_at_end": True}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--geom", action="append",
                    help="RATIO[@PIXELS], repeatable (default: 0.974 and 1.37, the show crops)")
    ap.add_argument("--cond", action="append",
                    help="NAME:key=val,... repeatable; keys " + ", ".join(COND_KEYS))
    ap.add_argument("--minutes", type=float, default=5.0, help="per run")
    ap.add_argument("--reps", type=int, default=2, help="ABAB... rounds")
    ap.add_argument("--fps", type=float, default=20.0)
    ap.add_argument("--exposure-us", type=float, default=25000.0)
    ap.add_argument("--gain-db", type=float, default=30.0)
    ap.add_argument("--gpu", action="store_true", help="read through read_gpu() (pinned upload)")
    ap.add_argument("--roi", default=None, help="WxH[+X+Y]: an exact sensor crop for every condition")
    ap.add_argument("--pixel-format", default=None, help="e.g. Mono12g24IDS (default: the app's choice)")
    ap.add_argument("--throughput-mbps", type=float, default=None,
                    help="DeviceLinkThroughputLimit in MB/s for the run (put back at its end)")
    ap.add_argument("--buffers", type=int, default=16)
    ap.add_argument("--wait-timeout-ms", type=float, default=None,
                    help="the acquisition loop's buffer wait timeout (150-500 ms)")
    ap.add_argument("--dump-nodes", default=None,
                    help="JSON path: at the first run, every readable node of the data stream, "
                         "the transport-layer device and the camera (read only)")
    ap.add_argument("--engine", default=None, help="U2c trt=1: the show engine (.engine) run on each frame")
    ap.add_argument("--engine-hw", default="1920x1056", help="U2c trt=1: the engine's input, HxW (app convention)")
    ap.add_argument("--preview-scale", type=float, default=0.496, help="U2c d2h=1: the preview's scale")
    ap.add_argument("--cv2-threads", type=int, default=4, help="U2c mog=S: OpenCV threads (the app's CV2_NUM_THREADS)")
    ap.add_argument("--plan-only", action="store_true", help="print the run order and exit")
    ap.add_argument("--out", default=os.environ.get("WD_REMOTE_OUT", "."))
    ap.add_argument("--repo", default=None,
                    help="checkout root holding application/src (default: $WD_REPO_ROOT, "
                         "else found above the cwd / this file)")
    args = ap.parse_args(argv)
    if args.plan_only:
        default_pixels = 2334784
    else:
        use_app_code(find_repo_root(args.repo))
        from core.config import IDS_CROP_PIXELS
        default_pixels = IDS_CROP_PIXELS
    conds = conditions(args, default_pixels)
    plan = [c for _ in range(max(1, args.reps)) for c in conds]
    if args.plan_only:
        print(json.dumps([{k: v for k, v in c.items()} for c in plan], indent=1))
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runs = []
    for c in plan:
        rec = run_one(c, args)
        runs.append(rec)
        with open(out / "stall_ab_runs.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
    summary = summarize(runs)
    print(json.dumps(summary, indent=1))
    (out / "stall_ab.json").write_text(json.dumps({"args": vars(args), "summary": summary,
                                                   "runs": runs}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
