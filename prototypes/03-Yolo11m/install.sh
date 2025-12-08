#!/bin/bash
cd "$(dirname "${BASH_SOURCE[0]}")"

# Create venv and install dependencies
uv sync

echo "Installation complete!"
echo "Run ./run.sh to start pose detection"
