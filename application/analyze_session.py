#!/usr/bin/env python3
"""Analyze a WallDance session directory.

Usage:
    python analyze_session.py [session_dir]       # text report (auto-finds latest)
    python analyze_session.py --json              # JSON report for agent consumption
    python analyze_session.py --list              # list available sessions
    python analyze_session.py --json --context N  # frames of context around issues

Produces a compact report:
  - Executive summary (quick health check)
  - Settings summary (with non-default highlights)
  - Issue reports (user-flagged problems)
  - Track lifecycle (creation, hits, lifespan)
  - Per-frame detection/track timeline
  - Ghost vs real classification
  - Key events around flagged issue frames
  - Swap / gating anomaly analysis
"""

import argparse
import json
import sys
import glob
from collections import Counter, defaultdict
from pathlib import Path


PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projects"


def find_all_sessions():
    """Return all session directories across all projects, newest first."""
    sessions = []
    if not PROJECTS_DIR.is_dir():
        return sessions
    for proj in PROJECTS_DIR.iterdir():
        sd = proj / "sessions"
        if sd.is_dir():
            for s in sd.iterdir():
                if s.is_symlink():
                    continue
                if s.is_dir() and (s / "tracking_events.jsonl").exists():
                    sessions.append(s)
    sessions.sort(key=lambda p: p.name, reverse=True)
    return sessions


def find_latest_session():
    """Find the most recent session directory across all projects."""
    sessions = find_all_sessions()
    return sessions[0] if sessions else None


def stream_parse_events(path):
    """Stream-parse JSONL file, yielding event dicts.

    Memory-efficient: doesn't load entire file into memory.
    """
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_issues(issues_dir):
    """Load issue JSON files from the issues/ subdirectory."""
    issues = []
    for f in sorted(glob.glob(str(issues_dir / "*.json"))):
        with open(f) as fh:
            iss = json.load(fh)
        # Normalise: frame may be top-level or nested in context
        if "frame" not in iss:
            iss["frame"] = iss.get("context", {}).get("frame", 0)
        issues.append(iss)
    return issues


def collect_stats(events_path):
    """Single-pass stream over events, collecting all stats into a dict."""
    event_counts = Counter()
    frame_summaries = {}
    track_creates = {}
    track_last_seen = {}
    track_hits = defaultdict(int)
    track_first_established = {}
    settings = {}
    matches = []
    gate_events = []
    swap_events = []
    dormant_events = []
    resurrect_events = []

    for ev in stream_parse_events(events_path):
        etype = ev.get("event", "")
        frame = ev.get("frame", -1)
        data = ev.get("data", {})
        event_counts[etype] += 1

        if etype == "SESSION_SETTINGS":
            settings = data.get("settings", data)

        elif etype == "FRAME_SUMMARY":
            frame_summaries[frame] = data
            for ts in data.get("track_states", []):
                tid = ts["id"]
                track_last_seen[tid] = frame
                if ts.get("established") and tid not in track_first_established:
                    track_first_established[tid] = frame

        elif etype == "NEW_TRACK":
            tid = data.get("track_id", data.get("id", "?"))
            track_creates[tid] = frame

        elif etype == "MATCH":
            tid = data.get("track_id", "?")
            track_hits[tid] += 1
            matches.append((frame, tid, data.get("cost", 0), data.get("raw_dist", 0)))

        elif etype == "MAHALANOBIS_GATE":
            gate_events.append({
                "frame": frame, "track_id": data.get("track_id"),
                "chi2": data.get("chi2", 0), "dist_px": data.get("dist_px", 0),
                "gate": data.get("gate", 0),
            })

        elif etype in ("CASCADE_OCCLUSION_SWAP", "MERGE_DIRECTION_SWAP", "TWO_OPT_SWAP"):
            swap_events.append({"frame": frame, "type": etype, "data": data})

        elif etype == "DORMANT":
            dormant_events.append({"frame": frame, "data": data})

        elif etype == "RESURRECT":
            resurrect_events.append({"frame": frame, "data": data})

    return {
        "event_counts": event_counts,
        "frame_summaries": frame_summaries,
        "track_creates": track_creates,
        "track_last_seen": track_last_seen,
        "track_hits": track_hits,
        "track_first_established": track_first_established,
        "settings": settings,
        "matches": matches,
        "gate_events": gate_events,
        "swap_events": swap_events,
        "dormant_events": dormant_events,
        "resurrect_events": resurrect_events,
    }


