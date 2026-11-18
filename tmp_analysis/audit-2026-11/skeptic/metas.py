import json, glob, os, collections
rows=[]
for p in sorted(glob.glob('projects/*/recordings/*.meta')):
    try: m=json.load(open(p))
    except Exception as e: continue
    prov = m.get('provenance') or m
    cfg = None
    for cand in (m.get('config'), (m.get('provenance') or {}).get('config')):
        if isinstance(cand, dict): cfg=cand
    if cfg is None: continue
    eng = (m.get('provenance') or m).get('engine') or {}
    app = (m.get('provenance') or m).get('app')
    rows.append((p, cfg.get('yolo_imgsz'), cfg.get('confidence'), cfg.get('tracker_intermittent_confirm'), cfg.get('tracking_mode'), cfg.get('model'), eng.get('imgsz'), (app or {}).get('commit') if isinstance(app, dict) else app, m.get('frames'), m.get('fps')))
print(len(rows))
for r in rows: print(r)
allm = glob.glob('projects/*/recordings/*.meta')
print('total metas', len(allm))
