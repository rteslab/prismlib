# PRISM Client Library

C/Python client library for PRISM devices.

## Directory layout

```
prismlib/
├── include/               Public C header
│   └── prismlib.h
├── lib/                   C library sources
│   ├── prismlib.c         API implementation (handle management, command I/O)
│   ├── scan.c             UDP/TCP receive thread and ring buffer
│   ├── protocol.c         Frame encoding/decoding
│   ├── transport.c        TCP/UDP socket utilities
│   └── led.c              LED control
├── python/
│   └── prismlib/          Python package
│       ├── prismlib_c.py  ctypes wrapper (public API class)
│       ├── constants.py   Enumerations and result codes
│       └── __init__.py    Package exports
├── examples/
│   ├── c/                 C examples
│   │   ├── finite_scan.c
│   │   ├── continuous_scan.c
│   │   └── fft_scan.c
│   └── python/            Python examples
│       ├── finite_scan.py
│       ├── continuous_scan.py
│       └── fft_scan.py
└── tools/
    ├── swupdate.py        Software update (firmware + prismlib)
    └── analyzer.py        PRISM ANALYZER - real-time waveform / spectrum analyzer GUI
```

## Communication

- **TCP port 7777**: command channel (request-response)
- **UDP port 7778**: scan data channel (device -> host, datagrams) - default

While a scan is running the device streams ADC data in the background and the
receive thread stores it in an internal ring buffer. `scan_read()` takes data
out of that buffer at any time.

### Scan data transport modes

The data channel is selected with the `scan_start()` options.

| Mode | Option | Data channel | Notes |
|------|--------|--------------|-------|
| **UDP** (default) | `OPTS_DEFAULT` / `OPTS_CONTINUOUS` | UDP 7778 | Loss is detectable through the chunk counter |
| **TCP** | add `OPTS_TCP_DATA` | TCP 7777 (shares the control socket) | Reliable transport, no extra socket |

UDP frame: `[cnt:2][CMD:1][n_samples:2][status:1][data...]`  
TCP frame: `[CMD:1][n_samples:2][status:1][data...]` (no counter)

## Connecting

The LAN port of the PRISM C100 is preconfigured as follows.

| Item | Value |
|------|-------|
| IP address | 192.168.0.10 |
| Subnet mask | 255.255.255.0 |

Set the PC to an address in the same subnet (e.g. 192.168.0.11) and connect over SSH.

```bash
ssh admin@192.168.0.10
```

The factory SSH credentials are listed below. **Change them before first use.**

| Item | Factory value |
|------|---------------|
| User | admin |
| Password | 1234 |

## Installation

Models with a built-in CM4 module, such as the PRISM C100, ship with the library
already installed; nothing needs to be done. For the network models (PRISM N
series) install the library on the machine that will use it as shown below. The
library is tested on Raspberry Pi OS on a CM4.

```bash
sudo bash ./install.sh
```

What gets installed:
- C shared library: `/usr/local/lib/libprismlib.so`
- C header: `/usr/local/include/prismlib.h`
- Python package: `pip install prismlib`
- UDP receive buffer tuning: `net.core.rmem_max=67108864` (applied now and made persistent in `/etc/sysctl.d/99-prism-udp.conf`)
- Update and uninstall tools in `/usr/local/share/prismlib/` (`prism-swupdate` command, `uninstall.sh`)

`install.sh` fetches its dependencies with apt, so the device needs internet access.
It installs prismlib only; update the firmware with `prism-swupdate` (see Software update).

### Offline installation (from a PC, `install_scp.bat`)

If the device has no internet connection, run `install_scp.bat` from this folder
on a **Windows PC that is online** (Windows 10 or later, PC and device on the same
LAN). It installs prismlib on the device **and updates the acquisition firmware**
from the newest release bundle in `release/`; the device reboots if the firmware
was written.

```bat
install_scp.bat                              :: device at admin@192.168.0.10
install_scp.bat admin@10.0.0.5               :: another address or account
install_scp.bat prism-release-1.1.tar.gz     :: firmware from this bundle
```

An existing installation is replaced after a confirmation (`y/N`). See "Offline
installation" in `doc/index.html` for details.

