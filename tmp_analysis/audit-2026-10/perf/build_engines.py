"""Rebuild TRT engines the app's way (extra/build_engines.sh: model.export(format='engine',
imgsz=N, half=True, device=0)), exporting from a scratch copy of the .pt so no
other agent sees a half-written models/<base>.engine, then move to models/<base>_<N>.engine.

TRT 11 is strongly typed: ultralytics bakes FP16 into the ONNX via nvidia-modelopt AutoCast,
which is NOT installed in this venv (the stock export FAILS). Scratch-only workaround:
monkeypatch modelopt_quantize_onnx with onnxruntime.transformers.float16 (keep_io_types=True,
same FP32-I/O contract as AutoCast's keep_io_types=True)."""
import shutil, sys, time, json
from pathlib import Path
import onnx
import ultralytics.utils.export.engine as E

def _ort_fp16(onnx_file, quantize, dataset, shape, dynamic, prefix):
    assert quantize == 16, quantize
    from onnxruntime.transformers import float16
    m = onnx.load(onnx_file)
    m16 = float16.convert_float_to_float16(m, keep_io_types=True)
    out = str(Path(onnx_file).with_suffix(".fp16.onnx"))
    onnx.save(m16, out)
    print(f"{prefix} [scratch] FP16 via onnxruntime.transformers.float16 -> {out}", flush=True)
    return out
E.modelopt_quantize_onnx = _ort_fp16

from ultralytics import YOLO
SCR = Path(__file__).parent / "engines"
MODELS = Path("/data/WallDance/models")
jobs = [a.split("@") for a in sys.argv[1:]]
log = []
for base, sz in jobs:
    sz = int(sz)
    src = SCR / f"{base}.pt"
    if not src.exists():
        shutil.copy2(MODELS / f"{base}.pt", src)
    t0 = time.time()
    out = YOLO(str(src)).export(format="engine", imgsz=sz, half=True, device=0, verbose=False)
    dt = time.time() - t0
    dst = MODELS / f"{base}_{sz}.engine"
    shutil.move(out, dst)
    rec = {"engine": dst.name, "build_s": round(dt, 1), "bytes": dst.stat().st_size}
    print("BUILT", json.dumps(rec), flush=True)
    log.append(rec)
    (SCR / "build_log.json").write_text(json.dumps(log, indent=1))
