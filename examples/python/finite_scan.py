#!/usr/bin/env python3
"""finite_scan.py — PRISM-PyLib example: finite scan (ch0~ch3, 1024 samples)

Usage:
    python finite_scan.py [ip] [port]
    python finite_scan.py 192.168.7.1 7777
"""
import sys
import time
from prism import Prism, ScanOptions, ScanStatus

NUM_CHANNELS = 4
SAMPLES      = 1024

ip   = sys.argv[1] if len(sys.argv) > 1 else "192.168.7.1"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 7777

print(f"Connecting to {ip}:{port} ...")
prism = Prism(ip, port)
prism.open()

try:
    print(f"FW version : {prism.fw_version()}")
    print(f"Serial     : {prism.serial()}")

    for ch in range(NUM_CHANNELS):
        prism.iepe_write(ch, True)
        prism.sens_write(ch, 100.0)   # 100 mV/g accelerometer

    prism.scan_start(0x0F, SAMPLES, ScanOptions.DEFAULT)

    data, status = prism.scan_read(SAMPLES, timeout=10.0)

    # 유한 스캔 완료 후 수신 스레드가 RUNNING 플래그를 해제할 때까지 대기.
    # scan_read() 반환 시점과 스레드 ~RUNNING 설정 사이에 race condition이 있으므로
    # scan_cleanup() 호출 전에 반드시 확인한다.
    while prism.scan_status()[0] & ScanStatus.RUNNING:
        time.sleep(0.005)

    n_sets = len(data) // NUM_CHANNELS
    print(f"Read {n_sets} samples/ch")
    for i in range(min(n_sets, 5)):
        vals = data[i * NUM_CHANNELS:(i + 1) * NUM_CHANNELS]
        print(f"[{i:4d}] " + "  ".join(f"ch{c}={vals[c]:8.4f} g"
                                        for c in range(NUM_CHANNELS)))
    if n_sets > 5:
        print(f"  ... ({n_sets - 5} more)")

finally:
    prism.scan_cleanup()
    prism.close()
