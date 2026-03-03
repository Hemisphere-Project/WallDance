"""
OSC output for WallDance.
Sends dancer tracking data to OSC receivers (VJ software, lighting, etc.)
"""

from pythonosc import udp_client
from pythonosc.osc_bundle_builder import OscBundleBuilder, IMMEDIATELY
from pythonosc.osc_message_builder import OscMessageBuilder
import numpy as np
from config import OSC_IP, OSC_PORT, OSC_ENABLED


class OSCSender:
    """Send dancer tracking data over OSC."""
    
    def __init__(self, ip=OSC_IP, port=OSC_PORT):
        self.enabled = OSC_ENABLED
        self.ip = ip
        self.port = port
        self.client = None
        
        if self.enabled:
            try:
                self.client = udp_client.SimpleUDPClient(ip, port)
                print(f"OSC: Sending to {ip}:{port}")
            except Exception as e:
                print(f"OSC: Failed to initialize - {e}")
                self.enabled = False
    
    def send_dancer(self, track, frame_width, frame_height):
        """
        Send single dancer data.
        
        Messages sent (id is prepended to each argument list):
        - /walldance/dancer/centroid [id, x, y]
        - /walldance/dancer/bbox [id, x, y, w, h]
        - /walldance/dancer/keypoints [id, x0, y0, c0, x1, y1, c1, ...]
        - /walldance/dancer/velocity [id, vx, vy]
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
        self.client.send_message("/walldance/dancer/centroid", 
                                  [dancer_id, norm_x(centroid_x), norm_y(centroid_y)])
        
        # Bounding box (normalized)
        self.client.send_message("/walldance/dancer/bbox", [
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
        self.client.send_message("/walldance/dancer/velocity",
                                  [dancer_id, norm_x(vel_x), norm_y(vel_y)])
        
        # All keypoints as flat list: [id, x0, y0, c0, x1, y1, c1, ...]
        keypoints_flat = [dancer_id]
        for i in range(17):
            x, y = track.keypoints[i]
            c = float(track.confidence[i])
            keypoints_flat.extend([norm_x(x), norm_y(y), c])
        
        self.client.send_message("/walldance/dancer/keypoints", keypoints_flat)
    
    def send_count(self, count, track_ids):
        """Send total dancer count followed by active track IDs."""
        if not self.enabled or not self.client:
            return
        
        self.client.send_message("/walldance/count", [count] + list(track_ids))
    
    def send_frame(self, tracks, frame_width, frame_height):
        """Send all tracking data for current frame."""
        if not self.enabled:
            return
        
        track_ids = [t.track_id for t in tracks]
        self.send_count(len(tracks), track_ids)
        
        for track in tracks:
            self.send_dancer(track, frame_width, frame_height)
    
    def send_clear(self):
        """Send clear message (e.g., when resetting)."""
        if not self.enabled or not self.client:
            return
        
        self.client.send_message("/walldance/clear", [1])
