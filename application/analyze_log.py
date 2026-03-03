"""Quick log analyzer for tracking_events.jsonl"""
import json, sys

lines = open('tracking_events.jsonl').readlines()

# Find last RESET
last_reset = 0
for i, l in enumerate(lines):
    if 'RESET' in l:
        last_reset = i
print(f'Last reset at line {last_reset}')

session = lines[last_reset:]

start_frame = int(sys.argv[1]) if len(sys.argv) > 1 else 300
end_frame = int(sys.argv[2]) if len(sys.argv) > 2 else 9999

for l in session:
    try:
        e = json.loads(l)
    except:
        continue
    
    ev = e.get('event', '')
    f = e.get('frame', 0)
    
    if f < start_frame or f > end_frame:
        continue
    
    if ev == 'FRAME_SUMMARY':
        d = e['data']
        ids = []
        for s in d['track_states']:
            tag = f"id{s['id']}(m{s['t_miss']}"
            if s['occluded']:
                tag += ",occ"
            tag += ")"
            ids.append(tag)
        pairs = [f"d{p['det']}->t{p['track_id']}({p['cost']})" for p in d['matched_pairs']]
        print(f"F{f:3d} det={d['n_detections']} trk={d['n_tracks']} dorm={d['n_dormant']} "
              f"ids=[{', '.join(ids)}] matched=[{', '.join(pairs)}]")
    
    elif ev in ('NEW_TRACK', 'DORMANT', 'RESURRECT', 'KILL_SHADOW', 'MAHALANOBIS_GATE'):
        d = e['data']
        if ev == 'MAHALANOBIS_GATE':
            print(f"F{f:3d} {ev}: det{d['det']}->t{d['track_id']} chi2={d['chi2']} dist={d['dist_px']}px")
        elif ev == 'NEW_TRACK':
            print(f"F{f:3d} {ev}: id{d['track_id']} pos={d['position']} min_dist={d['min_dist']} gate={d['gate']} edge={d['is_edge']}")
        elif ev == 'DORMANT':
            print(f"F{f:3d} {ev}: id{d['track_id']} occ={d['was_occluded']} est={d['is_established']} edge={d['edge_exit']}")
        elif ev == 'RESURRECT':
            print(f"F{f:3d} {ev}: id{d['track_id']} age={d['dormant_age']} score={d['score']}")
        elif ev == 'KILL_SHADOW':
            print(f"F{f:3d} {ev}: id={d.get('track_id', d)}")
        else:
            print(f"F{f:3d} {ev}: {d}")
    
    elif ev in ('MATCH_REJECTED', 'FORCE_UPDATE', 'ANTI_MERGE'):
        d = e['data']
        print(f"F{f:3d} {ev}: {d}")