def classify_tracks(stats):
    """Classify tracks into real / marginal / ghost."""
    tracks_info = []
    for tid, create_frame in sorted(stats["track_creates"].items()):
        last = stats["track_last_seen"].get(tid, create_frame)
        hits = stats["track_hits"].get(tid, 0)
        lifespan = last - create_frame + 1
        est_frame = stats["track_first_established"].get(tid, None)
        tracks_info.append({
            "id": tid, "created": create_frame, "last_seen": last,
            "lifespan": lifespan, "hits": hits, "established_at": est_frame,
        })

    real = [t for t in tracks_info if t["hits"] >= 20]
    marginal = [t for t in tracks_info if 5 <= t["hits"] < 20]
    ghosts = [t for t in tracks_info if t["hits"] < 5]
    return {"real": real, "marginal": marginal, "ghost": ghosts, "all": tracks_info}


def build_executive_summary(stats, tracks, issues, session_meta):
    """One-paragraph health assessment."""
    fs = stats["frame_summaries"]
    total_frames = len(fs)
    n_real = len(tracks["real"])
    n_ghost = len(tracks["ghost"])
    n_swaps = len(stats["swap_events"])
    n_issues = len(issues)
    n_gates = len(stats["gate_events"])

    dets = [d.get("n_detections", 0) for d in fs.values()]
    avg_det = sum(dets) / len(dets) if dets else 0
    zero_det = dets.count(0) if dets else 0

    health = "GOOD"
    problems = []
    if n_swaps > 0:
        problems.append(f"{n_swaps} swap(s)")
        health = "WARNING"
    if n_ghost > n_real * 2:
        problems.append(f"high ghost ratio ({n_ghost} ghosts vs {n_real} real)")
        health = "WARNING"
    if zero_det > total_frames * 0.1:
        problems.append(f"{zero_det} zero-detection frames ({zero_det*100//max(total_frames,1)}%)")
        health = "WARNING"
    if n_issues > 5:
        problems.append(f"{n_issues} user-flagged issues")
        health = "NEEDS_REVIEW"
    if n_swaps > 5 or (n_issues > 10):
        health = "POOR"

    return {
        "health": health,
        "total_frames": total_frames,
        "real_tracks": n_real,
        "ghost_tracks": n_ghost,
        "swap_count": n_swaps,
        "gate_rejections": n_gates,
        "issue_count": n_issues,
        "avg_detections": round(avg_det, 1),
        "zero_detection_frames": zero_det,
        "problems": problems,
        "model": session_meta.get("model", "?"),
        "imgsz": session_meta.get("imgsz", "?"),
        "slot": session_meta.get("slot", "?"),
        "project": session_meta.get("project", "?"),
    }


def analyze(session_dir, context_frames=3, output_json=False):
    session_dir = Path(session_dir)
    events_path = session_dir / "tracking_events.jsonl"
    issues_dir = session_dir / "issues"
    session_json = session_dir / "session.json"

    if not events_path.exists():
        msg = f"ERROR: {events_path} not found"
        if output_json:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return

    session_meta = {}
    if session_json.exists():
        with open(session_json) as fh:
            session_meta = json.load(fh)

    stats = collect_stats(events_path)
    issues = load_issues(issues_dir) if issues_dir.is_dir() else []
    tracks = classify_tracks(stats)
    summary = build_executive_summary(stats, tracks, issues, session_meta)

    if output_json:
        _emit_json_report(session_dir, session_meta, stats, tracks, issues, summary, context_frames)
    else:
        _emit_text_report(session_dir, session_meta, stats, tracks, issues, summary, context_frames)


def _emit_json_report(session_dir, session_meta, stats, tracks, issues, summary, context_frames):
    """Machine-readable JSON report for agent consumption."""
    fs = stats["frame_summaries"]

    # Build issue detail with surrounding context
    issue_details = []
    for iss in issues:
        f = iss.get("frame", 0)
        ctx_frames = {}
        for ff in range(max(0, f - context_frames), f + context_frames + 1):
            if ff in fs:
                fdata = fs[ff]
                ctx_frames[str(ff)] = {
                    "n_detections": fdata.get("n_detections", 0),
                    "n_tracks": fdata.get("n_tracks", 0),
                    "established_ids": sorted([s["id"] for s in fdata.get("track_states", []) if s.get("established")]),
                    "all_ids": sorted([s["id"] for s in fdata.get("track_states", [])]),
                    "missing": {f"D{s['id']}": s["t_miss"] for s in fdata.get("track_states", []) if s.get("t_miss", 0) > 0},
                }
        nearby_matches = [(mf, mt, mc, md) for mf, mt, mc, md in stats["matches"] if abs(mf - f) <= context_frames]
        issue_details.append({
            **iss,
            "surrounding_frames": ctx_frames,
            "nearby_matches": [{"frame": mf, "track_id": mt, "cost": mc, "raw_dist": md} for mf, mt, mc, md in nearby_matches],
        })

    # Gate summary
    gate_per_track = Counter(g["track_id"] for g in stats["gate_events"])

    report = {
        "session": session_dir.name,
        "session_path": str(session_dir),
        "session_meta": session_meta,
        "summary": summary,
        "settings": stats["settings"],
        "event_counts": dict(stats["event_counts"].most_common()),
        "tracks": {
            "real": tracks["real"],
            "marginal": tracks["marginal"],
            "ghost": tracks["ghost"],
            "total_created": len(tracks["all"]),
        },
        "issues": issue_details,
        "swaps": stats["swap_events"],
        "gate_summary": {
            "total": len(stats["gate_events"]),
            "top_tracks": gate_per_track.most_common(10),
        },
        "dormant_count": len(stats["dormant_events"]),
        "resurrect_count": len(stats["resurrect_events"]),
    }
    print(json.dumps(report, indent=2, default=str))


