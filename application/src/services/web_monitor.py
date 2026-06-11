"""
Lightweight smartphone monitor for WallDance camera setup.

P0 of docs/ROADMAP.md.  The laptop sits a couple of metres from the
camera, so there is no live feedback for setting focus or judging IR lighting.
This module streams the existing downscaled preview to any phone on the same
LAN (or the laptop's hotspot) over plain MJPEG-over-HTTP, with an overlay that
makes two setup tasks easy:

  * **Focus**  — a variance-of-Laplacian sharpness score on the centre crop,
                 with a peak-hold bar and a 2x zoomed centre inset.  Turn the
                 lens until the number stops climbing.
  * **Lighting** — mean brightness, clipping %, a luma histogram, and a
                 *uniformity* metric with the darkest grid tile marked, so IR
                 illuminators can be aimed for even coverage (MOG2 dislikes
                 gradients more than it dislikes darkness).

Dependency-free on purpose: only ``cv2``, ``numpy`` and the Python standard
library.  No Flask / aiohttp.  Open ``http://<laptop-ip>:<port>/`` on a phone.

The server is read-only: it never touches camera or tracker state.  It owns a
single "latest frame" slot updated by the app's render loop via
``update_frame()``; HTTP worker threads encode JPEGs on demand at their own
rate, so a slow phone never stalls the pipeline.
"""

from __future__ import annotations

import socket
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import cv2
import numpy as np


def get_lan_ip() -> str:
    """Best-effort outbound LAN IP (no packets actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


_INDEX_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>WallDance Monitor</title>
<style>
  html,body{margin:0;background:#0a0a0a;color:#ddd;font-family:system-ui,sans-serif;}
  #wrap{display:flex;flex-direction:column;align-items:center;}
  img{width:100vw;max-width:100%;height:auto;display:block;background:#000;}
  #stats{font-size:14px;padding:6px 10px;white-space:pre;font-variant-numeric:tabular-nums;}
  .hint{font-size:12px;color:#888;padding:0 10px 10px;}
  #ctl{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 10px;}
  button{font-size:15px;padding:8px 12px;border-radius:8px;border:1px solid #444;
         background:#1c1c1c;color:#ddd;}
  button.on{background:#1d7a1d;border-color:#2fae2f;color:#fff;}
  button.focus{background:#9a8400;border-color:#d4b800;color:#fff;}
  #gainwrap{display:none;align-items:center;gap:6px;}
  input[type=range]{width:140px;}
</style></head>
<body><div id="wrap">
  <img src="/stream.mjpg" alt="camera">
  <div id="ctl">
    <button id="btnFocus" onclick="toggleFocus()">Focus mode: OFF</button>
    <button id="btnAuto" onclick="toggleAuto()" style="display:none">Bright: AUTO</button>
    <span id="gainwrap"><span>gain</span>
      <input id="gain" type="range" min="1" max="16" step="0.5" value="3"
             oninput="setGain(this.value)"><span id="gainval">x3</span></span>
  </div>
  <div id="stats">connecting...</div>
  <div class="hint">Focus mode: brightens the view + highlights sharp edges (yellow). Turn the lens until FOCUS peaks.</div>
</div>
<script>
async function ctl(q){ try{ await fetch('/control?'+q,{cache:'no-store'}); }catch(e){} }
let focusOn=false, autoOn=true;
function toggleFocus(){ focusOn=!focusOn; ctl('focus='+(focusOn?'on':'off')); }
function toggleAuto(){ autoOn=!autoOn; ctl('mode='+(autoOn?'auto':'manual')); }
function setGain(v){ document.getElementById('gainval').textContent='x'+v; ctl('mode=manual&gain='+v); }
function applyState(s){
  focusOn=!!s.focus_mode; autoOn=!!s.focus_auto;
  const bf=document.getElementById('btnFocus'), ba=document.getElementById('btnAuto');
  bf.textContent='Focus mode: '+(focusOn?'ON':'OFF'); bf.className=focusOn?'focus':'';
  ba.textContent='Bright: '+(autoOn?'AUTO':'MANUAL'); ba.className=autoOn?'on':'';
  ba.style.display=focusOn?'':'none';   // Bright toggle only relevant in Focus mode
  document.getElementById('gainwrap').style.display=(focusOn&&!autoOn)?'flex':'none';
  if(typeof s.focus_gain==='number'){
    document.getElementById('gain').value=s.focus_gain;
    document.getElementById('gainval').textContent='x'+s.focus_gain;
  }
}
async function poll(){
  try{
    const r = await fetch('/stats.json',{cache:'no-store'});
    const s = await r.json();
    applyState(s);
    document.getElementById('stats').textContent =
      'FOCUS '+s.focus.toFixed(0)+'  (peak '+s.focus_peak.toFixed(0)+')\\n'+
      'BRIGHT '+s.brightness.toFixed(0)+'   UNIFORM '+(s.uniformity*100).toFixed(0)+'%'+
      '   CLIP hi '+s.clip_high.toFixed(1)+'% lo '+s.clip_low.toFixed(1)+'%';
  }catch(e){ document.getElementById('stats').textContent='no signal'; }
  setTimeout(poll, 500);
}
poll();
</script>
</body></html>
"""


