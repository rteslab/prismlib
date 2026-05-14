#!/bin/bash
# PRISM library uninstaller

set -e

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (sudo ./uninstall.sh)"
    exit 1
fi

echo "Uninstalling PRISM C library..."
make uninstall

echo "Uninstalling PRISM Python library..."
pip uninstall -y prism 2>/dev/null || true

echo ""
echo "PRISM library uninstalled."
