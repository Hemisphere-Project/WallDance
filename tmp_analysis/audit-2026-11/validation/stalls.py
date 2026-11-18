import json, glob, sys
tot=0; dur=0
for f in sorted(glob.glob('projects/mur30m-0710/recordings/*.camlog.jsonl')):
    rows=[json.loads(l) for l in open(f) if l.strip()]
    span=rows[-1]['t']-rows[0]['t']; dur+=span
    st=[]
    for a,b in zip(rows,rows[1:]):
        dt=b['t']-a['t']; df=b['frames']-a['frames']
        fc = b.get('frame_count',0)-a.get('frame_count',0)
        miss = dt*20.0 - df
        if miss>=10:   # >= 0.5 s of frames missing in this ~1 s sample
            st.append((round(a['t']-rows[0]['t'],1), a['frames'], round(dt,2), df, fc, round(miss/20.0,2), b.get('dropped_frames',0)-a.get('dropped_frames',0)))
    tot+=len(st)
    print(f.split('/')[-1], 'rows',len(rows),'span %.1fs'%span, 'frames',rows[-1]['frames'], 'stalls',len(st))
    for s in st: print('    t=%ss frame=%s dt=%s dframes=%s dframe_count=%s missing~%ss ddropped=%s'%s)
print('total stalls',tot,'in %.1f min'%(dur/60))
