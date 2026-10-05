import pickle, sys, numpy as np
from scipy.optimize import linear_sum_assignment
EXT=[9,10,15,16]
for p in sys.argv[1:]:
    d=pickle.load(open(p,'rb')); rows=d['rows']; nfr=d['frames']
    by={}
    for r in rows: by.setdefault(r['id'],{})[r['f']]=r
    sp=[]; flips=0; pairs=0
    gaps=[]; rep=0; skel=0
    for tid,fr in by.items():
        fs=sorted(fr); hs=[fr[f]['bbox'][3] for f in fs if fr[f]['fss']==0]
        if len(hs)<30: 
            rep+=len(fs); skel+=sum(fr[f]['fss']==0 for f in fs); continue
        H=float(np.median(hs)); rep+=len(fs); skel+=sum(fr[f]['fss']==0 for f in fs)
        run=0
        for f in fs:
            if fr[f]['fss']>0: run+=1
            elif run: gaps.append(run); run=0
        if run: gaps.append(run)
        for f in fs:
            a=fr[f]; b=fr.get(f+1)
            if b is None or a['fss'] or b['fss']: continue
            va=a['conf'][EXT]>0.3; vb=b['conf'][EXT]>0.3
            if not (va.all() and vb.all()): continue
            A=a['kpts'][EXT]; B=b['kpts'][EXT]
            C=np.linalg.norm(A[:,None]-B[None],axis=2); r,c=linear_sum_assignment(C)
            sp += list(C[r,c]/H); pairs+=1; flips+=int((c!=np.arange(4)).any())
    sp=np.array(sp); g=np.array(gaps)
    print(p.split('/')[-1], 'frames',nfr,'reported rows',rep,'skeleton-fed',skel, f'({100*skel/max(1,rep):.0f}%)', 'tracks',len(by))
    print('  unlabelled extremity speed /H per frame p50/p90/p99: %.3f %.3f %.3f'%tuple(np.percentile(sp,[50,90,99])), ' frame pairs with an L/R/limb label flip: %.1f%%'%(100*flips/max(1,pairs)))
    if len(g): print('  skeleton-gap runs on reported long tracks: n=%d  p50=%d p90=%d p99=%d max=%d  frames-in-gaps by length: <=5: %.0f%%  6-20: %.0f%%  >20: %.0f%%'%(len(g),*np.percentile(g,[50,90,99]),g.max(),100*g[g<=5].sum()/g.sum(),100*g[(g>5)&(g<=20)].sum()/g.sum(),100*g[g>20].sum()/g.sum()))
