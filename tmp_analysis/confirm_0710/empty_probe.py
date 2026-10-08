"""What YOLO sees on the empty end of slot 2 (frames 1700-1800, belt lit on the stand) at a given gamma / CLAHE,
conf 0.15, x@1280 (Calibrate's scene window ran at the seeded gamma 1.8 / CLAHE 1.5 and logged height=486px n=67)."""
import json, os, sys
sys.path.insert(0, "/data/WallDance/application/tests")
import replay as R  # noqa
import cv2
os.environ["WD_ENGINE_DIR"] = "/data/WallDance/tmp_analysis/confirm_0710/engines"
out = {}
for g, c in [(1.8, 1.5), (0.8, 1.0)]:
    cfg = R._latest_config("mur30m-0710")
    R.apply_overrides(cfg, ["confidence=0.15", "yolo_imgsz=1280", f"gamma={g}", f"clahe_clip={c}", "tracker_intermittent_confirm=true"])
    proc = R._build_processor(cfg, "yolo11x-pose", 1280, use_gpu_path=True, use_trt=True)
    R._attach_frame_clock(proc, 18.661)
    cap = cv2.VideoCapture("/data/WallDance/projects/mur30m-0710/recordings/slot_2_20261007_200742.avi")
    cap.set(cv2.CAP_PROP_POS_FRAMES, 1700)
    dets = []
    for i in range(100):
        ok, fr = cap.read()
        if not ok:
            break
        tracks, *_ = proc.process(fr, need_preview=False, frame_number=i)
        for d in proc.last_raw_dets:
            dets.append([i, round(d[0], 3), round(d[1]), round(d[2]), round(d[3])])
    out[f"{g}/{c}"] = dets
    print(f"gamma {g} clahe {c}: {len(dets)} raw dets in 100 frames; sample {dets[:8]}", flush=True)
json.dump(out, open("/data/WallDance/tmp_analysis/confirm_0710/empty_probe.json", "w"))
