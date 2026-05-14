#!/usr/bin/env python3
"""test_finite_scan.py — 유한 스캔 API 테스트

Usage: python test_finite_scan.py [ip] [port]
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
    SAMPLES  = 1024
    CH_MASK  = 0x0F   # 4채널

    # 01  scan_start
    rc = None
    try:
        prism.scan_start(CH_MASK, SAMPLES, ScanOptions.DEFAULT)
        rc = True
    except PrismError as e:
        rc = False
    check(1, "scan_start", rc is True)

    # 02  scan_status → RUNNING
    status, _ = prism.scan_status()
    check(2, "scan_status RUNNING", bool(status & ScanStatus.RUNNING))

    # 03  scan_ch_count
    n_ch = prism.scan_ch_count()
    check(3, "scan_ch_count", n_ch == 4, f"ch_count={n_ch}")

    # 04  scan_buf_size
    buf = prism.scan_buf_size()
    check(4, "scan_buf_size", buf >= SAMPLES, f"buf_size={buf}")

    # 05  scan_read
    data, status = prism.scan_read(SAMPLES, timeout=10.0)
    n_read = len(data) // n_ch
    check(5, "scan_read", n_read == SAMPLES, f"{n_read} samples/ch received")

    # 06  RUNNING 해제 → scan_cleanup
    stopped = wait_until_stopped(prism)
    check(6, "RUNNING 해제 대기", stopped)
    prism.scan_cleanup()

    # 07  scan_start 재시작
    try:
        prism.scan_start(CH_MASK, SAMPLES, ScanOptions.DEFAULT)
        check(7, "scan_start (재시작)", True)
    except PrismError as e:
        check(7, "scan_start (재시작)", False, str(e))

    # 08  scan_read_numpy → shape / dtype
    import numpy as np
    arr, status = prism.scan_read_numpy(SAMPLES, timeout=10.0)
    shape_ok = arr.shape == (SAMPLES, n_ch)
    dtype_ok = arr.dtype == np.float64
    check(8, "scan_read_numpy", shape_ok and dtype_ok,
          f"shape={arr.shape}  dtype={arr.dtype}")

    # 09  RUNNING 해제 → scan_cleanup
    stopped = wait_until_stopped(prism)
    check(9, "RUNNING 해제 대기 (2차)", stopped)
    prism.scan_cleanup()

    # 10  오류: RUNNING 중 scan_cleanup → RESULT_BUSY
    prism.scan_start(CH_MASK, 100_000, ScanOptions.CONTINUOUS)
    try:
        prism.scan_cleanup()
        check(10, "RUNNING 중 scan_cleanup → RESULT_BUSY", False, "예외 미발생")
    except PrismError as e:
        check(10, "RUNNING 중 scan_cleanup → RESULT_BUSY",
              e.result_code == RESULT_BUSY)
    finally:
        prism.scan_stop()
        wait_until_stopped(prism)
        prism.scan_cleanup()

    prism.close()
    total = _pass + _fail
    print(f"\nResults: {_pass}/{total} passed")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    ip   = sys.argv[1] if len(sys.argv) > 1 else "192.168.7.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 7777
    sys.exit(main(ip, port))
