#!/usr/bin/env python3
"""continuous_scan.py — PRISM-PyLib example: continuous scan until Ctrl-C

Usage:
    python continuous_scan.py [ip] [port] [sample_rate]
    sample_rate: 0=64K 1=128K 3=256K 4=512K 5=32K 6=16K 7=8K 8=4K 9=2K 10=1K 11=500  (default: 0)
"""
import sys
import time
import signal
from math import sqrt
from sys import stdout
from prismlib import prismlib, ScanOptions, ScanStatus, sr_hz, sr_name


def calc_rms(data, channel, num_channels, num_samples_per_channel):
    value = 0.0
    index = channel
    for _ in range(num_samples_per_channel):
        value += (data[index] * data[index]) / num_samples_per_channel
        index += num_channels
    return sqrt(value)

stop_flag = False
def on_sigint(sig, frame):
    global stop_flag
    stop_flag = True
    print("\nStopping ...")

signal.signal(signal.SIGINT, on_sigint)

ip   = sys.argv[1] if len(sys.argv) > 1 else "192.168.7.1"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 7777
sr   = int(sys.argv[3]) if len(sys.argv) > 3 else 0

if sr_hz(sr) <= 0:
    print("sample_rate: 0=64K 1=128K 3=256K 4=512K 5=32K 6=16K 7=8K 8=4K 9=2K 10=1K 11=500", file=sys.stderr)
    sys.exit(1)

print(f"Connecting to {ip}:{port} ...")
prism = prismlib(ip, port)
prism.open()

use_iepe = False
try:
    prism.sampleRate_write(sr)

    for ch in range(4):
        prism.sens_write(ch, 100.0)

    ans = input("Enable IEPE? [y/n]: ").strip().lower()
    use_iepe = ans in ("y", "yes")
    if use_iepe:
        for ch in range(4):
            prism.iepe_write(ch, True)
        print("Waiting for IEPE to settle (2 s)...", end="", flush=True)
        time.sleep(2.0)
        print(" done")

    prism.scan_start(0x0F, 100_000, ScanOptions.CONTINUOUS)
    n_ch = prism.scan_ch_count()
    print(f"Scanning [{sr_name(sr)}] continuously. Press Ctrl-C to stop.")

    # Print header
    print(f"{'Samples Read':>14}{'Total':>14}", end="")
    for ch in range(n_ch):
        print(f"{'ch' + str(ch + 1) + ' RMS':>14}", end="")
    print()

    total  = 0
    status = ScanStatus.RUNNING

    while (status & ScanStatus.RUNNING) and not stop_flag:
        data, status = prism.scan_read(1000, timeout=1.0)

        if status & ScanStatus.BUFFER_OVERRUN:
            print("WARNING: buffer overrun!")
            break

        samples_read = len(data) // n_ch
        total += samples_read

        if samples_read > 0:
            print(f"\r{samples_read:>14}{total:>14}", end="")
            for ch in range(n_ch):
                print(f"{calc_rms(data, ch, n_ch, samples_read):>14.5f}", end="")
            stdout.flush()

    print()

finally:
    if prism.scan_status()[0] & ScanStatus.RUNNING:
        prism.scan_stop()
        while prism.scan_status()[0] & ScanStatus.RUNNING:
            time.sleep(0.005)
    prism.scan_cleanup()
    if use_iepe:
        for ch in range(4):
            prism.iepe_write(ch, False)
    prism.close()

print(f"Done. Total {total:,} samples/ch received.")
