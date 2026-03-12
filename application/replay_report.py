"""Replay regression report generator for tracking_events.jsonl.

Summarizes one recorded playback session, or compares two sessions, using the
structured tracking log emitted by TrackingLogger.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


KEY_EVENTS = [
    "NEW_TRACK",
    "RESURRECT",
    "DORMANT",
    "DORMANT_EXPIRED",
    "FORCE_UPDATE",
    "FALLBACK_UPDATE",
    "MATCH_REJECTED",
    "ANTI_MERGE",
    "MAHALANOBIS_GATE",
    "CASCADE_OCCLUSION_SWAP",
    "MERGE_DIRECTION_SWAP",
    "OCCLUDED",
    "AMBIGUOUS_IGNORED",
    "DUPLICATE_IGNORED",
]


@dataclass
class SessionSlice:
    index: int
    label: str
    entries: list[dict[str, Any]]


@dataclass
class ReplaySummary:
    label: str
    frame_start: int | None
    frame_end: int | None
    total_entries: int
    total_frames: int
    event_counts: Counter
    unique_track_ids: set[int]
    new_track_ids: set[int]
    resurrected_track_ids: set[int]
    dormant_track_ids: set[int]
    max_active_tracks: int
    max_dormant_tracks: int
    max_detections: int
    hotspot_frames: list[tuple[int, list[str]]]


def load_entries(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def split_sessions(entries: list[dict[str, Any]]) -> list[SessionSlice]:
    sessions: list[SessionSlice] = []
    current: list[dict[str, Any]] = []

    for entry in entries:
        event = entry.get("event")
        if event in {"SESSION_START", "RESET"}:
            if current:
                sessions.append(SessionSlice(
                    index=len(sessions),
                    label=f"session-{len(sessions)}",
                    entries=current,
                ))
                current = []
            continue
        current.append(entry)

    if current:
        sessions.append(SessionSlice(
            index=len(sessions),
            label=f"session-{len(sessions)}",
            entries=current,
        ))

    return sessions


def select_session(sessions: list[SessionSlice], selector: str) -> SessionSlice:
    if not sessions:
        raise ValueError("No sessions found in log file")
    if selector == "latest":
        return sessions[-1]
    if selector == "previous":
        if len(sessions) < 2:
            raise ValueError("No previous session available")
        return sessions[-2]
    try:
        index = int(selector)
    except ValueError as exc:
        raise ValueError(f"Invalid session selector: {selector}") from exc
    if index < 0:
        index = len(sessions) + index
    if index < 0 or index >= len(sessions):
        raise ValueError(f"Session index out of range: {selector}")
    return sessions[index]


def filter_entries(entries: list[dict[str, Any]], start: int | None,
                   end: int | None) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        frame = entry.get("frame")
        if frame is None:
            continue
        if start is not None and frame < start:
            continue
        if end is not None and frame > end:
            continue
        filtered.append(entry)
    return filtered


def summarize_session(session: SessionSlice, start: int | None,
                      end: int | None) -> ReplaySummary:
    entries = filter_entries(session.entries, start, end)
    event_counts: Counter = Counter()
    unique_track_ids: set[int] = set()
    new_track_ids: set[int] = set()
    resurrected_track_ids: set[int] = set()
    dormant_track_ids: set[int] = set()
    max_active_tracks = 0
    max_dormant_tracks = 0
    max_detections = 0
    frame_events: dict[int, list[str]] = defaultdict(list)
    frame_numbers: set[int] = set()

    for entry in entries:
        event = entry.get("event", "")
        frame = entry.get("frame")
        data = entry.get("data", {})
        if frame is not None:
            frame_numbers.add(frame)
        event_counts[event] += 1

        track_id = data.get("track_id")
        if isinstance(track_id, int):
            unique_track_ids.add(track_id)

        if event == "NEW_TRACK" and isinstance(track_id, int):
            new_track_ids.add(track_id)
        elif event == "RESURRECT" and isinstance(track_id, int):
            resurrected_track_ids.add(track_id)
        elif event in {"DORMANT", "DORMANT_EXPIRED"} and isinstance(track_id, int):
            dormant_track_ids.add(track_id)

        if event == "FRAME_SUMMARY":
            max_active_tracks = max(max_active_tracks, int(data.get("n_tracks", 0)))
            max_dormant_tracks = max(max_dormant_tracks, int(data.get("n_dormant", 0)))
            max_detections = max(max_detections, int(data.get("n_detections", 0)))
            for state in data.get("track_states", []):
                state_id = state.get("id")
                if isinstance(state_id, int):
                    unique_track_ids.add(state_id)

        if event in KEY_EVENTS and frame is not None:
            frame_events[frame].append(event)

    hotspot_frames = sorted(
        ((frame, events) for frame, events in frame_events.items() if len(events) >= 2),
        key=lambda item: (len(item[1]), item[0]),
        reverse=True,
    )[:10]

    return ReplaySummary(
        label=session.label,
        frame_start=min(frame_numbers) if frame_numbers else None,
        frame_end=max(frame_numbers) if frame_numbers else None,
        total_entries=len(entries),
        total_frames=len(frame_numbers),
        event_counts=event_counts,
        unique_track_ids=unique_track_ids,
        new_track_ids=new_track_ids,
        resurrected_track_ids=resurrected_track_ids,
        dormant_track_ids=dormant_track_ids,
        max_active_tracks=max_active_tracks,
        max_dormant_tracks=max_dormant_tracks,
        max_detections=max_detections,
        hotspot_frames=hotspot_frames,
    )


def format_summary(summary: ReplaySummary) -> str:
    lines = [
        f"Replay summary: {summary.label}",
        f"Frames: {summary.frame_start}..{summary.frame_end} ({summary.total_frames} frames)",
        f"Entries: {summary.total_entries}",
        f"Peak detections/tracks/dormant: {summary.max_detections}/{summary.max_active_tracks}/{summary.max_dormant_tracks}",
        (
            "Track IDs: "
            f"seen={len(summary.unique_track_ids)} "
            f"new={len(summary.new_track_ids)} "
            f"resurrected={len(summary.resurrected_track_ids)} "
            f"dormant={len(summary.dormant_track_ids)}"
        ),
        "",
        "Key events:",
    ]

    for event in KEY_EVENTS:
        lines.append(f"  {event:24s} {summary.event_counts.get(event, 0)}")

    if summary.hotspot_frames:
        lines.extend(["", "Hotspot frames:"])
        for frame, events in summary.hotspot_frames:
            lines.append(f"  F{frame}: {', '.join(events)}")

    return "\n".join(lines)


def format_comparison(base: ReplaySummary, candidate: ReplaySummary) -> str:
    lines = [
        f"Replay comparison: {base.label} -> {candidate.label}",
        "",
        "Metric deltas:",
    ]

    scalar_metrics = [
        ("frames", base.total_frames, candidate.total_frames),
        ("entries", base.total_entries, candidate.total_entries),
        ("peak_detections", base.max_detections, candidate.max_detections),
        ("peak_tracks", base.max_active_tracks, candidate.max_active_tracks),
        ("peak_dormant", base.max_dormant_tracks, candidate.max_dormant_tracks),
        ("unique_ids", len(base.unique_track_ids), len(candidate.unique_track_ids)),
        ("new_ids", len(base.new_track_ids), len(candidate.new_track_ids)),
        ("resurrected_ids", len(base.resurrected_track_ids), len(candidate.resurrected_track_ids)),
    ]
    for label, old_val, new_val in scalar_metrics:
        lines.append(f"  {label:16s} {old_val:4d} -> {new_val:4d} ({new_val - old_val:+d})")

    lines.extend(["", "Key event deltas:"])
    for event in KEY_EVENTS:
        old_val = base.event_counts.get(event, 0)
        new_val = candidate.event_counts.get(event, 0)
        lines.append(f"  {event:24s} {old_val:4d} -> {new_val:4d} ({new_val - old_val:+d})")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        default="tracking_events.jsonl",
        help="Path to tracking JSONL log file",
    )
    parser.add_argument(
        "--session",
        default="latest",
        help="Session selector: latest, previous, or integer index",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BASE", "CANDIDATE"),
        help="Compare two sessions (selectors: latest, previous, or integer index)",
    )
    parser.add_argument("--start-frame", type=int, default=None)
    parser.add_argument("--end-frame", type=int, default=None)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text",
    )
    return parser


def to_json_payload(summary: ReplaySummary) -> dict[str, Any]:
    return {
        "label": summary.label,
        "frame_start": summary.frame_start,
        "frame_end": summary.frame_end,
        "total_entries": summary.total_entries,
        "total_frames": summary.total_frames,
        "event_counts": dict(summary.event_counts),
        "unique_track_ids": sorted(summary.unique_track_ids),
        "new_track_ids": sorted(summary.new_track_ids),
        "resurrected_track_ids": sorted(summary.resurrected_track_ids),
        "dormant_track_ids": sorted(summary.dormant_track_ids),
        "max_active_tracks": summary.max_active_tracks,
        "max_dormant_tracks": summary.max_dormant_tracks,
        "max_detections": summary.max_detections,
        "hotspot_frames": [
            {"frame": frame, "events": events}
            for frame, events in summary.hotspot_frames
        ],
    }


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        parser.error(f"Log file does not exist: {log_path}")

    entries = load_entries(log_path)
    sessions = split_sessions(entries)

    if args.compare:
        base = summarize_session(
            select_session(sessions, args.compare[0]),
            args.start_frame,
            args.end_frame,
        )
        candidate = summarize_session(
            select_session(sessions, args.compare[1]),
            args.start_frame,
            args.end_frame,
        )
        if args.json:
            print(json.dumps({
                "base": to_json_payload(base),
                "candidate": to_json_payload(candidate),
            }, indent=2))
        else:
            print(format_comparison(base, candidate))
        return 0

    summary = summarize_session(
        select_session(sessions, args.session),
        args.start_frame,
        args.end_frame,
    )
    if args.json:
        print(json.dumps(to_json_payload(summary), indent=2))
    else:
        print(format_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())