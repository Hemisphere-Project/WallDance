"""Build a dev37 TRT engine (TRT 11.3, FP16) the way tmp_analysis/audit-2026-10/perf/build_engines.py does,
into tmp_analysis/confirm_0710/engines/ (never models/): python build_engine.py yolo11x-pose 800"""
import json, shutil, sys, time
from pathlib import Path
sys.path.insert(0, "/data/WallDance/application/tests")
import replay as R  # noqa  (CUDA libs bootstrap)
import onnx
import ultralytics.utils.export.engine as E


def _ort_fp16(onnx_file, quantize, dataset, shape, dynamic, prefix):
    assert quantize == 16, quantize
    from onnxruntime.transformers import float16
    m16 = float16.convert_float_to_float16(onnx.load(onnx_file), keep_io_types=True)
    out = str(Path(onnx_file).with_suffix(".fp16.onnx"))
    onnx.save(m16, out)
    return out


E.modelopt_quantize_onnx = _ort_fp16
from ultralytics import YOLO  # noqa: E402

OUT = Path(__file__).resolve().parent / "engines"
base, sz = sys.argv[1], int(sys.argv[2])
scr = OUT / "scratch"
scr.mkdir(parents=True, exist_ok=True)
src = scr / f"{base}.pt"
if not src.exists():
    shutil.copy2(f"/data/WallDance/models/{base}.pt", src)
t0 = time.time()
out = YOLO(str(src)).export(format="engine", imgsz=sz, half=True, device=0, verbose=False)
dst = OUT / f"{base}_{sz}.engine"
shutil.move(out, dst)
print("BUILT", json.dumps({"engine": str(dst), "build_s": round(time.time() - t0, 1), "bytes": dst.stat().st_size}))
