# prismlib_c.py — ctypes binding to libprismlib.so
#
# 스캔 수신 스레드 및 링 버퍼는 C(pthread)로 동작하므로 Python GIL과 무관하게
# 고속(512kSPS)에서도 버퍼 오버런 없이 동작한다.
#
# libprismlib.so 검색 순서:
#   1. 이 파일 기준 ../../.. (prismlib/ 루트)
#   2. /usr/local/lib, /usr/lib
#   3. ldconfig 경로 (ctypes.util.find_library)
import ctypes
import ctypes.util
import os

import numpy as np

from .constants import ScanStatus, ScanOptions

# ── 라이브러리 로드 ────────────────────────────────────────────────────────────

_SO_NAME = 'libprismlib.so'
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

def _load_lib() -> ctypes.CDLL:
    search = [
        os.path.join(_THIS_DIR, '../../..'),   # prismlib/ 루트
        '/usr/local/lib',
        '/usr/lib',
    ]
    for d in search:
        path = os.path.realpath(os.path.join(d, _SO_NAME))
        if os.path.isfile(path):
            return ctypes.CDLL(path)
    # ldconfig 경로
    name = ctypes.util.find_library('prismlib')
    if name:
        return ctypes.CDLL(name)
    raise OSError(f"{_SO_NAME} not found — build with 'make' in prismlib/")

_lib = _load_lib()

# ── 함수 시그니처 ──────────────────────────────────────────────────────────────

_lib.prismlib_open.restype  = ctypes.c_void_p
_lib.prismlib_open.argtypes = [ctypes.c_char_p, ctypes.c_uint16]

_lib.prismlib_is_open.restype  = ctypes.c_int
_lib.prismlib_is_open.argtypes = [ctypes.c_void_p]

_lib.prismlib_close.restype  = ctypes.c_int
_lib.prismlib_close.argtypes = [ctypes.c_void_p]

_lib.prismlib_fw_version.restype  = ctypes.c_int
_lib.prismlib_fw_version.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

_lib.prismlib_serial.restype  = ctypes.c_int
_lib.prismlib_serial.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

_lib.prismlib_error_msg.restype  = ctypes.c_char_p
_lib.prismlib_error_msg.argtypes = [ctypes.c_int]

_lib.prismlib_cal_date.restype  = ctypes.c_int
_lib.prismlib_cal_date.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

_lib.prismlib_cal_read.restype  = ctypes.c_int
_lib.prismlib_cal_read.argtypes = [ctypes.c_void_p, ctypes.c_uint8,
                                    ctypes.POINTER(ctypes.c_double),
                                    ctypes.POINTER(ctypes.c_double)]

_lib.prismlib_cal_write.restype  = ctypes.c_int
_lib.prismlib_cal_write.argtypes = [ctypes.c_void_p, ctypes.c_uint8,
                                     ctypes.c_double, ctypes.c_double]

_lib.prismlib_iepe_read.restype  = ctypes.c_int
_lib.prismlib_iepe_read.argtypes = [ctypes.c_void_p, ctypes.c_uint8,
                                     ctypes.POINTER(ctypes.c_uint8)]

_lib.prismlib_iepe_write.restype  = ctypes.c_int
_lib.prismlib_iepe_write.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8]

_lib.prismlib_iepe_diag.restype  = ctypes.c_int
_lib.prismlib_iepe_diag.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8)]

_lib.prismlib_sens_read.restype  = ctypes.c_int
_lib.prismlib_sens_read.argtypes = [ctypes.c_void_p, ctypes.c_uint8,
                                     ctypes.POINTER(ctypes.c_double)]

_lib.prismlib_sens_write.restype  = ctypes.c_int
_lib.prismlib_sens_write.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_double]

_lib.prismlib_sampleRate_read.restype  = ctypes.c_int
_lib.prismlib_sampleRate_read.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]

_lib.prismlib_sampleRate_write.restype  = ctypes.c_int
_lib.prismlib_sampleRate_write.argtypes = [ctypes.c_void_p, ctypes.c_int]

_lib.prismlib_scan_start.restype  = ctypes.c_int
_lib.prismlib_scan_start.argtypes = [ctypes.c_void_p, ctypes.c_uint8,
                                      ctypes.c_uint32, ctypes.c_uint32]

