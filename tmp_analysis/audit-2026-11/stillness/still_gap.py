"""How long a single YOLO gap ends a still, dim (not 'strong skeleton') dancer?  Real chain, synthetic inputs."""
import sys
sys.path.insert(0, "/tmp/wd_audit_stillness")
import still_chain as S

for gap in (3, 5, 6, 7, 10, 20):
    yolo = lambda k, g=gap: not (200 <= k < 200 + g)          # one gap at still+10 s
    log, proc = S.run_solo("", yolo, plate_on=False, kconf=0.35, n_valid=10, verbose=False)
    still = [r for r in log if r[0] == "still"]
    rej = sum(int(r[4].get("crossval_rejected", 0)) for r in still)
    none = sum(1 for r in still if not r[2])
    first = next((r[1] for r in still if not r[2]), None)
    print("dim still dancer (10 kpts @0.35), ONE YOLO gap of %2d frames (%.2f s) at still+10 s, no plate: "
          "crossval rejected %3d detections; frames without a point %3d of 400%s" % (
              gap, gap / 20.0, rej, none, "" if first is None else " (from still+%.2f s to the end of the stillness)" % (first / 20.0)))
for gap in (6, 20, 60):
    yolo = lambda k, g=gap: not (200 <= k < 200 + g)
    log, proc = S.run_solo("", yolo, plate_on=False, kconf=0.6, n_valid=17, verbose=False)
    still = [r for r in log if r[0] == "still"]
    holes = sum(1 for r in still if not r[2])
    kept = sum(int(r[4].get("crossval_kept_skeleton", 0)) for r in still)
    print("CLEAR still dancer (17 kpts @0.60), one gap of %2d frames: frames without a point %3d; kept by the strong-skeleton rule %d" % (gap, holes, kept))