### UDP receive buffer tuning

To avoid UDP packet loss at high scan rates (512K sps and above) the kernel's
maximum socket receive buffer must be 64 MB. `install.sh` does this
automatically; to apply it by hand:

```bash
# Apply now (reset after reboot)
sudo sysctl -w net.core.rmem_max=67108864

# Keep it across reboots
echo "net.core.rmem_max=67108864" | sudo tee /etc/sysctl.d/99-prism-udp.conf
sudo sysctl --system
```

> Linux doubles the value requested with `setsockopt(SO_RCVBUF)` and caps it at `rmem_max`.  
> `transport` requests SO_RCVBUF = 32 MB when it creates the socket, so Linux applies 64 MB.

## Quick start

### C

```c
#include "prismlib.h"
#include <stdio.h>
#include <unistd.h>

int main(void)
{
    prismlib_t *dev = prismlib_open("192.168.7.1", 7777);
    if (!dev) return 1;

    prismlib_sens_write(dev, 0, 100.0);  /* 100 mV/g */
    prismlib_iepe_write(dev, 0, 1);
    sleep(2);                            /* let the IEPE supply settle */

    prismlib_scan_start(dev, 0x0F, 64000, OPTS_DEFAULT);

    static double buf[256000];   /* 4ch x 64000 samples */
    uint16_t status;
    uint32_t n_read, avail;
    prismlib_scan_read(dev, &status, 64000, 10.0, buf, 256000, &n_read);

    do { prismlib_scan_status(dev, &status, &avail); } while (status & STATUS_RUNNING);
    prismlib_scan_cleanup(dev);
    prismlib_iepe_write(dev, 0, 0);
    prismlib_close(dev);
    return 0;
}
```

Build:
```bash
gcc my_app.c -lprismlib -lpthread -lm -o my_app
```

### Python

```python
import time
from prismlib import prismlib, ScanOptions, ScanStatus

prism = prismlib("192.168.7.1", 7777)
prism.open()

prism.sens_write(0, 100.0)   # 100 mV/g
prism.iepe_write(0, True)
time.sleep(2.0)               # let the IEPE supply settle

prism.scan_start(0x0F, 64000, ScanOptions.DEFAULT)
data, status = prism.scan_read(64000, timeout=10.0)
# data: [ch1_s0, ch2_s0, ch3_s0, ch4_s0, ch1_s1, ...] (channel-interleaved)

while prism.scan_status()[0] & ScanStatus.RUNNING:
    pass
prism.scan_cleanup()
prism.iepe_write(0, False)
prism.close()
```

## Running the examples

| Example | Description |
|---------|-------------|
| `finite_scan` | Acquires the requested number of samples and saves them to a file |
| `continuous_scan` | Acquires continuously until Ctrl-C, showing per-channel RMS live |
| `fft_scan` | Finite scan followed by an FFT spectrum printout |

```bash
# C (built during installation)
cd examples/c/
./finite_scan 192.168.7.1 7777
./continuous_scan 192.168.7.1 7777
./fft_scan 192.168.7.1 7777

# Python
cd examples/python/
python3 finite_scan.py 192.168.7.1 7777
python3 continuous_scan.py 192.168.7.1 7777
python3 fft_scan.py 192.168.7.1 7777
```

## Software update

The firmware and prismlib are released together, as a tested combination, in a
**release bundle** (`prism-release-<version>.tar.gz`) in the **`release/`** folder
of this repository; `git pull` brings the latest. Run the update on the device
(CM4). No internet access is needed.

```bash
git pull                                                  # get the latest bundle
sudo prism-swupdate release/prism-release-1.1.tar.gz      # update
sudo prism-swupdate release/prism-release-1.1.tar.gz --force   # reinstall even if already this release
prism-swupdate --info                                     # show the installed versions
prism-swupdate --help                                     # all options
```

Parts already at the bundle's version are skipped; a downgrade asks first. The
folder prismlib was installed from (e.g. `~/prismlib`) is brought to the new
release as well; files you added there are kept.

On a device where prismlib was never installed, run the copy in the bundle:

