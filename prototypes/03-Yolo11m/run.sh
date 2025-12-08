#!/bin/bash
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Use PyTorch's bundled cuDNN to avoid version conflicts
TORCH_LIB=$(uv run python -c "import torch, os; print(os.path.dirname(torch.__file__))")/lib
export LD_LIBRARY_PATH="$TORCH_LIB:$LD_LIBRARY_PATH"

uv run python main.py "$@"
