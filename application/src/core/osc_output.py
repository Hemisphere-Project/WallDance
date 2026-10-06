"""
OSC output for WallDance.
Sends dancer tracking data to OSC receivers (VJ software, lighting, etc.)

Send failures never stop the show (audit 2026-10 ARCH-6): every datagram goes
through ``_send``, which drops it on ``OSError`` (unreachable network, a
``255.255.255.255`` target without broadcast permission, a full non-blocking
socket buffer, ...), counts it, and raises a console line + a pending operator
alert at most once every ``OSC_SEND_ERROR_ALERT_INTERVAL_S``. The main loop
collects that alert with ``take_alert()``. Datagrams are byte-identical.
"""

import time
from typing import Optional

from pythonosc import udp_client
from pythonosc.osc_bundle_builder import OscBundleBuilder, IMMEDIATELY
from pythonosc.osc_message_builder import OscMessageBuilder
import numpy as np
from core.config import (OSC_IP, OSC_PORT, OSC_ENABLED,
                         OSC_SEND_ERROR_ALERT_INTERVAL_S)


class OSCSender:
    """Send dancer tracking data over OSC."""
    
    def __init__(self, ip=OSC_IP, port=OSC_PORT):
        self.enabled = OSC_ENABLED
        self.ip = ip
        self.port = port
        self.client = None
        # Send-failure accounting (ARCH-6): totals + a rate-limited alert.
        self.send_errors = 0
        self.alert_interval_s = OSC_SEND_ERROR_ALERT_INTERVAL_S
        self._errors_since_report = 0
        self._last_report: Optional[float] = None
        self._pending_alert: Optional[str] = None
        
        if self.enabled:
            try:
                self.client = udp_client.SimpleUDPClient(ip, port)
                print(f"OSC: Sending to {ip}:{port}")
            except Exception as e:
                print(f"OSC: Failed to initialize - {e}")
                self.enabled = False

    # ------------------------------------------------------------------
    # Guarded send (ARCH-6)
    # ------------------------------------------------------------------
    def _send(self, address, args) -> bool:
        """Send one message; on a socket error drop it and keep going."""
        try:
            self.client.send_message(address, args)
            return True
        except OSError as exc:
            self._on_send_error(exc)
            return False

    def _on_send_error(self, exc: OSError) -> None:
        self.send_errors += 1
        self._errors_since_report += 1
        now = time.monotonic()
        if (self._last_report is not None
                and now - self._last_report < self.alert_interval_s):
            return
        msg = (f"OSC send to {self.ip}:{self.port} failing "
               f"({type(exc).__name__}: {exc}) - {self._errors_since_report} "
               f"message(s) dropped; output continues")
        print(f"[OSC] {msg}")
        self._pending_alert = msg
        self._last_report = now
        self._errors_since_report = 0

    def take_alert(self) -> Optional[str]:
        """The pending (rate-limited) send-failure alert, once; else None."""
        msg, self._pending_alert = self._pending_alert, None
        return msg
    
    def send_dancer(self, track, frame_width, frame_height,
                    prefix="/walldance/dancer"):
        """
        Send single dancer data under ``/walldance/dancer`` (the single output
        stream — Track X / OSC_CONTRACT §B).  At L=1 ``track`` is the causal
        report; at L>1 it is the fixed-lag RTS-smoothed report, L frames late.

        Messages sent (id is prepended to each argument list):
        - {prefix}/centroid [id, x, y]
        - {prefix}/bbox [id, x, y, w, h]
        - {prefix}/keypoints [id, x0, y0, c0, x1, y1, c1, ...]
        - {prefix}/velocity [id, vx, vy]
        """
        if not self.enabled or not self.client:
            return

        dancer_id = track.track_id
        
        # Normalize coordinates to 0-1
        def norm_x(x):
            return float(x / frame_width)
        
        def norm_y(y):
            return float(y / frame_height)
        
        # Centroid (normalized) — use smoothed centroid if available
        # (EMA-filtered for jitter-free generative video input),
        # otherwise fall back to bbox center.
        bbox = track.bbox
        if hasattr(track, 'smoothed_centroid') and track.smoothed_centroid is not None:
            centroid_x = float(track.smoothed_centroid[0])
            centroid_y = float(track.smoothed_centroid[1])
        else:
            centroid_x = bbox[0] + bbox[2] / 2
            centroid_y = bbox[1] + bbox[3] / 2
        self._send(f"{prefix}/centroid",
                   [dancer_id, norm_x(centroid_x), norm_y(centroid_y)])

        # Bounding box (normalized)
        self._send(f"{prefix}/bbox", [
            dancer_id,
            norm_x(bbox[0]),
            norm_y(bbox[1]),
            norm_x(bbox[2]),  # width
            norm_y(bbox[3])   # height
        ])
        
        # Velocity (normalized per frame) - use attribute directly
        # Clamp to reasonable range to avoid OSC overflow errors
        vel = track.velocity
        vel_x = float(np.clip(vel[0], -1e6, 1e6))
        vel_y = float(np.clip(vel[1], -1e6, 1e6))
        if not (np.isfinite(vel_x) and np.isfinite(vel_y)):
            vel_x, vel_y = 0.0, 0.0
        self._send(f"{prefix}/velocity",
                   [dancer_id, norm_x(vel_x), norm_y(vel_y)])

        # All keypoints as flat list: [id, x0, y0, c0, x1, y1, c1, ...]
        keypoints_flat = [dancer_id]
        for i in range(17):
            x, y = track.keypoints[i]
            c = float(track.confidence[i])
            keypoints_flat.extend([norm_x(x), norm_y(y), c])

        self._send(f"{prefix}/keypoints", keypoints_flat)

    def send_count(self, count, track_ids, address="/walldance/count"):
        """Send total dancer count followed by active track IDs."""
        if not self.enabled or not self.client:
            return

        self._send(address, [count] + list(track_ids))

    def send_latency_ms(self, latency_ms):
        """Publish the active output latency (Track X / OSC_CONTRACT §B).

        ``/walldance/meta/latency_ms [ms]`` — re-emitted whenever L or fps
        changes so consumers know how far behind real time the single
        /walldance/dancer/* stream runs.  0 ms means L=1 (causal / live)."""
        if not self.enabled or not self.client:
            return

        self._send("/walldance/meta/latency_ms", [float(latency_ms)])

    def send_frame(self, tracks, frame_width, frame_height):
        """Send all tracking data for the current frame on the single
        /walldance/dancer/* stream (Track X / OSC_CONTRACT §B).  ``tracks`` is
        the causal report at L=1 or the fixed-lag RTS report (L frames late) at
        L>1 — the same message shapes either way."""
        if not self.enabled:
            return

        track_ids = [t.track_id for t in tracks]
        self.send_count(len(tracks), track_ids, address="/walldance/count")

        for track in tracks:
            self.send_dancer(track, frame_width, frame_height)

    def send_clear(self):
        """Send clear message (e.g., when resetting)."""
        if not self.enabled or not self.client:
            return
        
        self._send("/walldance/clear", [1])
