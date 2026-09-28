#!/bin/sh
# PRISM library uninstaller - the mirror of install.sh.
#
# Removes everything install.sh installed, in reverse order.  Runs from the
# source folder or from its installed copy:
#
#     sudo bash ./uninstall.sh
#     sudo bash /usr/local/share/prismlib/uninstall.sh
#
# The source folder itself is left alone.  The apt packages stay too - other
# software may depend on them.

set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (sudo ./uninstall.sh)"
    exit 1
fi

echo "Removing RUN(ACT) LED access rule..."
rm -f /etc/udev/rules.d/99-prism-led.rules
if [ -e /sys/class/leds/ACT/trigger ]; then
    chgrp root /sys/class/leds/ACT/trigger /sys/class/leds/ACT/brightness 2>/dev/null || true
    chmod 644 /sys/class/leds/ACT/trigger /sys/class/leds/ACT/brightness 2>/dev/null || true
fi

echo "Removing GPIO13 USB power control (udev + systemd)..."
systemctl stop prism-usb-power.service 2>/dev/null || true
rm -f /etc/systemd/system/prism-usb-power.service
rm -f /etc/udev/rules.d/99-prism-usb-power.rules

echo "Removing UDP receive buffer tuning..."
# (the running value returns to the kernel default at the next reboot)
rm -f /etc/sysctl.d/99-prism-udp.conf

udevadm control --reload-rules
systemctl daemon-reload

echo "Uninstalling PRISM Python library..."
pip uninstall -y prismlib --break-system-packages --root-user-action=ignore

echo "Uninstalling PRISM C library..."
rm -f /usr/local/lib/libprismlib.so /usr/local/include/prismlib.h
ldconfig

# Build output, when run from a source folder
if [ -f Makefile ]; then
    echo "Cleaning build artifacts..."
    make clean >/dev/null 2>&1 || true
fi

# The tools install.sh placed outside the source folder, and the install record
echo "Removing the update/uninstall tools and the install record..."
rm -f /usr/local/bin/prism-swupdate
rm -rf /usr/local/share/prismlib

echo ""
echo "PRISM library uninstalled."
