"""PERF-4: core.fast_clahe must be bit-identical to kornia's equalize_clahe.

The GPU enhance feeds YOLO, so any bit of difference could change detections
(and the replay goldens).  These cases cover random, dark, saturated and
exact-bin-edge inputs (k/256, 0, 1, just outside [0, 1]), odd/non-divisible
shapes, the clip range the app uses (incl. 0 = no clipping), other grids
(kornia's own failures must fail alike), plus the GpuEnhancer wiring.
CUDA-only (the fast path is GPU-only).
"""
import pytest

torch = pytest.importorskip("torch")
if not torch.cuda.is_available():
    pytest.skip("CUDA not available", allow_module_level=True)

import core.gpu_pipeline as gp  # noqa: E402  (stubs kornia_rs before kornia)

if not gp.KORNIA_AVAILABLE:
    pytest.skip("kornia not available", allow_module_level=True)

from kornia.enhance import equalize_clahe  # noqa: E402

from core.fast_clahe import FastClahe  # noqa: E402

SHAPES = [(1195, 1296), (1139, 1299), (720, 1280), (97, 131), (16, 16), (33, 9)]


def _inputs(h, w, seed):
    g = torch.Generator().manual_seed(seed)
    yield "rand", torch.rand(1, 1, h, w, generator=g)
    k = torch.randint(0, 257, (1, 1, h, w), generator=g).float() / 256.0
    k[..., ::7, ::5] = 1.0
    k[..., ::11, ::3] = 0.0
    k[..., ::13, ::17] = 1.0000001      # outside [0, 1]: histc ignores it
    k[..., ::19, ::23] = -1e-7
    yield "edges", k
    yield "dark", (torch.rand(1, 1, h, w, generator=g) ** 4) * 0.2
    yield "sat", torch.clamp(torch.randn(1, 1, h, w, generator=g) * 0.3 + 0.9, 0, 1)


@pytest.mark.parametrize("h,w", SHAPES)
def test_bit_identical_to_kornia(h, w):
    fc = FastClahe()
    for kind, x in _inputs(h, w, seed=h * 7 + w):
        x = x.cuda()
        for clip in (2.5, 1.6, 4.9, 40.0, 0.0):
            for grid in ((8, 8), (4, 4), (4, 6)):   # (4, 6): kornia raises
                try:
                    ref = equalize_clahe(x, clip_limit=clip, grid_size=grid)
                except Exception as exc:  # noqa: BLE001 - same failure expected
                    with pytest.raises(type(exc)):
                        fc.equalize_clahe(x, clip_limit=clip, grid_size=grid)
                    continue
                out = fc.equalize_clahe(x, clip_limit=clip, grid_size=grid)
                assert torch.equal(ref, out), (kind, clip, grid)
    assert fc.fallbacks == 0


def test_cached_tensors_reused_across_calls():
    fc = FastClahe()
    x = torch.rand(1, 1, 300, 400, device="cuda")
    a = fc(x, 2.5, (8, 8))
    n_idx, n_w = len(fc._idx_cache), len(fc._w_cache)
    b = fc(x, 2.5, (8, 8))
    assert torch.equal(a, b) and (len(fc._idx_cache), len(fc._w_cache)) == (n_idx, n_w)


def test_falls_back_to_kornia_semantics():
    fc = FastClahe()
    x = torch.rand(1, 1, 64, 64, device="cuda")
    with pytest.raises(TypeError):                 # kornia: clip must be a float
        fc(x, 2, (8, 8))
    cpu = torch.rand(1, 1, 64, 64)                 # CPU input -> kornia directly
    assert torch.equal(fc(cpu, 2.5, (8, 8)), equalize_clahe(cpu, 2.5, (8, 8)))


def test_gpu_enhancer_uses_fast_clahe_and_matches_kornia_path():
    enh = gp.GpuEnhancer()
    assert enh._clahe is not None
    rgb = torch.rand(1, 1, 500, 700, device="cuda").expand(1, 3, -1, -1).contiguous()
    fast = enh._apply_clahe_gamma_gpu(rgb, 2.5, 8, 2.2)
    enh._clahe = None                              # the pre-PERF-4 kornia path
    ref = enh._apply_clahe_gamma_gpu(rgb, 2.5, 8, 2.2)
    assert torch.equal(fast, ref)