_lib.prismlib_scan_stop.restype  = ctypes.c_int
_lib.prismlib_scan_stop.argtypes = [ctypes.c_void_p]

_lib.prismlib_scan_read.restype  = ctypes.c_int
_lib.prismlib_scan_read.argtypes = [ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_uint16),
                                     ctypes.c_int32, ctypes.c_double,
                                     ctypes.POINTER(ctypes.c_double), ctypes.c_uint32,
                                     ctypes.POINTER(ctypes.c_uint32)]

_lib.prismlib_scan_status.restype  = ctypes.c_int
_lib.prismlib_scan_status.argtypes = [ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.c_uint16),
                                       ctypes.POINTER(ctypes.c_uint32)]

_lib.prismlib_scan_cleanup.restype  = ctypes.c_int
_lib.prismlib_scan_cleanup.argtypes = [ctypes.c_void_p]

_lib.prismlib_scan_ch_count.restype  = ctypes.c_int
_lib.prismlib_scan_ch_count.argtypes = [ctypes.c_void_p]

_lib.prismlib_scan_buf_size.restype  = ctypes.c_int
_lib.prismlib_scan_buf_size.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]

# ── Device info ───────────────────────────────────────────────────────────

class _DeviceInfo(ctypes.Structure):
    _fields_ = [
        ("NUM_AI_CHANNELS", ctypes.c_uint8),
        ("AI_MIN_CODE",     ctypes.c_int32),
        ("AI_MAX_CODE",     ctypes.c_int32),
        ("AI_MIN_VOLTAGE",  ctypes.c_double),
        ("AI_MAX_VOLTAGE",  ctypes.c_double),
        ("AI_MIN_RANGE",    ctypes.c_double),
        ("AI_MAX_RANGE",    ctypes.c_double),
    ]

_lib.prismlib_info.restype  = ctypes.POINTER(_DeviceInfo)
_lib.prismlib_info.argtypes = [ctypes.c_void_p]

# ── LED control ───────────────────────────────────────────────────────────

_lib.prismlib_led_write.restype  = ctypes.c_int
_lib.prismlib_led_write.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]

_lib.prismlib_run_led_kernel.restype  = ctypes.c_int
_lib.prismlib_run_led_kernel.argtypes = [ctypes.c_void_p, ctypes.c_int]

_lib.prismlib_run_led_write.restype  = ctypes.c_int
_lib.prismlib_run_led_write.argtypes = [ctypes.c_void_p, ctypes.c_int]

# ── 예외 ──────────────────────────────────────────────────────────────────────

class prismlibError(Exception):
    def __init__(self, result_code: int, message: str):
        super().__init__(message)
        self.result_code = result_code


def _check(rc: int, ctx: str) -> None:
    if rc != 0:
        msg_p = _lib.prismlib_error_msg(rc)
        detail = msg_p.decode() if msg_p else f"code {rc}"
        raise prismlibError(rc, f"{ctx}: {detail}")


# ── prismlib 클래스 ────────────────────────────────────────────────────────────

