#!/bin/sh
# PRISM library installer

set -e

# Required packages - everything a freshly imaged CM4 may lack.  Packages that
# are already present are skipped by apt, so the list is deliberately complete:
#   build-essential                          gcc + make: C library and examples
#   python3-pip python3-setuptools python3-wheel
#                                            pip install of the Python package (no network)
#   gpiod libgpiod-dev python3-libgpiod      GPIO13 USB power, RUN LED
#   python3-numpy                            Python examples, PRISM ANALYZER
#   python3-tk python3-matplotlib            PRISM ANALYZER (tools/analyzer.py)
#   iperf3                                   Ethernet throughput check (LAN/WAN) -
#                                            installed as a client tool only, no service
# install_scp.bat reads the next line - keep it on one line.
APT_PACKAGES="build-essential python3-pip python3-setuptools python3-wheel gpiod libgpiod-dev python3-libgpiod python3-numpy python3-tk python3-matplotlib iperf3"

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (sudo ./install.sh)"
    exit 1
fi

# Downloaded package files are only needed during the install. Remove them
# when the script ends, whether it succeeded or stopped on an error: debs/
# carried here by install_scp.bat, and the .deb files apt keeps in its cache.
cleanup_packages() {
    rm -rf debs
    apt-get clean > /dev/null 2>&1 || true
}
trap cleanup_packages EXIT

# Packages come from debs/ when the device has no internet (install_scp.bat
# downloads them on the PC and carries them here), otherwise from apt.
echo "Installing required packages..."
# iperf3 asks during install whether to run a server at boot. Answer "no" up
# front so the install never stops at a prompt and no daemon is left listening.
export DEBIAN_FRONTEND=noninteractive
echo "iperf3 iperf3/start_daemon boolean false" | debconf-set-selections 2>/dev/null || true
if ls debs/*.deb > /dev/null 2>&1; then
    echo "  from debs/ (offline)"
    dpkg -i debs/*.deb || apt-get -y -f install
else
    echo "  from apt: $APT_PACKAGES"
    apt-get install -y $APT_PACKAGES
fi

echo "Building PRISM C library..."
make

echo "Building examples..."
make examples

echo "Installing PRISM C library..."
make install

# Offline install: dependencies come from apt above, pip stays off the network.
echo "Installing PRISM Python library..."
pip install ./python --break-system-packages --root-user-action=ignore \
    --no-build-isolation --no-deps --no-index

echo "Cleaning build artifacts..."
rm -rf ./python/build ./python/prismlib.egg-info
# Build intermediates do not stay in this folder: the object files, their
# dependency files and the library built here. The library that is used is the
# installed copy in /usr/local/lib - the examples find it there, and
# "make examples" rebuilds whatever it needs.
rm -f lib/*.o lib/*.d libprismlib.so examples/c/*.d

echo "Tuning UDP receive buffer (net.core.rmem_max=67108864, 64MB)..."
sysctl -w net.core.rmem_max=67108864
echo "net.core.rmem_max=67108864" > /etc/sysctl.d/99-prism-udp.conf
echo "  /etc/sysctl.d/99-prism-udp.conf written (persistent across reboots)"

echo "Installing GPIO13 USB power control (udev + systemd)..."

# systemd service: keeps a gpioset process alive so GPIO13 stays HIGH
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

# udev rule: starts the service when the USB2514B hub is detected
cat > /etc/udev/rules.d/99-prism-usb-power.rules << 'EOF'
# Start prism-usb-power.service when USB2514B hub is recognized.
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="0424", ATTR{idProduct}=="2514", RUN+="/bin/systemctl --no-block start prism-usb-power.service"
EOF

echo "Granting RUN(ACT) LED access to the gpio group (udev)..."

# Give the gpio group write access to /sys/class/leds/ACT/{trigger,brightness}
# so applications can drive the RUN LED without root.
cat > /etc/udev/rules.d/99-prism-led.rules << 'EOF'
# Allow the gpio group to control the RUN(ACT) LED.
ACTION=="add", SUBSYSTEM=="leds", KERNEL=="ACT", RUN+="/bin/sh -c 'chgrp gpio /sys/class/leds/ACT/trigger /sys/class/leds/ACT/brightness; chmod 664 /sys/class/leds/ACT/trigger /sys/class/leds/ACT/brightness'"
EOF

# Apply once now as well (the LED is already registered).
if [ -e /sys/class/leds/ACT/trigger ]; then
    chgrp gpio /sys/class/leds/ACT/trigger /sys/class/leds/ACT/brightness 2>/dev/null || true
    chmod 664 /sys/class/leds/ACT/trigger /sys/class/leds/ACT/brightness 2>/dev/null || true
fi

udevadm control --reload-rules
systemctl daemon-reload
echo "  udev rule   : /etc/udev/rules.d/99-prism-usb-power.rules"
echo "  udev rule   : /etc/udev/rules.d/99-prism-led.rules"
echo "  systemd     : prism-usb-power.service (started on hub detection)"

# The update and uninstall tools live outside this folder, so the folder (or
# the unpacked bundle) is not needed afterwards.
echo "Installing the update and uninstall tools..."
mkdir -p /usr/local/share/prismlib
install -m 755 uninstall.sh      /usr/local/share/prismlib/uninstall.sh
install -m 755 tools/swupdate.py /usr/local/share/prismlib/swupdate.py
ln -sf /usr/local/share/prismlib/swupdate.py /usr/local/bin/prism-swupdate

# Record what was installed - version and a hash of the sources in this folder,
# computed the same way as for a release bundle. Without it prism-swupdate (and
# the EOL tester) cannot tell that this is already the bundle's prismlib and
# install it again.
echo "Recording the installed prismlib..."
python3 tools/swupdate.py --record-install "$(pwd)" || echo "  (could not write the install record)"

echo ""
echo "PRISM library installed successfully."
echo "  C header   : /usr/local/include/prismlib.h"
echo "  C library  : /usr/local/lib/libprismlib.so"
echo "  C examples : examples/c/"
echo "  Python     : pip show prismlib"
echo "  GPIO13     : AUTO (HIGH after USB hub recognized on each boot)"
echo "  Update     : sudo prism-swupdate <prism-release-x.y.tar.gz>"
echo "               sudo prism-swupdate <prism-release-x.y.tar.gz> --force"
echo "                 (reinstall prismlib and rewrite the firmware even if already that release)"
echo "               prism-swupdate --info        (show what is installed)"
echo "               prism-swupdate --help        (all options)"
echo "  Uninstall  : sudo bash /usr/local/share/prismlib/uninstall.sh"
echo ""
echo "This installed prismlib only. The measurement unit's firmware is updated by"
echo "prism-swupdate with a release bundle (install_scp.bat does both from a PC)."
