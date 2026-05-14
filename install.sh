#!/bin/bash
# PRISM library installer — mirrors MCC daqhats install.sh style

set -e

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (sudo ./install.sh)"
    exit 1
fi

echo "Installing PRISM C library..."
make install

echo "Installing numpy (required for ring buffer)..."
pip install numpy --break-system-packages 2>/dev/null || pip install numpy

echo "Installing PRISM Python library..."
pip install ./python --break-system-packages 2>/dev/null || pip install ./python

echo ""
echo "PRISM library installed successfully."
echo "  C header : /usr/local/include/prismc.h"
echo "  C library: /usr/local/lib/libprismc.so"
echo "  Python   : pip show prism"
