#!/bin/bash
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SITE_PACKAGES="$ROOT_DIR/.venv/lib/python3.13/site-packages"
export LD_LIBRARY_PATH="$SITE_PACKAGES/nvidia/cudnn/lib:$SITE_PACKAGES/nvidia/cublas/lib:$LD_LIBRARY_PATH"

ln -sf /usr/lib/nvidia-cuda-toolkit/libdevice/libdevice.10.bc "$ROOT_DIR/cuda_fix/nvvm/libdevice/libdevice.10.bc"
export XLA_FLAGS=--xla_gpu_cuda_data_dir="$ROOT_DIR/cuda_fix"
uv run main.py