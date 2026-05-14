#!/usr/bin/env python3
"""test_config.py — 설정 API 테스트 (cal / iepe / sens / clock)

Usage: python test_config.py [ip] [port]
"""
import sys
from prism import Prism, PrismError, RESULT_BAD_PARAMETER

_pass = 0
_fail = 0

def check(num: int, name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    if cond:
        _pass += 1;  tag = "[PASS]"
    else:
        _fail += 1;  tag = "[FAIL]"
    print(f"{tag} {num:02d} {name}" + (f" : {detail}" if detail else ""))


def main(ip: str, port: int) -> int:
    prism = Prism(ip, port)
    prism.open()

    # ── Calibration ─────────────────────────────────────────────────────────

    # 01  cal_date
    date = prism.cal_date()
    ok   = len(date) == 10 and date[4] == "-" and date[7] == "-"
    check(1, "cal_date", ok, f'"{date}"')

    # 02~05  cal_read (4채널)
    orig_cal = []
    for ch in range(4):
        slope, offset = prism.cal_read(ch)
        orig_cal.append((slope, offset))
        check(2 + ch, f"cal_read({ch})", slope > 0.0,
              f"slope={slope:.6f}  offset={offset:.6f}")

    # 06  cal_write → 로컬 저장 확인 (private attr 직접 검증)
    prism.cal_write(0, 1.5, 0.1)
    ok = abs(prism._cal_slope[0] - 1.5) < 1e-9 and abs(prism._cal_offset[0] - 0.1) < 1e-9
    check(6, "cal_write(0, 1.5, 0.1)", ok, "slope=1.5  offset=0.1")
    prism.cal_write(0, orig_cal[0][0], orig_cal[0][1])  # 복원

    # ── IEPE ────────────────────────────────────────────────────────────────

    # 07  iepe_read
    iepe0 = prism.iepe_read(0)
    check(7, "iepe_read(0)", isinstance(iepe0, bool), f"ch0={iepe0}")

    # 08  iepe_write True → read back
    prism.iepe_write(0, True)
    check(8, "iepe_write(0, True)  → iepe_read", prism.iepe_read(0) is True, "ch0=True")

    # 09  iepe_write False → read back
    prism.iepe_write(0, False)
    check(9, "iepe_write(0, False) → iepe_read", prism.iepe_read(0) is False, "ch0=False")

    # ── Sensitivity ─────────────────────────────────────────────────────────

    # 10  sens_read 기본값
    sv = prism.sens_read(0)
    check(10, "sens_read(0) default", abs(sv - 1000.0) < 1e-6, f"ch0={sv} mV/unit")

    # 11  sens_write → read back
    prism.sens_write(0, 100.0)
    sv2 = prism.sens_read(0)
    check(11, "sens_write(0, 100.0) → sens_read", abs(sv2 - 100.0) < 1e-6,
          f"ch0={sv2} mV/unit")
    prism.sens_write(0, 1000.0)  # 복원

    # 12  sens_write 오류: 음수 값
    try:
        prism.sens_write(0, -1.0)
        check(12, "sens_write(0, -1.0) → PrismError", False, "예외 미발생")
    except PrismError as e:
        check(12, "sens_write(0, -1.0) → PrismError",
              e.result_code == RESULT_BAD_PARAMETER)

    # ── Clock ────────────────────────────────────────────────────────────────

    # 13  clock_read
    rate = prism.clock_read()
    check(13, "clock_read", rate > 0.0, f"{rate:.1f} S/s")

    # 14  clock_write 51200 → read back
    prism.clock_write(51200.0)
    r2 = prism.clock_read()
    check(14, "clock_write(51200.0) → clock_read", abs(r2 - 51200.0) / 51200.0 < 0.01,
          f"{r2:.1f} S/s")

    # 15  clock_write 64000 → 복원
    prism.clock_write(64000.0)
    r3 = prism.clock_read()
    check(15, "clock_write(64000.0) → clock_read", abs(r3 - 64000.0) / 64000.0 < 0.01,
          f"{r3:.1f} S/s")

    prism.close()
    total = _pass + _fail
    print(f"\nResults: {_pass}/{total} passed")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    ip   = sys.argv[1] if len(sys.argv) > 1 else "192.168.7.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 7777
    sys.exit(main(ip, port))