class prismlib:
    """PRISM device handle backed by libprismlib.so (C backend).

    스캔 수신 스레드가 C(pthread)로 동작하므로 512kSPS에서도 Python GIL의
    영향을 받지 않는다. 예제 코드 API는 순수 Python 버전과 동일하다.
    """

    # sample_rate enum → Hz (SampleRate.SR_xxx 인덱스와 일치)
    _SR_HZ = [64_000, 128_000, 170_000, 256_000, 512_000]

    def __init__(self, ip: str, port: int = 7777) -> None:
        self._ip          = ip.encode()
        self._port        = port
        self._dev: ctypes.c_void_p | None = None
        self._ch_count    = 0
        self._sample_rate = 64_000          # sampleRate_write() 호출 시 갱신
        self._rbuf: ctypes.Array | None = None   # 재사용 scan_read 버퍼
        self._rbuf_len    = 0

    def _default_buf_cap(self) -> int:
        """샘플링 속도 기준 최소 2초 분량의 링 버퍼 크기."""
        return max(128_000, self._sample_rate * 2)

    # ── 장치 관리 ──────────────────────────────────────────────────────────────

    def open(self) -> None:
        dev = _lib.prismlib_open(self._ip, self._port)
        if not dev:
            raise prismlibError(-7, "prismlib_open failed")
        self._dev = dev

    def is_open(self) -> bool:
        return self._dev is not None and bool(_lib.prismlib_is_open(self._dev))

    def close(self) -> None:
        if self._dev:
            _check(_lib.prismlib_close(self._dev), "close")
            self._dev = None

    def info(self) -> dict:
        p = _lib.prismlib_info(self._dev)
        if not p:
            return {}
        i = p.contents
        return {
            "num_ai_channels": i.NUM_AI_CHANNELS,
            "ai_min_code":     i.AI_MIN_CODE,
            "ai_max_code":     i.AI_MAX_CODE,
            "ai_min_voltage":  i.AI_MIN_VOLTAGE,
            "ai_max_voltage":  i.AI_MAX_VOLTAGE,
            "ai_min_range":    i.AI_MIN_RANGE,
            "ai_max_range":    i.AI_MAX_RANGE,
        }

    def fw_version(self) -> str:
        buf = ctypes.create_string_buffer(64)
        _check(_lib.prismlib_fw_version(self._dev, buf), "fw_version")
        return buf.value.decode()

    def serial(self) -> str:
        buf = ctypes.create_string_buffer(64)
        _check(_lib.prismlib_serial(self._dev, buf), "serial")
        return buf.value.decode()

    # ── 캘리브레이션 ───────────────────────────────────────────────────────────

    def cal_date(self) -> str:
        buf = ctypes.create_string_buffer(64)
        _check(_lib.prismlib_cal_date(self._dev, buf), "cal_date")
        return buf.value.decode()

    def cal_read(self, channel: int) -> tuple[float, float]:
        slope = ctypes.c_double()
        offset = ctypes.c_double()
        _check(_lib.prismlib_cal_read(self._dev, channel,
                                      ctypes.byref(slope), ctypes.byref(offset)), "cal_read")
        return slope.value, offset.value

    def cal_write(self, channel: int, slope: float, offset: float) -> None:
        _check(_lib.prismlib_cal_write(self._dev, channel, slope, offset), "cal_write")

    # ── IEPE ──────────────────────────────────────────────────────────────────

    def iepe_read(self, channel: int) -> bool:
        cfg = ctypes.c_uint8()
        _check(_lib.prismlib_iepe_read(self._dev, channel, ctypes.byref(cfg)), "iepe_read")
        return bool(cfg.value)

    def iepe_write(self, channel: int, enable: bool) -> None:
        """IEPE 전류원을 활성화/비활성화한다.

        XTR111 전류원은 활성화 직후 바이어스 전압이 안정화되기까지 시간이 걸린다.
        enable=True 호출 후 최소 2초 대기한 뒤 데이터 수집 및 iepe_diag()를 수행해야
        정상적인 센서 신호와 진단 결과를 얻을 수 있다.
        """
        _check(_lib.prismlib_iepe_write(self._dev, channel, 1 if enable else 0), "iepe_write")

    def iepe_diag(self) -> list[bool]:
        """각 채널의 IEPE 고장 여부 반환. [ch1, ch2, ch3, ch4] 순서, True=고장."""
        mask = ctypes.c_uint8()
        _check(_lib.prismlib_iepe_diag(self._dev, ctypes.byref(mask)), "iepe_diag")
        return [(mask.value >> ch) & 1 != 0 for ch in range(4)]

    # ── 감도 ──────────────────────────────────────────────────────────────────

    def sens_read(self, channel: int) -> float:
        val = ctypes.c_double()
        _check(_lib.prismlib_sens_read(self._dev, channel, ctypes.byref(val)), "sens_read")
        return val.value

    def sens_write(self, channel: int, value: float) -> None:
        _check(_lib.prismlib_sens_write(self._dev, channel, value), "sens_write")

    # ── 샘플링 레이트 ──────────────────────────────────────────────────────────

    def sampleRate_read(self) -> int:
        sr = ctypes.c_int()
        _check(_lib.prismlib_sampleRate_read(self._dev, ctypes.byref(sr)), "sampleRate_read")
        return sr.value

    def sampleRate_write(self, sample_rate: int) -> None:
        _check(_lib.prismlib_sampleRate_write(self._dev, sample_rate), "sampleRate_write")
        if 0 <= sample_rate < len(self._SR_HZ):
            self._sample_rate = self._SR_HZ[sample_rate]

    # ── 스캔 ──────────────────────────────────────────────────────────────────

    def scan_start(self, channel_mask: int,
                   samples_per_channel: int, options: int) -> None:
        # 연속 스캔에서는 서버가 samples_per_channel을 무시하므로
        # 링 버퍼를 최소 2초 분량으로 자동 조정한다.
        if options & ScanOptions.CONTINUOUS:
            buf_cap = max(samples_per_channel, self._default_buf_cap())
        else:
            buf_cap = samples_per_channel
        _check(_lib.prismlib_scan_start(self._dev, channel_mask,
                                        buf_cap, options), "scan_start")
        self._ch_count = _lib.prismlib_scan_ch_count(self._dev)

    def scan_stop(self) -> None:
        _check(_lib.prismlib_scan_stop(self._dev), "scan_stop")

    def scan_read(self, samples_per_channel: int,
                  timeout: float) -> tuple[list[float], int]:
        """샘플 읽기. 반환: (채널 인터리브 float 리스트, status)"""
        needed = self._ch_count * samples_per_channel
        if needed > self._rbuf_len:
            self._rbuf     = (ctypes.c_double * needed)()
            self._rbuf_len = needed

        status = ctypes.c_uint16()
        n_read = ctypes.c_uint32()
        rc = _lib.prismlib_scan_read(self._dev, ctypes.byref(status),
                                     samples_per_channel, timeout,
                                     self._rbuf, self._rbuf_len,
                                     ctypes.byref(n_read))
        _check(rc, "scan_read")
        count = n_read.value * self._ch_count
        return list(self._rbuf[:count]), status.value

    def scan_read_numpy(self, samples_per_channel: int,
                        timeout: float) -> tuple[np.ndarray, int]:
        """scan_read() numpy 버전. 반환: (shape=(n, ch_count) float64, status)"""
        needed = self._ch_count * samples_per_channel
        if needed > self._rbuf_len:
            self._rbuf     = (ctypes.c_double * needed)()
            self._rbuf_len = needed

        status = ctypes.c_uint16()
        n_read = ctypes.c_uint32()
        rc = _lib.prismlib_scan_read(self._dev, ctypes.byref(status),
                                     samples_per_channel, timeout,
                                     self._rbuf, self._rbuf_len,
                                     ctypes.byref(n_read))
        _check(rc, "scan_read")
        count = n_read.value * self._ch_count
        arr = np.frombuffer(self._rbuf, dtype=np.float64, count=count).copy()
        return arr.reshape(n_read.value, self._ch_count), status.value

    def scan_status(self) -> tuple[int, int]:
        """반환: (status, 읽을 수 있는 샘플 수/ch)"""
        status = ctypes.c_uint16()
        avail  = ctypes.c_uint32()
        _check(_lib.prismlib_scan_status(self._dev, ctypes.byref(status),
                                         ctypes.byref(avail)), "scan_status")
        return status.value, avail.value

    def scan_cleanup(self) -> None:
        _check(_lib.prismlib_scan_cleanup(self._dev), "scan_cleanup")
        self._rbuf     = None
        self._rbuf_len = 0
        self._ch_count = 0

    def scan_ch_count(self) -> int:
        return self._ch_count

    def scan_buf_size(self) -> int:
        size = ctypes.c_uint32()
        _check(_lib.prismlib_scan_buf_size(self._dev, ctypes.byref(size)), "scan_buf_size")
        return size.value

    # ── LED control ───────────────────────────────────────────────────────

    # led_id: PRISM_LED_PWR=21 / PRISM_LED_ERR=20 / PRISM_LED_ALARM=16
    LED_PWR   = 21
    LED_ERR   = 20
    LED_ALARM = 16

    def led_write(self, led_id: int, state: bool) -> None:
        _check(_lib.prismlib_led_write(self._dev, led_id, 1 if state else 0), "led_write")

    def run_led_kernel(self, enable: bool) -> None:
        """enable=False: RUN LED를 수동 제어 모드로 전환.
        enable=True: 커널 제어 복원."""
        _check(_lib.prismlib_run_led_kernel(self._dev, 1 if enable else 0), "run_led_kernel")

    def run_led_write(self, state: bool) -> None:
        """RUN LED on/off. run_led_kernel(False) 호출 후에만 유효."""
        _check(_lib.prismlib_run_led_write(self._dev, 1 if state else 0), "run_led_write")
