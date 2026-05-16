# PRISM Client Library

PRISM 장치용 C/Python 클라이언트 라이브러리입니다.

## 디렉터리 구조

```
prismlib/
├── include/               C 공개 헤더
│   └── prismlib.h
├── lib/                   C 라이브러리 소스
│   ├── prismlib.c         API 구현 (핸들 관리, 커맨드 송수신)
│   ├── scan.c             UDP/TCP 수신 스레드 및 링 버퍼
│   ├── protocol.c         프레임 인코딩/디코딩
│   ├── transport.c        TCP/UDP 소켓 유틸리티
│   └── led.c              LED 제어
├── python/
│   └── prismlib/          Python 패키지
│       ├── prismlib_c.py  ctypes 래퍼 (공개 API 클래스)
│       ├── constants.py   열거형 및 결과 코드 상수
│       └── __init__.py    패키지 익스포트
└── examples/
    ├── c/                 C 예제
    │   ├── finite_scan.c
    │   ├── continuous_scan.c
    │   └── fft_scan.c
    └── python/            Python 예제
        ├── finite_scan.py
        ├── continuous_scan.py
        └── fft_scan.py
```

## 통신 구조

- **TCP 포트 7777**: 커맨드 제어 채널 (요청–응답)
- **UDP 포트 7778**: 스캔 데이터 채널 (장치 → 호스트, 데이터그램) — 기본값

스캔 실행 중 장치는 ADC 데이터를 백그라운드로 전송하며,
수신 스레드가 내부 링 버퍼에 저장합니다. `scan_read()`로 언제든 버퍼에서 꺼낼 수 있습니다.

### 스캔 데이터 전송 모드

`scan_start()` 옵션으로 데이터 채널을 선택할 수 있습니다.

| 모드 | 옵션 | 데이터 채널 | 특징 |
|------|------|-------------|------|
| **UDP** (기본) | `OPTS_DEFAULT` / `OPTS_CONTINUOUS` | UDP 7778 | 청크 카운터로 손실 감지 가능 |
| **TCP** | `OPTS_TCP_DATA` 추가 | TCP 7777 (제어 소켓 공유) | 신뢰성 전송, 별도 소켓 불필요 |

UDP 프레임: `[cnt:2][CMD:1][n_samples:2][status:1][data...]`  
TCP 프레임: `[CMD:1][n_samples:2][status:1][data...]` (카운터 없음)

## 연결 

PRISM C100의 LAN 포트는 아래와 같이 초기화되어 있습니다.

| 항목 | 값 |
|------|----|
| IP 주소 | 192.168.0.10 |
| 서브넷 마스크 | 255.255.255.0 |

PC의 IP를 동일한 대역(예: 192.168.0.11)으로 설정한 후 SSH로 접속합니다.

```bash
ssh admin@192.168.0.10
```

초기 SSH 접속 정보는 아래와 같습니다. **최초 사용 전 반드시 변경하십시오.**

| 항목 | 초기값 |
|------|--------|
| ID | admin |
| 패스워드 | 1234 |

## 설치
PRISM C100 모델처럼 CM4 모듈이 내장된 모델은 출고시 라이브러리가 설치되어 있어 별도의 설치가 필요하지 않습니다. 네트워크 타입 모델(PRISM N 시리즈)의 경우 사용하실 장치에 아래와 같이 라이브러리를 설치 후 사용할 수 있습니다. 라이브러리는 CM4 기반 라즈비안에서 테스트되었습니다. 

```bash
sudo ./install.sh
```

설치 항목:
- C 공유 라이브러리: `/usr/local/lib/libprismlib.so`
- C 헤더: `/usr/local/include/prismlib.h`
- Python 패키지: `pip install prismlib`
- UDP 수신 버퍼 튜닝: `net.core.rmem_max=67108864` (즉시 적용 + `/etc/sysctl.d/99-prism-udp.conf` 영구 등록)

### UDP 수신 버퍼 튜닝

고속 스캔(512K sps 이상)에서 UDP 패킷 손실을 방지하려면 커널 최대 소켓 수신 버퍼를 64 MB로 설정해야 합니다.
`install.sh`가 자동으로 처리하며, 수동으로 적용하려면 아래 명령을 실행하세요.

```bash
# 즉시 적용 (재부팅 후 초기화됨)
sudo sysctl -w net.core.rmem_max=67108864

# 재부팅 후에도 유지
echo "net.core.rmem_max=67108864" | sudo tee /etc/sysctl.d/99-prism-udp.conf
sudo sysctl --system
```

> Linux는 `setsockopt(SO_RCVBUF)` 요청값을 2배로 실제 적용하고, `rmem_max`를 초과할 수 없습니다.  
> `transport`는 소켓 생성 시 SO_RCVBUF=32 MB를 요청하며, Linux가 2배인 64 MB를 실제 적용합니다.

## 빠른 시작

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
    sleep(2);                            /* IEPE 안정화 대기 */

    prismlib_scan_start(dev, 0x0F, 64000, OPTS_DEFAULT);

    static double buf[256000];   /* 4ch × 64000 samples */
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

컴파일:
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
time.sleep(2.0)               # IEPE 안정화 대기

prism.scan_start(0x0F, 64000, ScanOptions.DEFAULT)
data, status = prism.scan_read(64000, timeout=10.0)
# data: [ch1_s0, ch2_s0, ch3_s0, ch4_s0, ch1_s1, ...] (채널 인터리브)

while prism.scan_status()[0] & ScanStatus.RUNNING:
    pass
prism.scan_cleanup()
prism.iepe_write(0, False)
prism.close()
```

## 예제 실행

| 예제 | 설명 |
|------|------|
| `finite_scan` | 지정한 샘플 수만큼 수집 후 파일 저장 |
| `continuous_scan` | Ctrl-C까지 연속 수집, 채널별 RMS 실시간 표시 |
| `fft_scan` | 유한 스캔 후 FFT 스펙트럼 출력 |

```bash
# C (빌드 후 - 설치시 자동 빌드됨)
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

## API 문서

자세한 API 설명은 `doc/index.html`을 참조하세요.
