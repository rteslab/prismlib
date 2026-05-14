#!/usr/bin/env python3
"""test_device.py — 장치 연결 / 기본 정보 테스트

Usage: python test_device.py [ip] [port]
"""
import sys
from prism import Prism, PrismError

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

    # 01  open
    prism.open()
    check(1, "open", prism.is_open())

    # 02  fw_version
    ver = prism.fw_version()
    check(2, "fw_version", len(ver) > 0, f'"{ver}"')

    # 03  serial
    sn = prism.serial()
    check(3, "serial", len(sn) > 0, f'"{sn}"')

    # 04  info
    inf    = prism.info()
    ok_key = all(k in inf for k in ("num_ai_channels", "ai_min_code",
                                    "ai_max_code", "ai_min_voltage", "ai_max_voltage"))
    ok_rng = inf.get("ai_min_code", 0) < inf.get("ai_max_code", 0)
    detail = (f"num_ch={inf.get('num_ai_channels')}  "
              f"min_code={inf.get('ai_min_code')}  "
              f"max_code={inf.get('ai_max_code')}")
    check(4, "info", ok_key and ok_rng, detail)

    # 05  close
    prism.close()
    check(5, "close", not prism.is_open())

    total = _pass + _fail
    print(f"\nResults: {_pass}/{total} passed")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    ip   = sys.argv[1] if len(sys.argv) > 1 else "192.168.7.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 7777
    sys.exit(main(ip, port))
