#!/usr/bin/env python3
"""finite_scan.py — PRISM-PyLib example: finite scan (ch1~ch4, N samples)

Usage:
    python finite_scan.py [ip] [port] [samples]
    python finite_scan.py 192.168.7.1 7777 1024
"""
import sys
import time
import datetime
from prismlib import prismlib, ScanOptions, ScanStatus

NUM_CHANNELS = 4

ip      = sys.argv[1] if len(sys.argv) > 1 else "192.168.7.1"
port    = int(sys.argv[2]) if len(sys.argv) > 2 else 7777
SAMPLES = int(sys.argv[3]) if len(sys.argv) > 3 else 64000

print(f"Connecting to {ip}:{port} ...")
prism = prismlib(ip, port)
prism.open()

use_iepe = False
try:
    print(f"FW version : {prism.fw_version()}")
    print(f"Serial     : {prism.serial()}")

    for ch in range(NUM_CHANNELS):
        prism.sens_write(ch, 100.0)   # 100 mV/g accelerometer

    ans = input("Enable IEPE? [y/n]: ").strip().lower()
    use_iepe = ans in ("y", "yes")
    if use_iepe:
        for ch in range(NUM_CHANNELS):
            prism.iepe_write(ch, True)
        print("Waiting for IEPE to settle (2 s)...", end="", flush=True)
        time.sleep(2.0)
        print(" done")

    prism.scan_start(0x0F, SAMPLES, ScanOptions.DEFAULT)

    data, status = prism.scan_read(SAMPLES, timeout=10.0)

    # Wait for the receive thread to clear RUNNING.  scan_read() can return
    # before the thread clears the flag, so check before scan_cleanup().
    while prism.scan_status()[0] & ScanStatus.RUNNING:
        time.sleep(0.005)

    n_sets = len(data) // NUM_CHANNELS
    print(f"Read {n_sets} samples/ch")
    for i in range(min(n_sets, 5)):
        vals = data[i * NUM_CHANNELS:(i + 1) * NUM_CHANNELS]
        print(f"[{i:4d}] " + "  ".join(f"ch{c+1}={vals[c]:8.4f} g"
                                        for c in range(NUM_CHANNELS)))
    if n_sets > 5:
        print(f"  ... ({n_sets - 5} more)")

    fname = datetime.datetime.now().strftime("finite_scan_%Y%m%d_%H%M%S.txt")
    with open(fname, "w") as f:
        f.write("sample\t" + "\t".join(f"ch{c+1}(g)" for c in range(NUM_CHANNELS)) + "\n")
        for i in range(n_sets):
            vals = data[i * NUM_CHANNELS:(i + 1) * NUM_CHANNELS]
            f.write(f"{i}\t" + "\t".join(f"{v:.6f}" for v in vals) + "\n")
    print(f"Saved: {fname}")

finally:
    prism.scan_cleanup()
    if use_iepe:
        for ch in range(NUM_CHANNELS):
            prism.iepe_write(ch, False)
    prism.close()
