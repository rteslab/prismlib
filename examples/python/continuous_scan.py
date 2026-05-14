#!/usr/bin/env python3
"""continuous_scan.py — PRISM-PyLib example: continuous scan until Ctrl-C

Usage:
    python continuous_scan.py [ip] [port]
    python continuous_scan.py 192.168.7.1 7777
"""
import sys
import time
import signal
from prism import Prism, ScanOptions, ScanStatus

stop_flag = False
def on_sigint(sig, frame):
    global stop_flag
    stop_flag = True
    print("\nStopping ...")

signal.signal(signal.SIGINT, on_sigint)

ip   = sys.argv[1] if len(sys.argv) > 1 else "192.168.7.1"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 7777

print(f"Connecting to {ip}:{port} ...")
prism = Prism(ip, port)
prism.open()

try:
    for ch in range(4):
        prism.iepe_write(ch, True)
        prism.sens_write(ch, 100.0)

    prism.scan_start(0x0F, 100_000, ScanOptions.CONTINUOUS)
    print("Scanning continuously. Press Ctrl-C to stop.")

    total  = 0
    status = ScanStatus.RUNNING

    while (status & ScanStatus.RUNNING) and not stop_flag:
        data, status = prism.scan_read(1000, timeout=1.0)

        if status & ScanStatus.BUFFER_OVERRUN:
            print("WARNING: buffer overrun!")
            break

        n_ch   = prism.scan_ch_count()
        total += len(data) // n_ch
        print(f"\r  Received: {total:,} samples/ch    ", end="", flush=True)

    print()

    # 스캔이 아직 실행 중이면 (Ctrl-C 또는 overrun break) 중단 후 스레드 종료 대기.
    # scan_stop()은 비동기(서버 응답 없음)이므로 RUNNING 해제까지 polling 필요.
    if prism.scan_status()[0] & ScanStatus.RUNNING:
        prism.scan_stop()
        while prism.scan_status()[0] & ScanStatus.RUNNING:
            time.sleep(0.005)

finally:
    prism.scan_cleanup()
    prism.close()

print(f"Done. Total {total:,} samples/ch received.")
