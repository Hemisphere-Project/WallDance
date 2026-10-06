"""Launch-light, bit-exact CLAHE for the GPU enhance path (PERF-4).

``kornia.enhance.equalize_clahe`` (kornia 0.8.x, the non-differentiable
path) is launch-bound on the show path, not GPU-bound: per frame it runs one
``torch.histc`` per tile through a Python ``map`` (64 launches + a stack for
the 8x8 grid) and builds its LUT-gather indices as *CPU* tensors, so every
advanced-indexing step does a pageable H2D copy that blocks the host
(~7 stream syncs per frame).  The GPU work itself is ~2 ms; the call took
4.6-6.5 ms on the show laptop and ~9 ms on dev37.

``equalize_clahe`` below is the same algorithm with the same tensor ops in
the same order -- outputs are **bit-identical** to kornia's
(``tests/test_fast_clahe.py`` checks random, edge-value and real-footage
inputs with ``torch.equal``) -- with two changes:

* the 64 per-tile histograms are one batched scatter-add.  ``torch.histc``
  with ``min=0, max=1, bins=256`` bins a value ``x`` at ``int(x * 256)``
  (clamped to 255 for ``x == 1``) and ignores values outside ``[0, 1]`` (and
  NaN).  ``x * 256`` is exact in float32, and the counts are integers far
  below 2**24, so the float32 histograms are identical whatever the
  accumulation order;
* the LUT-gather index tensors and the interpolation weights are built once
  per (shape, device) and cached on the device, so nothing in the call
  synchronises the host.

Anything this module does not cover (other kornia versions, the
differentiable path, odd shapes) falls back to kornia: callers use
``FastClahe.__call__`` which delegates to kornia on any exception.
"""
from __future__ import annotations

import sys as _sys
import types as _types
from typing import Dict, Tuple

import torch

# Same kornia_rs stub as core.gpu_pipeline: its AVX2-only wheel SIGILLs on the
# Ivy-Bridge dev box, and only kornia's pure-torch ops are used here.
if "kornia_rs" not in _sys.modules:
    _sys.modules["kornia_rs"] = _types.ModuleType("kornia_rs")

try:  # kornia's own tiling helpers: pure reshapes/pads, reused verbatim
    from kornia.enhance.equalization import (
        _compute_interpolation_tiles,
        _compute_tiles,
        equalize_clahe as _kornia_equalize_clahe,
    )
    KORNIA_CLAHE_AVAILABLE = True
except Exception:  # noqa: BLE001 - optional dependency
    KORNIA_CLAHE_AVAILABLE = False
    _compute_tiles = _compute_interpolation_tiles = _kornia_equalize_clahe = None

NUM_BINS = 256


def _batched_histc01(tiles: torch.Tensor, num_bins: int = NUM_BINS) -> torch.Tensor:
    """Row-wise ``torch.histc(row, bins=num_bins, min=0, max=1)`` for a
    (T, P) float tensor, as one scatter-add (float32 counts, like histc)."""
    t, p = tiles.shape
    x = tiles if tiles.dtype in (torch.float32, torch.float64) else tiles.float()
    valid = (x >= 0) & (x <= 1)                      # histc drops out-of-range / NaN
    idx = (x * num_bins).to(torch.int64).clamp_(0, num_bins - 1)
    idx += (torch.arange(t, device=x.device, dtype=torch.int64) * num_bins).unsqueeze(1)
    counts = torch.zeros(t * num_bins, dtype=torch.int32, device=x.device)
    counts.scatter_add_(0, idx.view(-1), valid.view(-1).to(torch.int32))
    return counts.view(t, num_bins).to(tiles.dtype)