def _emit_text_report(session_dir, session_meta, stats, tracks, issues, summary, context_frames):
    """Human-readable text report."""
    fs = stats["frame_summaries"]
    matches = stats["matches"]

    print("=" * 70)
    print(f"SESSION ANALYSIS: {session_dir.name}")
    print(f"  Project: {summary['project']}, Slot: {summary['slot']}")
    print("=" * 70)

    # Executive summary
    print(f"\n--- HEALTH: {summary['health']} ---")
    print(f"  {summary['total_frames']} frames | {summary['real_tracks']} real tracks | "
          f"{summary['ghost_tracks']} ghosts | {summary['swap_count']} swaps | "
          f"{summary['issue_count']} issues")
    if summary["problems"]:
        for p in summary["problems"]:
            print(f"  !! {p}")

    # Settings
    print("\n--- SETTINGS ---")
    if session_meta:
        print(f"  Model: {session_meta.get('model', '?')}, imgsz: {session_meta.get('imgsz', '?')}")
        print(f"  Playback: {session_meta.get('playback_path', '?')}")
    settings = stats["settings"]
    key_settings = [
        "confidence", "tracker_max_age", "tracking_mode", "mog2_scale",
        "person_height_px", "enhance_enabled", "greyscale",
        "roi_enabled", "roi_x", "roi_y", "roi_w", "roi_h",
    ]
    for k in key_settings:
        if k in settings:
            print(f"  {k}: {settings[k]}")
    other = {k: v for k, v in settings.items() if k not in key_settings and k not in ("playback_path", "model", "imgsz")}
    if other:
        print(f"  Other: { {k: v for k, v in sorted(other.items())} }")

    # Issues
    print(f"\n--- USER-FLAGGED ISSUES ({len(issues)}) ---")
    for iss in issues:
        ctx = iss.get("context", {})
        print(f"  F{iss.get('frame','?'):>5} | {iss.get('issue_type','?'):>12} | "
              f"D{iss.get('dancer_id', '-'):>3} | "
              f"IDs={ctx.get('active_dancer_ids', [])} | "
              f"{iss.get('note', '')}")

    # Swap events
    if stats["swap_events"]:
        print(f"\n--- SWAP EVENTS ({len(stats['swap_events'])}) ---")
        for sw in stats["swap_events"]:
            print(f"  F{sw['frame']:>5} {sw['type']}: {sw['data']}")

    # Event counts
    print(f"\n--- EVENT COUNTS ---")
    for etype, cnt in stats["event_counts"].most_common():
        print(f"  {etype:30s}: {cnt:>6}")

    # Frame timeline
    total_frames = len(fs)
    print(f"\n--- FRAME TIMELINE ({total_frames} frames) ---")

    dets_per_frame = [d.get("n_detections", 0) for d in fs.values()]
    trks_per_frame = [d.get("n_tracks", 0) for d in fs.values()]
    if dets_per_frame:
        print(f"  Detections/frame: avg={sum(dets_per_frame)/len(dets_per_frame):.1f}, "
              f"max={max(dets_per_frame)}, zero={dets_per_frame.count(0)}/{total_frames}")
        print(f"  Tracks/frame:     avg={sum(trks_per_frame)/len(trks_per_frame):.1f}, "
              f"max={max(trks_per_frame)}")

    # Sample timeline at key frames
    sample_frames = sorted(set(
        list(range(0, total_frames, max(1, total_frames // 10))) +
        [iss.get("frame", 0) for iss in issues] +
        [max(0, iss.get("frame", 0) - 5) for iss in issues] +
        [min(total_frames - 1, iss.get("frame", 0) + 5) for iss in issues]
    ))
    print(f"\n  {'Frame':>6} {'Dets':>5} {'Trks':>5} {'Est':>4} {'IDs (established)':30}")
    for f in sample_frames:
        if f not in fs:
            continue
        fdata = fs[f]
        states = fdata.get("track_states", [])
        est_ids = sorted([s["id"] for s in states if s.get("established")])
        all_ids = sorted([s["id"] for s in states])
        n_est = len(est_ids)
        print(f"  {f:>6} {fdata.get('n_detections',0):>5} {fdata.get('n_tracks',0):>5} {n_est:>4}  "
              f"est={est_ids}  all={all_ids}")

    # Track lifecycle
    print(f"\n--- TRACK LIFECYCLE ({len(tracks['all'])} tracks created) ---")
    print(f"  REAL (>=20 hits):  {len(tracks['real'])}")
    for t in tracks["real"]:
        est = f"  est@F{t['established_at']}" if t["established_at"] else "  never_est"
        print(f"    D{t['id']:>3}: F{t['created']:>5}-F{t['last_seen']:>5} ({t['lifespan']:>4}fr, {t['hits']:>4}hits){est}")

    print(f"  MARGINAL (5-19):   {len(tracks['marginal'])}")
    for t in tracks["marginal"]:
        est = f"  est@F{t['established_at']}" if t["established_at"] else "  never_est"
        print(f"    D{t['id']:>3}: F{t['created']:>5}-F{t['last_seen']:>5} ({t['lifespan']:>4}fr, {t['hits']:>4}hits){est}")

    print(f"  GHOST (<5 hits):   {len(tracks['ghost'])}")
    for t in tracks["ghost"]:
        print(f"    D{t['id']:>3}: F{t['created']:>5}-F{t['last_seen']:>5} ({t['lifespan']:>4}fr, {t['hits']:>4}hits)")

    # Detail around issue frames
    if issues:
        print(f"\n--- DETAIL AROUND ISSUE FRAMES ---")
        for iss in issues:
            f = iss.get("frame", 0)
            print(f"\n  >> F{f}: {iss.get('note','')} (D{iss.get('dancer_id','-')})")
            for ff in range(max(0, f - context_frames), f + context_frames + 1):
                if ff not in fs:
                    continue
                fdata = fs[ff]
                states = fdata.get("track_states", [])
                est = [s for s in states if s.get("established")]
                marker = " <<<" if ff == f else ""
                print(f"    F{ff:>5}: dets={fdata.get('n_detections',0)}, "
                      f"trks={fdata.get('n_tracks',0)}, "
                      f"est={[s['id'] for s in est]}, "
                      f"miss={{{', '.join('D' + str(s['id']) + ':' + str(s['t_miss']) for s in states if s['t_miss']>0)}}}"
                      f"{marker}")
            nearby_matches = [(mf, mt, mc, md) for mf, mt, mc, md in matches
                              if abs(mf - f) <= context_frames]
            if nearby_matches:
                print(f"    Matches: {[(f'F{mf}:D{mt}(c={mc:.0f},d={md:.0f})') for mf, mt, mc, md in nearby_matches]}")

    # Mahalanobis gating summary
    if stats["gate_events"]:
        gate_per_track = Counter(g["track_id"] for g in stats["gate_events"])
        print(f"\n--- MAHALANOBIS GATE SUMMARY ({len(stats['gate_events'])} total) ---")
        print(f"  Top gated tracks: {gate_per_track.most_common(10)}")

    print(f"\n{'=' * 70}")
    print("END OF ANALYSIS")
    print(f"{'=' * 70}")


def list_sessions():
    """Print all available sessions across projects."""
    sessions = find_all_sessions()
    if not sessions:
        print("No sessions found.")
        return
    print(f"{'Session':40s} {'Project':15s} {'Files':>6}")
    print("-" * 65)
    for s in sessions:
        project = s.parent.parent.name
        n_issues = len(list((s / "issues").glob("*.json"))) if (s / "issues").is_dir() else 0
        has_tracking = (s / "tracking_events.jsonl").exists()
        tag = f"{'T' if has_tracking else '-'} I:{n_issues}"
        print(f"  {s.name:38s} {project:15s} {tag:>6}")


def main():
    parser = argparse.ArgumentParser(description="Analyze a WallDance tracking session")
    parser.add_argument("session_dir", nargs="?", help="Session directory path (auto-finds latest if omitted)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON report")
    parser.add_argument("--list", action="store_true", help="List all available sessions")
    parser.add_argument("--context", type=int, default=3, help="Frames of context around issue frames (default: 3)")
    args = parser.parse_args()

    if args.list:
        list_sessions()
        return

    session_dir = args.session_dir
    if session_dir is None:
        session_dir = find_latest_session()
        if session_dir is None:
            print("No session found. Pass session dir as argument or check projects/*/sessions/.")
            sys.exit(1)
        if not args.json:
            print(f"Auto-selected: {session_dir}")

    analyze(session_dir, context_frames=args.context, output_json=args.json)


if __name__ == "__main__":
    main()
