import json, sys, glob, os
P = str(__import__("pathlib").Path(__file__).resolve().parent / "runs") + "/"
keys = ["process_wall","g_upload","g_enhance","g_letterbox","yolo_call","y_tensor_check_max","y_preprocess","y_inference","y_postprocess","y_origimg_d2h",
        "extract_d2h","dedup","motion_wait","mw_total","mw_gamma_lut","mw_preprocess_blur_resize","mw_noise_welford","mw_feed_pre(diff+mog2+morph)",
        "post_yolo","pc_crossval","pc_blob_detect","pc_cold_gate","pc_tracker_update","pc_finalize","pc_osc_send","logger_flush","g_preview_resize","g_preview_download","app.mog2_cvt"]
tags = sys.argv[1:] or sorted(os.path.basename(f)[:-5] for f in glob.glob(P+"*.json"))
rows=[]
for t in tags:
    try: r=json.load(open(P+t+".json"))
    except Exception: continue
    rows.append((t,r))
print("stage".ljust(30)+"".join(t[:22].rjust(24) for t,_ in rows))
for k in keys:
    line=k.ljust(30)
    for t,r in rows:
        s=r["stages"].get(k)
        line += (f"{s['p50']:7.2f}/{s['p95']:7.2f}/{s['mean']:6.1f}" if s else "-").rjust(24)
    print(line)
print("fps(excl decode)".ljust(30)+"".join(str(r["loop_fps_excl_decode"]).rjust(24) for t,r in rows))
print("gpu util mean".ljust(30)+"".join(str(r["nvidia_smi"].get("util_mean")).rjust(24) for t,r in rows))
print("load before".ljust(30)+"".join(f"{r['load_before'][0]:.1f}".rjust(24) for t,r in rows))
print("decode p50".ljust(30)+"".join(f"{r['decode_ms']['p50']:.1f}".rjust(24) for t,r in rows))
print("n_tracks mean".ljust(30)+"".join(f"{r['stages']['n_tracks']['mean']:.2f}".rjust(24) for t,r in rows))
