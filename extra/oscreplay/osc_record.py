#!/usr/bin/env python3
"""
OSC Recorder — Captures incoming OSC (UDP) packets with timestamps.
Zero dependencies beyond Python 3 standard library.

Usage:
    python osc_record.py [--port PORT] [--output FILE]

Defaults:
    --port    9000
    --output  recording.osc
"""

import argparse
import socket
import struct
import time
import sys
import os

# Binary file format per record:
#   8 bytes  float64  relative timestamp (seconds since start)
#   4 bytes  uint32   packet length
#   N bytes  raw      OSC packet data

HEADER_MAGIC = b'OSCREC01'  # 8-byte file header for validation


def osc_address(data):
    """Extract the OSC address pattern from a raw packet (for display)."""
    try:
        end = data.index(b'\x00')
        return data[:end].decode('ascii')
    except (ValueError, UnicodeDecodeError):
        return '???'


def main():
    parser = argparse.ArgumentParser(description='Record incoming OSC packets to a file.')
    parser.add_argument('--port', type=int, default=9000,
                        help='UDP port to listen on (default: 9000)')
    parser.add_argument('--output', '-o', type=str, default='recording.osc',
                        help='Output file (default: recording.osc)')
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('0.0.0.0', args.port))
    except OSError as e:
        print(f'[ERROR] Cannot bind to port {args.port}: {e}')
        sys.exit(1)

    print(f'[OSC Record] Listening on UDP port {args.port}')
    print(f'[OSC Record] Recording to: {os.path.abspath(args.output)}')
    print(f'[OSC Record] Press Ctrl+C to stop and save.\n')

    packets = []
    count = 0
    start_time = None

    try:
        while True:
            data, addr = sock.recvfrom(65535)
            now = time.monotonic()
            if start_time is None:
                start_time = now
            rel = now - start_time
            packets.append((rel, data))
            count += 1
            addr_str = osc_address(data)
            print(f'  [{rel:8.3f}s] #{count:<5d}  {addr_str}  ({len(data)} bytes) from {addr[0]}:{addr[1]}')

    except KeyboardInterrupt:
        print(f'\n[OSC Record] Stopped. {count} packet(s) captured.')

    finally:
        sock.close()

    if count == 0:
        print('[OSC Record] Nothing to save.')
        return

    # Write binary file
    with open(args.output, 'wb') as f:
        f.write(HEADER_MAGIC)
        for rel, data in packets:
            f.write(struct.pack('<d', rel))       # 8 bytes: timestamp
            f.write(struct.pack('<I', len(data)))  # 4 bytes: length
            f.write(data)                          # N bytes: packet

    size = os.path.getsize(args.output)
    print(f'[OSC Record] Saved {count} packets to {args.output} ({size} bytes)')


if __name__ == '__main__':
    main()