class WebMonitor:
    """Threaded MJPEG monitor server with a focus/lighting overlay."""

    def __init__(
        self,
        port: int = 8080,
        host: str = "0.0.0.0",
        jpeg_quality: int = 70,
        max_fps: float = 15.0,
        center_frac: float = 0.4,
        grid: tuple[int, int] = (8, 5),
    ):
        self.port = int(port)
        self.host = host
        self.jpeg_quality = int(jpeg_quality)
        self.max_fps = float(max_fps)
        self.center_frac = float(center_frac)
        self.grid = grid

        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None   # latest clean BGR frame (owned copy)
        self._frame_id = 0

        # Encode cache so N clients don't re-encode the same frame.  Keyed by
        # (frame_id, render_ver) so a control change re-renders even if the
        # frame itself hasn't advanced (e.g. paused preview).
        self._enc_lock = threading.Lock()
        self._enc_key: Optional[tuple] = None
        self._enc_jpeg: Optional[bytes] = None

        self._stats: dict = {}        # full stats (incl center_box) for the cached frame
        self._stats_id = -1            # frame id the cached stats belong to
        self._focus_peak = 1.0         # overview-mode focus peak-hold

        # Focus mode (manual-lens focusing aid): brightened display + peaking.
        self._focus_mode = False
        self._focus_auto = True        # True = auto histogram stretch; False = manual gain
        self._focus_gain = 3.0         # display gain when manual (1..16)
        self._render_ver = 0           # bumps on control change to bust the encode cache

        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Start the HTTP server in a daemon thread. Returns success."""
        if self._running:
            return True
        try:
            handler = partial(_MonitorHandler, monitor=self)
            self._server = ThreadingHTTPServer((self.host, self.port), handler)
            self._server.daemon_threads = True
        except OSError as e:
            print(f"[WebMonitor] could not bind {self.host}:{self.port}: {e}")
            self._server = None
            return False
        self._running = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="WebMonitor")
        self._thread.start()
        print(f"[WebMonitor] live at http://{get_lan_ip()}:{self.port}/  "
              f"(open on a phone on the same network)")
        return True

    def stop(self) -> None:
        self._running = False
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    @property
    def running(self) -> bool:
        return self._running

    def url(self) -> str:
        """The address a phone on the same network should open."""
        host = self.host
        if host in ("0.0.0.0", "", "::"):
            host = get_lan_ip()
        return f"http://{host}:{self.port}/"

    def qr_matrix(self) -> Optional[list]:
        """QR modules (list of rows of bool) for the webui URL, or None.

        Uses the optional ``segno`` package (pure-Python).  Returns None if it
        is not installed so the caller can fall back to showing the URL text.
        """
        try:
            import segno
        except Exception:
            return None
        try:
            qr = segno.make(self.url(), error="m")
            return [[bool(c) for c in row] for row in qr.matrix]
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Frame intake (called from the app render loop)
    # ------------------------------------------------------------------
    def update_frame(self, frame_bgr: np.ndarray) -> None:
        """Store a copy of the latest clean preview frame (cheap, non-blocking)."""
        if frame_bgr is None or not self._running:
            return
        # Copy immediately so the caller may draw overlays on its buffer after.
        with self._lock:
            self._frame = frame_bgr.copy()
            self._frame_id += 1

    # ------------------------------------------------------------------
    # JPEG production (called from HTTP worker threads)
    # ------------------------------------------------------------------
    def get_jpeg(self) -> Optional[bytes]:
        """Return the current overlaid frame as JPEG bytes, or None if no frame."""
        with self._lock:
            frame = self._frame
            fid = self._frame_id
        if frame is None:
            return None
        # Reuse the cached encode if this frame+render was already produced.
        key = (fid, self._render_ver)
        with self._enc_lock:
            if key == self._enc_key and self._enc_jpeg is not None:
                return self._enc_jpeg
        overlaid = self._render_overlay(frame)
        ok, buf = cv2.imencode(
            ".jpg", overlaid, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            return None
        jpeg = buf.tobytes()
        with self._enc_lock:
            self._enc_key = key
            self._enc_jpeg = jpeg
        return jpeg

    def get_stats(self) -> dict:
        """Public stats for /stats.json — computed on demand from the latest frame."""
        s = self._stats_for_current_frame()
        out = {k: v for k, v in s.items() if k != "center_box"}
        out["focus_mode"] = self._focus_mode
        out["focus_auto"] = self._focus_auto
        out["focus_gain"] = round(self._focus_gain, 1)
        return out

    # ------------------------------------------------------------------
    # Focus-mode controls (driven by the /control endpoint)
    # ------------------------------------------------------------------
    def set_focus_mode(self, on: bool) -> None:
        self._focus_mode = bool(on)
        self._render_ver += 1

    def set_focus_bright(self, auto: Optional[bool] = None,
                         gain: Optional[float] = None) -> None:
        if auto is not None:
            self._focus_auto = bool(auto)
        if gain is not None:
            self._focus_gain = float(max(1.0, min(16.0, gain)))
        self._render_ver += 1

    def _stats_for_current_frame(self) -> dict:
        """Compute (once per frame, cached) the focus/lighting stats.

        Shared by the overlay renderer and the /stats.json route so stats are
        available even if nobody is pulling the MJPEG stream, and so focus_peak
        advances exactly once per frame.
        """
        with self._lock:
            frame = self._frame
            fid = self._frame_id
        if frame is None:
            return {}
        if fid == self._stats_id and self._stats:
            return self._stats
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        self._stats = self._compute_stats(gray)
        self._stats_id = fid
        return self._stats

    # ------------------------------------------------------------------
    # Metrics + overlay
    # ------------------------------------------------------------------
    def _compute_stats(self, gray: np.ndarray) -> dict:
        h, w = gray.shape[:2]
        # Centre crop for focus
        cf = self.center_frac
        x0, x1 = int(w * (0.5 - cf / 2)), int(w * (0.5 + cf / 2))
        y0, y1 = int(h * (0.5 - cf / 2)), int(h * (0.5 + cf / 2))
        crop = gray[y0:y1, x0:x1]
        focus = float(cv2.Laplacian(crop, cv2.CV_64F).var()) if crop.size else 0.0

        brightness = float(gray.mean())
        total = gray.size
        clip_high = float(np.count_nonzero(gray >= 250)) / total * 100.0
        clip_low = float(np.count_nonzero(gray <= 5)) / total * 100.0

        # Uniformity over a grid of tile means: min/max ratio (1.0 = perfectly even)
        gx, gy = self.grid
        tiles = np.zeros((gy, gx), dtype=np.float64)
        for j in range(gy):
            ya, yb = h * j // gy, h * (j + 1) // gy
            for i in range(gx):
                xa, xb = w * i // gx, w * (i + 1) // gx
                tiles[j, i] = gray[ya:yb, xa:xb].mean()
        t_max = float(tiles.max()) if tiles.size else 0.0
        t_min = float(tiles.min()) if tiles.size else 0.0
        uniformity = (t_min / t_max) if t_max > 1e-6 else 0.0
        dark_idx = np.unravel_index(int(tiles.argmin()), tiles.shape) if tiles.size else (0, 0)

        # Peak-hold focus (slow decay so re-focusing recalibrates the bar)
        self._focus_peak = max(focus, self._focus_peak * 0.995, 1.0)

        return {
            "focus": focus,
            "focus_peak": self._focus_peak,
            "brightness": brightness,
            "clip_high": clip_high,
            "clip_low": clip_low,
            "uniformity": uniformity,
            "dark_tile": (int(dark_idx[1]), int(dark_idx[0])),  # (col, row)
            "center_box": (x0, y0, x1, y1),
        }

    def _render_overlay(self, frame: np.ndarray) -> np.ndarray:
        out = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        out = out.copy()
        s = self._stats_for_current_frame()
        if not s:
            return out
        if self._focus_mode:
            return self._render_focus_overlay(out, frame, s)
        return self._render_normal_overlay(out, frame, s)

    def _render_normal_overlay(self, out, frame, s):
        """Overview: focus/lighting metrics, histogram, darkest-tile marker."""
        h, w = out.shape[:2]
        green, red, white, dark = (0, 255, 0), (60, 60, 255), (240, 240, 240), (0, 0, 0)

        # --- centre focus box ---
        x0, y0, x1, y1 = s["center_box"]
        cv2.rectangle(out, (x0, y0), (x1, y1), green, 1)

        # --- text panel (top-left, on a dark plate) ---
        lines = [
            f"FOCUS {s['focus']:.0f}",
            f"  peak {s['focus_peak']:.0f}",
            f"BRIGHT {s['brightness']:.0f}",
            f"UNIFORM {s['uniformity'] * 100:.0f}%",
            f"CLIP hi {s['clip_high']:.1f} lo {s['clip_low']:.1f}",
        ]
        cv2.rectangle(out, (0, 0), (190, 18 * len(lines) + 8), dark, -1)
        for i, ln in enumerate(lines):
            cv2.putText(out, ln, (6, 18 * (i + 1)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, white, 1, cv2.LINE_AA)

        # --- focus bar (under the panel), length vs peak ---
        bar_y = 18 * len(lines) + 14
        bw = 180
        frac = max(0.0, min(1.0, s["focus"] / s["focus_peak"])) if s["focus_peak"] else 0.0
        cv2.rectangle(out, (6, bar_y), (6 + bw, bar_y + 6), (70, 70, 70), -1)
        cv2.rectangle(out, (6, bar_y), (6 + int(bw * frac), bar_y + 6), green, -1)

        # --- luma histogram (bottom-left) from the CLEAN frame so the overlay's
        #     own black plates / white text don't pollute the bins ---
        clean_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        self._draw_histogram(out, clean_gray)

        # --- zoomed centre inset (top-right) to judge focus directly ---
        self._draw_center_inset(out, frame, s["center_box"])

        # --- darkest-tile marker LAST so it is never hidden (where to add IR) --
        gx, gy = self.grid
        dc, dr = s["dark_tile"]
        dxa, dxb = w * dc // gx, w * (dc + 1) // gx
        dya, dyb = h * dr // gy, h * (dr + 1) // gy
        cv2.rectangle(out, (dxa, dya), (dxb, dyb), red, 2)
        label_y = dya + 16 if dya < h - 20 else dya - 6
        cv2.putText(out, "dark", (dxa + 3, label_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, red, 1, cv2.LINE_AA)
        return out

    # ------------------------------------------------------------------
    # Focus mode: brightened display + peaking + prominent focusness gauge
    # ------------------------------------------------------------------
    def _enhance_for_focus(self, frame: np.ndarray) -> np.ndarray:
        """Return a brightened mono BGR view so a dark IR image is focusable.

        auto   → percentile stretch + gamma lift (smartphone-'flash' style)
        manual → linear gain on the raw pixels
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        if self._focus_auto:
            lo, hi = np.percentile(gray, (2.0, 98.0))
            if hi <= lo:
                lo, hi = float(gray.min()), float(max(gray.max(), gray.min() + 1))
            stretched = np.clip((gray.astype(np.float32) - lo) * (255.0 / (hi - lo)), 0, 255)
            disp = (((stretched / 255.0) ** 0.6) * 255.0).astype(np.uint8)  # gamma lift
        else:
            disp = cv2.convertScaleAbs(gray, alpha=self._focus_gain, beta=0.0)
        return cv2.cvtColor(disp, cv2.COLOR_GRAY2BGR)

    @staticmethod
    def _draw_peaking(out: np.ndarray, gray: np.ndarray) -> None:
        """Paint the sharpest edges yellow (manual-focus peaking).

        Computed on the CLEAN gray with a light denoise blur so amplified
        sensor noise in dark areas is not mistaken for edges.  The gradient is
        thresholded above the local noise floor, so as the lens nears focus
        more / stronger edges light up.
        """
        g = cv2.GaussianBlur(gray, (3, 3), 0)
        mag = np.abs(cv2.Laplacian(g, cv2.CV_32F, ksize=3))
        thr = max(24.0, float(mag.mean() + 4.0 * mag.std()))
        out[mag >= thr] = (0, 255, 255)  # yellow (BGR)

    def _render_focus_overlay(self, out, frame, s):
        """Focus-setting view: bright display + peaking + big focusness gauge."""
        h, w = out.shape[:2]
        green, white, dark, yellow = (0, 255, 0), (240, 240, 240), (0, 0, 0), (0, 255, 255)
        clean_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame

        # 1. Brighten the whole display so the dark IR image becomes visible.
        out = self._enhance_for_focus(frame)

        # 2. Focus peaking measured on the CLEAN gray (display brightness does
        #    not pollute it), drawn over the brightened view.
        self._draw_peaking(out, clean_gray)
        inset_src = out.copy()  # enhanced + peaked, before boxes — for the zoom

        # 3. Focusness gauge uses the same clean-frame metric as overview mode.
        focus = s["focus"]
        peak = s["focus_peak"] or 1.0
        pct = max(0.0, min(1.0, focus / peak))

        # 4. Centre focus box.
        x0, y0, x1, y1 = s["center_box"]
        cv2.rectangle(out, (x0, y0), (x1, y1), green, 1)

        # 5. Prominent focusness gauge (top-left).
        mode = "AUTO" if self._focus_auto else f"GAIN x{self._focus_gain:.1f}"
        cv2.rectangle(out, (0, 0), (304, 122), dark, -1)
        cv2.putText(out, "FOCUS MODE", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, yellow, 2, cv2.LINE_AA)
        cv2.putText(out, f"{focus:.0f}", (8, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, white, 3, cv2.LINE_AA)
        cv2.putText(out, f"{pct * 100:.0f}% of peak   [{mode}]", (8, 92),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, white, 1, cv2.LINE_AA)
        bx0, by0, bw = 8, 100, 288
        cv2.rectangle(out, (bx0, by0), (bx0 + bw, by0 + 14), (70, 70, 70), -1)
        bar_col = green if pct > 0.85 else (0, 200, 255)  # green near peak, amber below
        cv2.rectangle(out, (bx0, by0), (bx0 + int(bw * pct), by0 + 14), bar_col, -1)

        # 6. Zoomed centre inset (top-right) — enhanced + peaked detail.
        self._draw_center_inset(out, inset_src, s["center_box"])

        # 7. Hint.
        cv2.putText(out, "Turn the lens until FOCUS peaks (bar full / green)",
                    (8, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, yellow, 1, cv2.LINE_AA)
        return out

    @staticmethod
    def _draw_histogram(out: np.ndarray, gray: np.ndarray) -> None:
        h, w = out.shape[:2]
        bins = 64
        hist = cv2.calcHist([gray], [0], None, [bins], [0, 256]).flatten()
        hmax = float(hist.max()) if hist.size else 1.0
        if hmax <= 0:
            return
        strip_h = max(30, h // 8)
        y_base = h - 4
        plate_top = y_base - strip_h - 4
        cv2.rectangle(out, (0, plate_top), (bins * 3 + 8, y_base + 2), (0, 0, 0), -1)
        for i, v in enumerate(hist):
            bh = int((v / hmax) * strip_h)
            x = 4 + i * 3
            cv2.rectangle(out, (x, y_base - bh), (x + 2, y_base),
                          (180, 180, 180), -1)

    @staticmethod
    def _draw_center_inset(out: np.ndarray, clean: np.ndarray,
                           center_box: tuple) -> None:
        x0, y0, x1, y1 = center_box
        patch = clean[y0:y1, x0:x1]
        if patch.size == 0:
            return
        h, w = out.shape[:2]
        target = max(80, w // 5)
        ph, pw = patch.shape[:2]
        if pw == 0 or ph == 0:
            return
        scale = target / pw
        inset = cv2.resize(patch, (target, max(1, int(ph * scale))),
                           interpolation=cv2.INTER_NEAREST)
        ih, iw = inset.shape[:2]
        if iw >= w or ih >= h:
            return
        # Top-right corner: panel is top-left, histogram bottom-left, and the
        # darkest-tile marker usually lands along the bottom (IR edge falloff),
        # so the top-right is the one collision-free spot for the zoom inset.
        ox, oy = w - iw - 4, 4
        out[oy:oy + ih, ox:ox + iw] = inset
        cv2.rectangle(out, (ox, oy), (ox + iw, oy + ih), (0, 255, 0), 1)


class _MonitorHandler(BaseHTTPRequestHandler):
    """One handler instance per request; ``monitor`` is bound via functools.partial."""

    protocol_version = "HTTP/1.1"

    def __init__(self, *args, monitor: WebMonitor, **kwargs):
        self.monitor = monitor
        super().__init__(*args, **kwargs)

    # Silence the default per-request stderr logging.
    def log_message(self, *args, **kwargs):  # noqa: D401
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_html(_INDEX_HTML)
        elif self.path.startswith("/stream.mjpg"):
            self._serve_mjpeg()
        elif self.path.startswith("/snapshot.jpg"):
            self._serve_snapshot()
        elif self.path.startswith("/stats.json"):
            self._serve_stats()
        elif self.path.startswith("/control"):
            self._serve_control()
        else:
            self.send_error(404)

    # -- routes ---------------------------------------------------------
    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_stats(self):
        import json
        body = json.dumps(self.monitor.get_stats()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_control(self):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        if "focus" in q:
            self.monitor.set_focus_mode(q["focus"][0].lower() in ("1", "on", "true"))
        if "mode" in q:
            self.monitor.set_focus_bright(auto=(q["mode"][0].lower() == "auto"))
        if "gain" in q:
            try:
                self.monitor.set_focus_bright(gain=float(q["gain"][0]))
            except ValueError:
                pass
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_snapshot(self):
        jpeg = self.monitor.get_jpeg()
        if jpeg is None:
            self.send_error(503, "no frame yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(jpeg)))
        self.end_headers()
        self.wfile.write(jpeg)

    def _serve_mjpeg(self):
        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header(
            "Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
        self.end_headers()
        min_dt = 1.0 / self.monitor.max_fps if self.monitor.max_fps > 0 else 0.0
        try:
            while self.monitor.running:
                t0 = time.time()
                jpeg = self.monitor.get_jpeg()
                if jpeg is None:
                    time.sleep(0.05)
                    continue
                self.wfile.write(b"--FRAME\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                dt = time.time() - t0
                if min_dt > dt:
                    time.sleep(min_dt - dt)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client disconnected — normal


if __name__ == "__main__":
    # Standalone smoke test: serve a synthetic animated frame.
    mon = WebMonitor(port=8080)
    if not mon.start():
        raise SystemExit("could not start monitor")
    try:
        t = 0
        while True:
            img = np.full((540, 960, 3), 30, dtype=np.uint8)
            cv2.circle(img, (480 + int(200 * np.sin(t / 10)), 270), 40,
                       (200, 200, 200), -1)
            cv2.putText(img, f"frame {t}", (20, 520),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            mon.update_frame(img)
            time.sleep(0.05)
            t += 1
    except KeyboardInterrupt:
        mon.stop()
