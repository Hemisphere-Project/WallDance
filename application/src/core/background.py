"""
Static background subtraction for WallDance.

Captures a reference frame (snapshot) and subtracts it from live frames
to isolate moving elements (dancers) from static background.

Supports both CPU (numpy/OpenCV) and GPU (PyTorch tensor) pipelines.

Usage:
    bg = BackgroundSubtractor()
    bg.capture_cpu(frame)           # Take snapshot from BGR numpy frame
    fg = bg.apply_cpu(frame, 30)    # Subtract with sensitivity 30

    bg.capture_gpu(gpu_tensor)      # Take snapshot from GPU tensor
    fg = bg.apply_gpu(gpu_tensor, 30)  # Subtract on GPU

Mismatch detection:
    After each apply, bg.foreground_ratio returns the fraction of pixels
    classified as foreground (0.0–1.0). Values above ~0.6 typically indicate
    the reference is outdated (camera moved, lighting changed drastically).
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

import cv2
import numpy as np

# Optional GPU support
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    torch = None
    TORCH_AVAILABLE = False

# Foreground ratio above this triggers a mismatch warning
BG_MISMATCH_THRESHOLD = 0.55


class BackgroundSubtractor:
    """Static frame background subtractor (CPU + GPU)."""

    def __init__(self):
        # CPU reference (BGR uint8 numpy, HWC)
        self._ref_cpu: Optional[np.ndarray] = None
        # GPU reference (float32 [0,1] RGB, BCHW)
        self._ref_gpu: Optional['torch.Tensor'] = None

        # Last computed foreground ratio (0.0–1.0)
        self.foreground_ratio: float = 0.0
        # Smoothed foreground ratio (EMA) for stable mismatch detection
        self._fg_ratio_ema: float = 0.0
        self._ema_alpha: float = 0.15  # Smooth over ~7 frames

        # Mismatch state
        self.is_mismatched: bool = False

        # Timing (ms)
        self.last_apply_ms: float = 0.0

        # Capture timestamp
        self.capture_time: Optional[float] = None

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------
    def capture_cpu(self, frame: np.ndarray):
        """Store a BGR numpy frame as the background reference."""
        self._ref_cpu = frame.copy()
        self._ref_gpu = None  # Invalidate GPU ref (will be rebuilt if needed)
        self.capture_time = time.time()
        self._fg_ratio_ema = 0.0
        self.is_mismatched = False
        self.foreground_ratio = 0.0
        print(f"[BG] Captured CPU reference {frame.shape[1]}x{frame.shape[0]}")

    def capture_gpu(self, gpu_tensor: 'torch.Tensor'):
        """Store a GPU tensor (1,3,H,W) float32 [0,1] as the background reference."""
        self._ref_gpu = gpu_tensor.clone()
        self._ref_cpu = None  # Invalidate CPU ref
        self.capture_time = time.time()
        self._fg_ratio_ema = 0.0
        self.is_mismatched = False
        self.foreground_ratio = 0.0
        _, _, h, w = gpu_tensor.shape
        print(f"[BG] Captured GPU reference {w}x{h}")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def has_reference(self) -> bool:
        """Whether a background reference has been captured."""
        return self._ref_cpu is not None or self._ref_gpu is not None

    @property
    def ref_cpu(self) -> Optional[np.ndarray]:
        return self._ref_cpu

    # ------------------------------------------------------------------
    # Apply (CPU path)
    # ------------------------------------------------------------------
    def apply_cpu(self, frame: np.ndarray, sensitivity: int = 30) -> np.ndarray:
        """
        Subtract background from a BGR numpy frame.

        Pixels with a per-channel absolute difference <= sensitivity are
        zeroed out (considered background).

        Args:
            frame: BGR uint8 numpy (H, W, 3)
            sensitivity: Threshold 0–255 (lower = more aggressive removal)

        Returns:
            Foreground-only frame (same shape, background pixels are black)
        """
        if self._ref_cpu is None:
            return frame

        t0 = time.perf_counter()

        ref = self._ref_cpu
        # Handle resolution mismatch (e.g. camera changed resolution)
        if ref.shape[:2] != frame.shape[:2]:
            ref = cv2.resize(ref, (frame.shape[1], frame.shape[0]))

        # Absolute difference per channel
        diff = cv2.absdiff(frame, ref)

        # Max across channels → single-channel mask
        max_diff = np.max(diff, axis=2)

        # Binary mask: foreground where any channel differs > sensitivity
        mask = (max_diff > sensitivity).astype(np.uint8)

        # Update foreground ratio + mismatch detection
        self._update_fg_ratio(float(mask.sum()) / float(mask.size))

        # Apply mask (broadcast to 3 channels)
        result = frame * mask[:, :, np.newaxis]

        self.last_apply_ms = (time.perf_counter() - t0) * 1000.0
        return result

    # ------------------------------------------------------------------
    # Apply (GPU path)
    # ------------------------------------------------------------------
    def apply_gpu(self, gpu_tensor: 'torch.Tensor', sensitivity: int = 30) -> 'torch.Tensor':
        """
        Subtract background from a GPU tensor.

        Args:
            gpu_tensor: (1, 3, H, W) float32 [0,1] RGB on CUDA
            sensitivity: Threshold 0–255 (mapped to 0.0–1.0 internally)

        Returns:
            Foreground-only tensor (same shape, background pixels zeroed)
        """
        if self._ref_gpu is None:
            return gpu_tensor

        t0 = time.perf_counter()

        ref = self._ref_gpu
        # Handle resolution mismatch
        if ref.shape[2:] != gpu_tensor.shape[2:]:
            ref = torch.nn.functional.interpolate(
                ref, size=gpu_tensor.shape[2:], mode='bilinear', align_corners=False
            )

        # Absolute difference per channel, max across channels
        diff = torch.abs(gpu_tensor - ref)
        max_diff, _ = diff.max(dim=1, keepdim=True)  # (1, 1, H, W)

        # Threshold (sensitivity is 0-255, tensor is 0.0-1.0)
        threshold = sensitivity / 255.0
        mask = (max_diff > threshold).float()  # (1, 1, H, W)

        # Update foreground ratio (cheap: just mean of mask)
        fg_ratio = mask.mean().item()
        self._update_fg_ratio(fg_ratio)

        # Apply mask (broadcasts across channels)
        result = gpu_tensor * mask

        self.last_apply_ms = (time.perf_counter() - t0) * 1000.0
        return result

    # ------------------------------------------------------------------
    # Ensure reference exists on the right device
    # ------------------------------------------------------------------
    def ensure_gpu_ref(self, device: 'torch.device'):
        """Convert CPU reference to GPU if only CPU reference exists."""
        if self._ref_gpu is not None:
            return
        if self._ref_cpu is None:
            return
        # BGR uint8 HWC → RGB float32 BCHW
        rgb = cv2.cvtColor(self._ref_cpu, cv2.COLOR_BGR2RGB)
        t = torch.from_numpy(rgb).float().div_(255.0)
        t = t.permute(2, 0, 1).unsqueeze(0).to(device)
        self._ref_gpu = t
        print(f"[BG] Converted CPU reference to GPU tensor")

    def ensure_cpu_ref(self):
        """Convert GPU reference to CPU if only GPU reference exists."""
        if self._ref_cpu is not None:
            return
        if self._ref_gpu is None:
            return
        # BCHW float32 RGB → HWC uint8 BGR
        t = self._ref_gpu.squeeze(0).clamp(0, 1)
        t = t.flip(0)  # RGB → BGR
        t = t.permute(1, 2, 0).mul(255).byte()
        self._ref_cpu = t.cpu().numpy()
        print(f"[BG] Converted GPU reference to CPU numpy")

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------
    def clear(self):
        """Remove the background reference."""
        self._ref_cpu = None
        self._ref_gpu = None
        self.foreground_ratio = 0.0
        self._fg_ratio_ema = 0.0
        self.is_mismatched = False
        self.capture_time = None
        print("[BG] Reference cleared")

    # ------------------------------------------------------------------
    # Mismatch detection
    # ------------------------------------------------------------------
    def _update_fg_ratio(self, ratio: float):
        """Update foreground ratio with EMA smoothing and mismatch flag."""
        self.foreground_ratio = ratio
        self._fg_ratio_ema = (self._ema_alpha * ratio +
                              (1.0 - self._ema_alpha) * self._fg_ratio_ema)
        self.is_mismatched = self._fg_ratio_ema > BG_MISMATCH_THRESHOLD
