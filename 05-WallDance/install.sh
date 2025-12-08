#!/bin/bash
cd "$(dirname "${BASH_SOURCE[0]}")"

uv sync

echo "Installation complete!"
echo "Run ./run.sh to start WallDance pose detection"
