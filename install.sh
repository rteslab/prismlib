#!/bin/sh
# PRISM library installer

set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (sudo ./install.sh)"
    exit 1
fi

echo "Checking gpiod..."
if ! command -v gpioset > /dev/null 2>&1; then
    echo "  Installing gpiod tools..."
    apt-get install -y gpiod
else
    echo "  gpiod tools: OK"
fi
if ! dpkg -s libgpiod-dev > /dev/null 2>&1; then
    echo "  Installing libgpiod-dev (C library headers)..."
    apt-get install -y libgpiod-dev
else
    echo "  libgpiod-dev: OK"
fi
if ! python3 -c "import gpiod" > /dev/null 2>&1; then
    echo "  Installing python3-gpiod..."
    apt-get install -y python3-gpiod
else
    echo "  python3-gpiod: OK"
fi

echo "Building PRISM C library..."
make

echo "Building examples..."
make examples

echo "Installing PRISM C library..."
make install

echo "Installing numpy (required for ring buffer)..."
pip install numpy --break-system-packages --root-user-action=ignore

echo "Installing PRISM Python library..."
pip install ./python --break-system-packages --root-user-action=ignore

echo "Cleaning build artifacts..."
rm -rf ./python/build ./python/prismlib.egg-info

echo "Tuning UDP receive buffer (net.core.rmem_max=67108864, 64MB)..."
sysctl -w net.core.rmem_max=67108864
echo "net.core.rmem_max=67108864" > /etc/sysctl.d/99-prism-udp.conf
echo "  /etc/sysctl.d/99-prism-udp.conf written (persistent across reboots)"

echo "Installing GPIO13 USB power control (udev + systemd)..."

# systemd service: gpioset 프로세스를 유지하여 GPIO13 HIGH 지속
cat > /etc/systemd/system/prism-usb-power.service << 'EOF'
[Unit]
Description=PRISM USB Power (GPIO13 HIGH)
After=sysinit.target

[Service]
Type=simple
ExecStart=/usr/bin/gpioset -c 0 13=1
Restart=on-failure
RestartSec=1
EOF

# udev rule: USB2514B 허브 인식 시 systemd service 시작
cat > /etc/udev/rules.d/99-prism-usb-power.rules << 'EOF'
# Start prism-usb-power.service when USB2514B hub is recognized.
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="0424", ATTR{idProduct}=="2514", RUN+="/bin/systemctl --no-block start prism-usb-power.service"
EOF

udevadm control --reload-rules
systemctl daemon-reload
echo "  udev rule   : /etc/udev/rules.d/99-prism-usb-power.rules"
echo "  systemd     : prism-usb-power.service (started on hub detection)"

echo ""
echo "PRISM library installed successfully."
echo "  C header   : /usr/local/include/prismlib.h"
echo "  C library  : /usr/local/lib/libprismlib.so"
echo "  C examples : examples/c/"
echo "  Python     : pip show prismlib"
echo "  GPIO13     : AUTO (HIGH after USB hub recognized on each boot)"
