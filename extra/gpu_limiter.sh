#!/bin/bash
# GPU Power Limiter for WallDance
# Limits RTX 3090 power to prevent PSU overload on 750W systems
#
# Usage: sudo ./extra/gpu_limiter.sh [power_limit_watts]
# Default: 280W (safe for 750W PSU with RTX 3090)
#
# Run this before starting WallDance if you experience system shutdowns

POWER_LIMIT=${1:-280}

echo "=== GPU Power Limiter ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script requires root privileges."
    echo "Usage: sudo $0 [power_limit_watts]"
    exit 1
fi

# Check current power settings
echo "Current GPU Power Settings:"
nvidia-smi -q -d POWER | grep -E "(Power Draw|Power Limit)" | head -6
echo ""

# Get current and max limits
CURRENT_LIMIT=$(nvidia-smi --query-gpu=power.limit --format=csv,noheader,nounits | head -1 | tr -d ' ')
MAX_LIMIT=$(nvidia-smi --query-gpu=power.max_limit --format=csv,noheader,nounits | head -1 | tr -d ' ')
MIN_LIMIT=$(nvidia-smi --query-gpu=power.min_limit --format=csv,noheader,nounits | head -1 | tr -d ' ')

echo "Current limit: ${CURRENT_LIMIT}W"
echo "Valid range: ${MIN_LIMIT}W - ${MAX_LIMIT}W"
echo "Requested: ${POWER_LIMIT}W"
echo ""

# Validate requested limit
if [ "$POWER_LIMIT" -lt "${MIN_LIMIT%.*}" ] || [ "$POWER_LIMIT" -gt "${MAX_LIMIT%.*}" ]; then
    echo "Error: Power limit must be between ${MIN_LIMIT}W and ${MAX_LIMIT}W"
    exit 1
fi

# Apply limit
echo "Applying power limit..."
nvidia-smi -pl "$POWER_LIMIT"

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Power limit set to ${POWER_LIMIT}W"
    echo ""
    echo "New GPU Power Settings:"
    nvidia-smi --query-gpu=power.limit,power.draw --format=csv
    echo ""
    echo "Note: This setting resets on reboot."
    echo "To make permanent, add to /etc/rc.local or create a systemd service."
else
    echo ""
    echo "✗ Failed to set power limit"
    exit 1
fi