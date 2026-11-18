import json, glob, os, sys
sys.path.insert(0,'application/src')
from core import config_schema
five = ("tracker_swap_correctors","crossval_skel_min_kpts","crossval_skel_min_conf","crossval_motion_min_ratio","dial_b_relevant")
nfiles=0; hits=[]
for pd in sorted(glob.glob('projects/*/')):
    files=[p for p in glob.glob(pd+'*.json') if not os.path.basename(p).startswith('_')]
    nfiles+=len(files)
    for f in files:
        try: c=json.load(open(f))
        except Exception: continue
        txt=json.dumps(c)
        for k in five:
            if '"%s"'%k in txt: hits.append((f,k))
    if not files: 
        print(pd,'no configs'); continue
    head=sorted(files,key=lambda p:(os.path.getmtime(p),p))[-1]
    c=json.load(open(head)); flat=config_schema.flatten(c)
    print(pd, len(files), os.path.basename(head), flat.get('yolo_imgsz'), flat.get('confidence'), flat.get('tracker_intermittent_confirm'), flat.get('tracking_mode'), 'exp', flat.get('ids_exposure_us'), 'h',flat.get('person_height_px'), 'slots', flat.get('identity_slots_enabled'), 'maxage', flat.get('tracker_max_age'))
print('files', nfiles, 'five-key hits', hits)
