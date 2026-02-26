#!/usr/bin/env python3
"""
OSC Replay — Replays a recorded .osc file, sending packets via UDP.
Zero dependencies beyond Python 3 standard library.

Usage:
    python osc_replay.py [--port PORT] [--host HOST] [--loop] [--speed FACTOR] FILE

Defaults:
    --port   9000
    --host   127.0.0.1
    --loop   off (play once)
    --speed  1.0
"""

import argparse
import socket
import struct
import time
import sys
import os

HEADER_MAGIC = b'OSCREC01'


def osc_address(data):
    """Extract the OSC address pattern from a raw packet (for display)."""
    try:
        end = data.index(b'\x00')
        return data[:end].decode('ascii')
    except (ValueError, UnicodeDecodeError):
        return '???'


def load_recording(filepath):
    """Load packets from a .osc recording file. Returns list of (timestamp, data)."""
    with open(filepath, 'rb') as f:
        magic = f.read(8)
        if magic != HEADER_MAGIC:
            print(f'[ERROR] Invalid file header. Expected OSCREC01, got {magic!r}')
            sys.exit(1)

        packets = []
        while True:
            ts_bytes = f.read(8)
            if len(ts_bytes) < 8:
                break
            length_bytes = f.read(4)
            if len(length_bytes) < 4:
                break
            ts = struct.unpack('<d', ts_bytes)[0]
            length = struct.unpack('<I', length_bytes)[0]
            data = f.read(length)
            if len(data) < length:
                break
            packets.append((ts, data))

    return packets


def main():
    parser = argparse.ArgumentParser(description='Replay a recorded OSC file.')
    parser.add_argument('file', type=str,
                        help='Path to .osc recording file')
    parser.add_argument('--port', type=int, default=9000,
                        help='Target UDP port (default: 9000)')
    parser.add_argument('--host', type=str, default='127.0.0.1',
                        help='Target host (default: 127.0.0.1)')
    parser.add_argument('--loop', action='store_true',
                        help='Loop playback indefinitely')
    parser.add_argument('--speed', type=float, default=1.0,
                        help='Playback speed factor (default: 1.0, 2.0 = twice as fast)')
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(f'[ERROR] File not found: {args.file}')
        sys.exit(1)

    packets = load_recording(args.file)
    if not packets:
        print('[ERROR] No packets in recording.')
        sys.exit(1)

    duration = packets[-1][0]
    print(f'[OSC Replay] Loaded {len(packets)} packets ({duration:.2f}s) from {args.file}')
    print(f'[OSC Replay] Sending to {args.host}:{args.port}  speed={args.speed}x  loop={args.loop}')
    print(f'[OSC Replay] Press Ctrl+C to stop.\n')

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (args.host, args.port)

    loop_count = 0
    try:
        while True:
            loop_count += 1
            if args.loop:
                print(f'--- Loop #{loop_count} ---')

            start = time.monotonic()
            for i, (ts, data) in enumerate(packets):
                # Wait until correct relative time
                adjusted_ts = ts / args.speed
                target_time = start + adjusted_ts
                now = time.monotonic()
                if target_time > now:
                    time.sleep(target_time - now)

                sock.sendto(data, target)
                addr_str = osc_address(data)
                print(f'  [{ts:8.3f}s] #{i+1:<5d}  {addr_str}  ({len(data)} bytes)')

            if not args.loop:
                break

    except KeyboardInterrupt:
        print(f'\n[OSC Replay] Stopped after {loop_count} loop(s).')

    finally:
        sock.close()

    print('[OSC Replay] Done.')


if __name__ == '__main__':
    main()
