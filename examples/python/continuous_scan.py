#!/usr/bin/env python3
"""continuous_scan.py — PRISM-PyLib example: continuous scan until Ctrl-C

Usage:
    python continuous_scan.py [ip] [port] [sample_rate]
    sample_rate: 0=64K 1=128K 2=170K 3=256K 4=512K  (default: 0)
"""
import sys
import time
import signal
from math import sqrt
from sys import stdout
from prismlib import prismlib, ScanOptions, ScanStatus


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

_SR_NAME = ["64K", "128K", "170K", "256K", "512K"]
if not (0 <= sr <= 4):
    print("sample_rate: 0=64K 1=128K 2=170K 3=256K 4=512K", file=sys.stderr)
    sys.exit(1)

print(f"Connecting to {ip}:{port} ...")
prism = prismlib(ip, port)
prism.open()

use_iepe = False
try:
    prism.sampleRate_write(sr)

    for ch in range(4):
        prism.sens_write(ch, 100.0)

    ans = input("IEPE를 활성화하시겠습니까? [y/n]: ").strip().lower()
    use_iepe = ans in ("y", "yes")
    if use_iepe:
        for ch in range(4):
            prism.iepe_write(ch, True)
        print("IEPE 안정화 대기 중 (2초)...", end="", flush=True)
        time.sleep(2.0)
        print(" 완료")

    prism.scan_start(0x0F, 100_000, ScanOptions.CONTINUOUS)
    n_ch = prism.scan_ch_count()
    print(f"Scanning [{_SR_NAME[sr]}] continuously. Press Ctrl-C to stop.")

    # 헤더 출력
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
