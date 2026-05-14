#!/usr/bin/env python3
"""test_continuous_scan.py — 연속 스캔 API 테스트

Usage: python test_continuous_scan.py [ip] [port]
"""
import sys
import time
from prism import Prism, PrismError, ScanOptions, ScanStatus, RESULT_BUSY

_pass = 0
_fail = 0

def check(num: int, name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    if cond:
        _pass += 1;  tag = "[PASS]"
    else:
        _fail += 1;  tag = "[FAIL]"
    print(f"{tag} {num:02d} {name}" + (f" : {detail}" if detail else ""))


def wait_until_stopped(prism: Prism, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not (prism.scan_status()[0] & ScanStatus.RUNNING):
            return True
        time.sleep(0.005)
    return False


def main(ip: str, port: int) -> int:
    prism = Prism(ip, port)
    prism.open()
    CH_MASK = 0x0F   # 4채널

    # 01  scan_start CONTINUOUS
    try:
        prism.scan_start(CH_MASK, 100_000, ScanOptions.CONTINUOUS)
        check(1, "scan_start CONTINUOUS", True)
    except PrismError as e:
        check(1, "scan_start CONTINUOUS", False, str(e))
        prism.close()
        return 1

    # 02  scan_status → RUNNING
    status, _ = prism.scan_status()
    check(2, "scan_status RUNNING", bool(status & ScanStatus.RUNNING))

    n_ch = prism.scan_ch_count()

    # 03  scan_read × 3회 반복 수신
    total = 0
    ok    = True
    for i in range(3):
        data, status = prism.scan_read(1000, timeout=2.0)
        n = len(data) // n_ch
        if n == 0:
            ok = False
            break
        total += n
    check(3, "scan_read × 3 수신", ok, f"1000 samples/ch × 3  (total={total})")

    # 04  scan_read_numpy → shape / dtype
    import numpy as np
    arr, status = prism.scan_read_numpy(1000, timeout=2.0)
    shape_ok = arr.shape == (1000, n_ch)
    dtype_ok = arr.dtype == np.float64
    check(4, "scan_read_numpy", shape_ok and dtype_ok,
          f"shape={arr.shape}  dtype={arr.dtype}")

    # 05  오류: RUNNING 중 scan_cleanup → RESULT_BUSY
    try:
        prism.scan_cleanup()
        check(5, "RUNNING 중 scan_cleanup → RESULT_BUSY", False, "예외 미발생")
    except PrismError as e:
        check(5, "RUNNING 중 scan_cleanup → RESULT_BUSY",
              e.result_code == RESULT_BUSY)

    # 06  scan_stop
    try:
        prism.scan_stop()
        check(6, "scan_stop", True)
    except PrismError as e:
        check(6, "scan_stop", False, str(e))

    # 07  RUNNING 해제 대기 (2초 이내)
    stopped = wait_until_stopped(prism, timeout=2.0)
    check(7, "RUNNING 해제 (2초 이내)", stopped)

    # 08  scan_cleanup
    try:
        prism.scan_cleanup()
        check(8, "scan_cleanup", True)
    except PrismError as e:
        check(8, "scan_cleanup", False, str(e))

    prism.close()
    total_tests = _pass + _fail
    print(f"\nResults: {_pass}/{total_tests} passed")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    ip   = sys.argv[1] if len(sys.argv) > 1 else "192.168.7.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 7777
    sys.exit(main(ip, port))