def _compute_luts(tiles_x_im: torch.Tensor, clip: float, num_bins: int = NUM_BINS) -> torch.Tensor:
    """kornia ``_compute_luts`` (diff=False) with the batched histogram."""
    b, gh, gw, c, th, tw = tiles_x_im.shape
    pixels: int = th * tw
    tiles: torch.Tensor = tiles_x_im.view(-1, pixels)
    histos = _batched_histc01(tiles, num_bins)
    if clip > 0.0:
        max_val: float = max(clip * pixels // num_bins, 1)
        histos.clamp_(max=max_val)
        clipped: torch.Tensor = pixels - histos.sum(1)
        residual: torch.Tensor = torch.remainder(clipped, num_bins)
        redist: torch.Tensor = (clipped - residual).div(num_bins)
        histos += redist[None].transpose(0, 1)
        v_range: torch.Tensor = torch.arange(num_bins, device=histos.device)
        mat_range: torch.Tensor = v_range.repeat(histos.shape[0], 1)
        histos += mat_range < residual[None].transpose(0, 1)
    lut_scale: float = (num_bins - 1) / pixels
    luts: torch.Tensor = torch.cumsum(histos, 1) * lut_scale
    luts = luts.clamp(0, num_bins - 1)
    luts = luts.floor()
    return luts.view((b, gh, gw, c, num_bins))


class FastClahe:
    """``equalize_clahe`` with per-(shape, device) cached index/weight tensors."""

    def __init__(self):
        self._idx_cache: Dict[Tuple, Tuple[torch.Tensor, torch.Tensor]] = {}
        self._w_cache: Dict[Tuple, Tuple[torch.Tensor, ...]] = {}
        self.fallbacks = 0

    # -- kornia _map_luts with device-resident index tensors ----------------
    def _indices(self, gh: int, gw: int, device) -> Tuple[torch.Tensor, torch.Tensor]:
        key = (gh, gw, str(device))
        hit = self._idx_cache.get(key)
        if hit is not None:
            return hit
        j_idxs = torch.empty(0, 4, dtype=torch.long)
        if gh > 2:
            j_floor = torch.arange(1, gh - 1).view(gh - 2, 1).div(2, rounding_mode="trunc")
            j_idxs = torch.tensor([[0, 0, 1, 1], [-1, -1, 0, 0]] * ((gh - 2) // 2))
            j_idxs += j_floor
        i_idxs = torch.empty(0, 4, dtype=torch.long)
        if gw > 2:
            i_floor = torch.arange(1, gw - 1).view(gw - 2, 1).div(2, rounding_mode="trunc")
            i_idxs = torch.tensor([[0, 1, 0, 1], [-1, 0, -1, 0]] * ((gw - 2) // 2))
            i_idxs += i_floor
        hit = (j_idxs.to(device), i_idxs.to(device))
        self._idx_cache[key] = hit
        return hit

    def _map_luts(self, interp_tiles: torch.Tensor, luts: torch.Tensor) -> torch.Tensor:
        num_imgs, gh, gw, c, _, _ = interp_tiles.shape
        j_idxs, i_idxs = self._indices(gh, gw, interp_tiles.device)
        out = torch.full((num_imgs, gh, gw, 4, c, luts.shape[-1]), -1,
                         dtype=interp_tiles.dtype, device=interp_tiles.device)
        out[:, 0:: gh - 1, 0:: gw - 1, 0] = luts[:, 0:: max(gh // 2 - 1, 1), 0:: max(gw // 2 - 1, 1)]
        out[:, 1:-1, 0:: gw - 1, 0] = luts[:, j_idxs[:, 0], 0:: max(gw // 2 - 1, 1)]
        out[:, 1:-1, 0:: gw - 1, 1] = luts[:, j_idxs[:, 2], 0:: max(gw // 2 - 1, 1)]
        out[:, 0:: gh - 1, 1:-1, 0] = luts[:, 0:: max(gh // 2 - 1, 1), i_idxs[:, 0]]
        out[:, 0:: gh - 1, 1:-1, 1] = luts[:, 0:: max(gh // 2 - 1, 1), i_idxs[:, 1]]
        out[:, 1:-1, 1:-1, :] = luts[
            :, j_idxs.repeat(max(gh - 2, 1), 1, 1).permute(1, 0, 2), i_idxs.repeat(max(gw - 2, 1), 1, 1)
        ]
        return out

    # -- kornia _compute_equalized_tiles with cached interpolation weights ---
    def _weights(self, gh, gw, th, tw, dtype, device):
        key = (gh, gw, th, tw, dtype, str(device))
        hit = self._w_cache.get(key)
        if hit is not None:
            return hit
        ih = (
            torch.arange(2 * th - 1, -1, -1, dtype=dtype, device=device)
            .div(2.0 * th - 1)[None]
            .transpose(-2, -1)
            .expand(2 * th, tw)
        )
        ih = ih.unfold(0, th, th).unfold(1, tw, tw)
        iw = (
            torch.arange(2 * tw - 1, -1, -1, dtype=dtype, device=device)
            .div(2.0 * tw - 1)
            .expand(th, 2 * tw)
        )
        iw = iw.unfold(0, th, th).unfold(1, tw, tw)
        tiw = iw.expand((gw - 2) // 2, 2, th, tw).reshape(gw - 2, 1, th, tw).unsqueeze(0)
        tih = ih.repeat((gh - 2) // 2, 1, 1, 1).unsqueeze(1)
        hit = (tiw, tih)
        self._w_cache[key] = hit
        return hit

    def _equalized_tiles(self, interp_tiles: torch.Tensor, luts: torch.Tensor) -> torch.Tensor:
        mapped_luts = self._map_luts(interp_tiles, luts)
        num_imgs, gh, gw, c, th, tw = interp_tiles.shape
        flat = (interp_tiles * 255).long().flatten(-2, -1)
        flat = flat.unsqueeze(-3).expand(num_imgs, gh, gw, 4, c, th * tw)
        pre = (
            torch.gather(mapped_luts, 5, flat)
            .to(interp_tiles)
            .reshape(num_imgs, gh, gw, 4, c, th, tw)
        )
        tiles_equalized = torch.zeros_like(interp_tiles)
        tiw, tih = self._weights(gh, gw, th, tw, interp_tiles.dtype, interp_tiles.device)
        tl, tr, bl, br = pre[:, 1:-1, 1:-1].unbind(3)
        t = torch.addcmul(tr, tiw, torch.sub(tl, tr))
        b = torch.addcmul(br, tiw, torch.sub(bl, br))
        tiles_equalized[:, 1:-1, 1:-1] = torch.addcmul(b, tih, torch.sub(t, b))
        tiles_equalized[:, 0:: gh - 1, 0:: gw - 1] = pre[:, 0:: gh - 1, 0:: gw - 1, 0]
        t, b, _, _ = pre[:, 1:-1, 0].unbind(2)
        tiles_equalized[:, 1:-1, 0] = torch.addcmul(b, tih.squeeze(1), torch.sub(t, b))
        t, b, _, _ = pre[:, 1:-1, gh - 1].unbind(2)
        tiles_equalized[:, 1:-1, gh - 1] = torch.addcmul(b, tih.squeeze(1), torch.sub(t, b))
        left, right, _, _ = pre[:, 0, 1:-1].unbind(2)
        tiles_equalized[:, 0, 1:-1] = torch.addcmul(right, tiw, torch.sub(left, right))
        left, right, _, _ = pre[:, gw - 1, 1:-1].unbind(2)
        tiles_equalized[:, gw - 1, 1:-1] = torch.addcmul(right, tiw, torch.sub(left, right))
        return tiles_equalized.div(255.0)

    def equalize_clahe(self, input: torch.Tensor, clip_limit: float = 40.0,
                       grid_size: Tuple[int, int] = (8, 8)) -> torch.Tensor:
        """Same contract and result as ``kornia.enhance.equalize_clahe``
        (``slow_and_differentiable=False``)."""
        if not isinstance(clip_limit, float):
            raise TypeError(f"Input clip_limit type is not float. Got {type(clip_limit)}")
        if (not isinstance(grid_size, tuple) or len(grid_size) != 2
                or not all(isinstance(g, int) and g > 0 for g in grid_size)):
            raise ValueError(f"Invalid grid_size {grid_size!r}")
        imgs = input
        hist_tiles, img_padded = _compute_tiles(imgs, grid_size, True)
        tile_size = (hist_tiles.shape[-2], hist_tiles.shape[-1])
        interp_tiles = _compute_interpolation_tiles(img_padded, tile_size)
        luts = _compute_luts(hist_tiles, clip=clip_limit)
        equalized = self._equalized_tiles(interp_tiles, luts)
        eq_imgs = equalized.permute(0, 3, 1, 4, 2, 5).reshape_as(img_padded)
        h, w = imgs.shape[-2:]
        eq_imgs = eq_imgs[..., :h, :w]
        if input.dim() != eq_imgs.dim():
            eq_imgs = eq_imgs.squeeze(0)
        return eq_imgs

    def __call__(self, input: torch.Tensor, clip_limit: float,
                 grid_size: Tuple[int, int]) -> torch.Tensor:
        """Fast path; on any failure, kornia's own implementation (which may
        raise -- the caller's existing RuntimeError/ValueError guard applies)."""
        if input.dim() == 4 and input.is_cuda and input.dtype == torch.float32:
            try:
                return self.equalize_clahe(input, clip_limit=clip_limit, grid_size=grid_size)
            except Exception:  # noqa: BLE001 - kornia decides what is an error
                self.fallbacks += 1
        return _kornia_equalize_clahe(input, clip_limit=clip_limit, grid_size=grid_size)

