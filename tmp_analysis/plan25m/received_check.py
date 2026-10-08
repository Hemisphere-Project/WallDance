#!/usr/bin/env python3
"""Recordings that came another way (a transfer, a disk) vs what the laptop holds.

The operator sends the takes by another route, then connects the laptop. This checks the received files
against the laptop's project folders by CONTENT (size + SHA-256 of the first and last 4 MiB, computed on both
sides; a file renamed or re-encoded on the way is caught), then optionally places the matching ones in the
local project at the laptop's path with the laptop's mtime -- so `wdremote pull` (which skips a file only when
size AND mtime match) fetches only what is still missing: the .meta / camlog / configs / plates / logs.

  python3 tmp_analysis/plan25m/received_check.py --received ~/incoming/2026-10-07 --since 2026-10-07T12:00
  python3 tmp_analysis/plan25m/received_check.py --received ... --since ... --place      # + link/copy in place

Needs the laptop online (wdremote's SSH config).  --project limits to one project folder.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "extra"))
import wdremote as W  # noqa: E402

CHUNK = 4 << 20

SIG_AGENT = r'''
import json, sys, hashlib, fnmatch
from pathlib import Path
MARK = "@@WDREMOTE-JSON@@"
ROOT = Path(sys.argv[1])
CHUNK = 4 << 20

def sig(p, size):
    h = hashlib.sha256(str(size).encode())
    with open(p, "rb") as f:
        if size <= 2 * CHUNK:
            h.update(f.read())
        else:
            h.update(f.read(CHUNK)); f.seek(size - CHUNK); h.update(f.read(CHUNK))
    return h.hexdigest()

def main(op, args):
    out = []
    since = float(args.get("since") or 0)
    for rel in args.get("paths") or ["projects"]:
        base = ROOT / rel
        for f in (base.rglob("*") if base.exists() else []):
            if not f.is_file() or f.is_symlink():
                continue
            st = f.stat()
            if st.st_mtime < since:
                continue
            r = str(f.relative_to(ROOT)).replace("\\", "/")
            row = {"rel": r, "size": st.st_size, "mtime": round(st.st_mtime, 3)}
            if not any(fnmatch.fnmatch(r, g) for g in args.get("nohash") or []):
                row["sig"] = sig(f, st.st_size)
            out.append(row)
    sys.stdout.write("\n" + MARK + "\n" + json.dumps({"files": out}) + "\n" + MARK + "\n")
    sys.stdout.flush()
'''


def sig(p: Path, size: int) -> str:
    h = hashlib.sha256(str(size).encode())
    with open(p, "rb") as f:
        if size <= 2 * CHUNK:
            h.update(f.read())
        else:
            h.update(f.read(CHUNK))
            f.seek(size - CHUNK)
            h.update(f.read(CHUNK))
    return h.hexdigest()


def fmt(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f} {u}" if u != "B" else f"{int(n)} B"
        n /= 1024
    return str(n)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--received", required=True, help="folder with what came another way (searched recursively)")
    ap.add_argument("--since", required=True, help="laptop files modified after this (YYYY-MM-DD[THH:MM], local)")
    ap.add_argument("--project", default=None, help="one project folder (default: every project changed since)")
    ap.add_argument("--place", action="store_true", help="put matched files at the laptop's path, laptop mtime")
    ap.add_argument("--json", default=None, help="write the comparison here")
    ap.add_argument("--local", default=None, metavar="ROOT", help="self-test: treat this local checkout as the laptop")
    a = ap.parse_args()

    since = dt.datetime.fromisoformat(a.since).timestamp()
    if a.local:
        remote = W.load_remote(host="local", root=a.local, os="posix")
        tr = W.LocalTransport(remote)
    else:
        remote = W.load_remote()
        tr = W.SshTransport(remote)
    paths = [f"projects/{a.project}"] if a.project else ["projects"]
    # tracking_events logs are big and only needed for forensics: listed, not hashed
    man = W.run_agent(tr, remote, "manifest", {"paths": paths, "since": since,
                                               "nohash": ["*tracking_events.jsonl"]},
                      timeout=1800, script_src=SIG_AGENT)
    rfiles = man["files"]
    by_sig = {}
    for f in rfiles:
        if f.get("sig"):
            by_sig.setdefault(f["sig"], []).append(f)

    recv = [p for p in Path(a.received).expanduser().rglob("*") if p.is_file()]
    matched, unknown = {}, []
    for p in recv:
        s = sig(p, p.stat().st_size)
        if s in by_sig:
            for f in by_sig[s]:
                matched[f["rel"]] = p
        else:
            unknown.append(p)

    projects = sorted({f["rel"].split("/")[1] for f in rfiles if f["rel"].count("/") >= 2})
    print(f"laptop: {len(rfiles)} file(s) changed since {a.since} in {', '.join(projects) or '-'}")
    rows = []
    for f in sorted(rfiles, key=lambda x: x["rel"]):
        got = matched.get(f["rel"])
        name_twin = next((p for p in recv if p.name == Path(f["rel"]).name), None)
        state = ("received" if got else
                 "DIFFERS (same name, other content: re-encoded or cut?)" if name_twin else "not received")
        rows.append({"rel": f["rel"], "size": f["size"], "state": state,
                     "received_as": str(got or name_twin or "")})
    videos = [r for r in rows if r["rel"].endswith(".avi")]
    for r in videos:
        print(f"  {r['state']:<12} {fmt(r['size']):>9}  {r['rel']}"
              + (f"  <- {Path(r['received_as']).name}" if r["received_as"] else ""))
    rest = [r for r in rows if not r["rel"].endswith(".avi") and r["state"] != "received"]
    print(f"other files to pull from the laptop: {len(rest)} ({fmt(sum(r['size'] for r in rest))}), "
          f"of which tracking_events: {fmt(sum(r['size'] for r in rest if r['rel'].endswith('tracking_events.jsonl')))}")
    if unknown:
        print(f"received but not on the laptop (or changed): {len(unknown)}")
        for p in unknown[:20]:
            print(f"  ? {p}")

    if a.place:
        mt = {f["rel"]: f["mtime"] for f in rfiles}
        for rel, src in matched.items():
            dst = REPO / rel
            if dst.exists() and dst.stat().st_size == src.stat().st_size:
                os.utime(dst, (mt[rel], mt[rel]))
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
            os.utime(dst, (mt[rel], mt[rel]))
        print(f"placed {len(matched)} file(s) under {REPO / 'projects'} with the laptop's mtimes; next:")
        for p in projects:
            print(f"  python3 extra/wdremote.py pull projects/{p} --since {a.since[:10]} "
                  f"--exclude '*tracking_events.jsonl' --dry-run")
    if a.json:
        Path(a.json).write_text(json.dumps({"rows": rows, "unknown": [str(p) for p in unknown]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