```bash
tar xzf prism-release-1.1.tar.gz
sudo python3 prism-release-1.1/prismlib/tools/swupdate.py prism-release-1.1.tar.gz
```

> **Do not touch the power or the USB cable while the firmware is being
> updated.** It takes a few seconds; if power is lost in the middle the device
> will not boot. The new firmware runs after a reboot.

To uninstall: `sudo bash /usr/local/share/prismlib/uninstall.sh`.

## Tools

| Tool | Description |
|------|-------------|
| `tools/swupdate.py` | Software update - firmware + prismlib together from a release bundle (see above). Available as the `prism-swupdate` command after installation |
| `tools/analyzer.py` | **PRISM ANALYZER** - real-time waveform / FFT / 1/3-octave band analyzer GUI. `python3 tools/analyzer.py [ip] [port] [sample_rate]`. Its packages (`python3-tk`, `python3-matplotlib`) are installed by `install.sh` |

## API documentation

See `doc/index.html` for the full API description.

## Changelog

### v1.1 - prismlib 1.1.1, firmware 1.1.0

- **Firmware: input polarity corrected.** With the v1.0 firmware a positive voltage at the BNC was read as negative on all four channels.
- **Firmware: IEPE channel numbering corrected.** With the v1.0 firmware `iepe_write(0)` / `iepe_diag()` bit 0 acted on the connector labelled CH4. They now follow the front-panel labels, matching the scan channels.
- **Firmware: longer serial number.** `serial()` may return up to 31 characters (C: pass a buffer of at least 32 bytes). Stored calibration and serial data are kept across the update.
- **Lost-frame counter** - `scan_lost()` (C: `prismlib_scan_lost()`) returns the number of scan data frames that never arrived; `ScanStatus.DATA_LOST` (`STATUS_DATA_LOST`) is set at the same time.
- **11 sample rates** (512 kS/s to 500 S/s) with `sr_hz()`, `sr_name()`, `sr_supported()` (C: `prismlib_sampleRate_hz()`, `prismlib_sampleRate_name()`). The former 170 kS/s (value 2) is no longer supported.
- `scan_stop()` responds in 2 ms instead of 201 ms.
- **Software update** - `prism-swupdate` installs the firmware and prismlib together from a release bundle.
- **Offline installation** - `install_scp.bat` installs from a Windows PC when the device has no internet.
- **PRISM ANALYZER** (`tools/analyzer.py`) - real-time waveform / FFT / 1/3-octave band analyzer.
- Programs written for v1.0 run unchanged - the API only gained functions.

### v1.0 (2026-05-17)

- First release - C library, Python package, examples, API documentation.

## License

The PRISM Client Library is proprietary software of RTES. See `LICENSE` for the terms.

### Third-party software notice

This library uses the open-source software listed below. **None of it is
included in this distribution**; it is downloaded from each project's
distribution channel and installed on the system at installation time, and the
system's copy is used at run time.

| Software | License | How it is used |
|----------|---------|----------------|
| [libgpiod](https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git) | LGPL-2.1-or-later | **Dynamically linked** by `libprismlib.so` (LED / GPIO control) |
| libgpiod Python bindings (`python3-libgpiod`) | LGPL-2.1-or-later | Used by the Python package |
| libgpiod command-line tools (`gpiod`) | GPL-2.0-or-later | Run by the USB power service registered by `install.sh` |
| [NumPy](https://github.com/numpy/numpy) | BSD-3-Clause | Used by `scan_read_numpy()` and PRISM ANALYZER (`tools/analyzer.py`) |
| [Matplotlib](https://github.com/matplotlib/matplotlib) | Matplotlib License (PSF-based, BSD-compatible) | Plotting in PRISM ANALYZER |
| Tcl/Tk (`python3-tk`) | Tcl/Tk License (BSD-style) | PRISM ANALYZER window |

**libgpiod** is used in the manner described by LGPL-2.1 section 6(b): the
shared library already installed on the system is used at run time. Users are
therefore free to replace or upgrade the system's libgpiod.

The full license text and source code of each component are available at the
links above or, on the installed system, in `/usr/share/doc/<package>/copyright`.
