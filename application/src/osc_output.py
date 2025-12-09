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
        
        Messages sent:
        - /walldance/dancer/<id>/centroid [x, y]
        - /walldance/dancer/<id>/bbox [x, y, w, h]
        - /walldance/dancer/<id>/keypoints [x0, y0, c0, x1, y1, c1, ...]
        - /walldance/dancer/<id>/velocity [vx, vy]
        """
        if not self.enabled or not self.client:
            return
        
        dancer_id = track.track_id
        prefix = f"/walldance/dancer/{dancer_id}"
        
        # Normalize coordinates to 0-1
        def norm_x(x):
            return float(x / frame_width)
        
        def norm_y(y):
            return float(y / frame_height)
        
        # Centroid (normalized) - compute from bbox
        bbox = track.bbox
        centroid_x = bbox[0] + bbox[2] / 2
        centroid_y = bbox[1] + bbox[3] / 2
        self.client.send_message(f"{prefix}/centroid", 
                                  [norm_x(centroid_x), norm_y(centroid_y)])
        
        # Bounding box (normalized)
        self.client.send_message(f"{prefix}/bbox", [
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
        self.client.send_message(f"{prefix}/velocity",
                                  [norm_x(vel_x), norm_y(vel_y)])
        
        # All keypoints as flat list: [x0, y0, c0, x1, y1, c1, ...]
        keypoints_flat = []
        for i in range(17):
            x, y = track.keypoints[i]
            c = float(track.confidence[i])
            keypoints_flat.extend([norm_x(x), norm_y(y), c])
        
        self.client.send_message(f"{prefix}/keypoints", keypoints_flat)
    
    def send_count(self, count):
        """Send total dancer count."""
        if not self.enabled or not self.client:
            return
        
        self.client.send_message("/walldance/count", [count])
    
    def send_frame(self, tracks, frame_width, frame_height):
        """Send all tracking data for current frame."""
        if not self.enabled:
            return
        
        self.send_count(len(tracks))
        
        for track in tracks:
            self.send_dancer(track, frame_width, frame_height)
    
    def send_clear(self):
        """Send clear message (e.g., when resetting)."""
        if not self.enabled or not self.client:
            return
        
        self.client.send_message("/walldance/clear", [1])
