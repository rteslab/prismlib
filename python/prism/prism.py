# prism.py — PRISM-PyLib main Prism class
import struct
import time
from .constants import (
    RESULT_SUCCESS, RESULT_BAD_PARAMETER, RESULT_RESOURCE_UNAVAIL, RESULT_BUSY,
    RESULT_TIMEOUT, RESULT_COMMS_FAILURE,
    ScanOptions, ScanStatus,
    CMD_OPEN, CMD_CLOSE, CMD_IS_OPEN, CMD_INFO,
    CMD_FW_VERSION, CMD_SERIAL,
    CMD_CAL_DATE, CMD_CAL_READ,
    CMD_IEPE_READ, CMD_IEPE_WRITE,
    CMD_CLOCK_READ, CMD_CLOCK_WRITE,
    CMD_SCAN_START, CMD_SCAN_STOP,
    CMD_SCAN_CLEANUP, CMD_SCAN_STATUS,
    CMD_SCAN_CH_COUNT, CMD_SCAN_BUF_SIZE,
)
from .transport import Transport
from .scan_thread import ScanThread
from .protocol import (
    build_req, parse_res_hdr,
    pack_double, unpack_double, unpack_i32,
)


# USB NCM GPIO control (CM4 GPIO 13, 100k external pull-down)
# BCM2711 GPIO 13 + 외부 100k pull-down → 전원 인가 직후 LOW 확정.
# open() 에서 HIGH로 구동해 AM2431 Vbus를 활성화한 후 TCP 재시도.
_NCM_GPIO_PIN      = 13
_NCM_OPEN_TIMEOUT  = 10.0   # USB NCM 열거 최대 대기 (초)
_NCM_OPEN_INTERVAL = 0.2    # TCP 재시도 간격 (초)


class PrismError(Exception):
    def __init__(self, result_code: int, message: str):
        super().__init__(message)
        self.result_code = result_code


class Prism:
    """PRISM device handle. Supports multiple simultaneous instances (multi-device)."""

    def __init__(self, ip: str, port: int = 7777) -> None:
        self._ip   = ip
        self._port = port
        self._tr   = Transport()
        self._scan = ScanThread()
        self._cal_slope:   list[float] = [1.0]    * 4
        self._cal_offset:  list[float] = [0.0]    * 4
        self._sensitivity: list[float] = [1000.0] * 4
        self._sample_rate: float       = 64000.0
        self._info: dict = {}

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _gpio_enable_ncm() -> None:
        """GPIO _NCM_GPIO_PIN HIGH → AM2431 Vbus 활성화 (이미 HIGH여도 무해)."""
        pin  = _NCM_GPIO_PIN
        base = f"/sys/class/gpio/gpio{pin}"
        try:
            with open("/sys/class/gpio/export", "w") as f:
                f.write(str(pin))
            time.sleep(0.05)  # sysfs 디렉터리 생성 대기
        except OSError:
            pass  # EBUSY: 이미 export됨 — 무시
        try:
            with open(f"{base}/direction", "w") as f:
                f.write("out")
            with open(f"{base}/value", "w") as f:
                f.write("1")
        except OSError as e:
            raise PrismError(RESULT_COMMS_FAILURE,
                             f"GPIO {pin} control failed: {e}")

    def _cmd(self, cmd: int, payload: bytes = b"") -> bytes:
        """Send a command and return the response payload bytes."""
        self._tr.send_all(build_req(cmd, payload))
        hdr = self._tr.recv_exact(3)
        result, payload_len = parse_res_hdr(hdr, cmd)
        body = self._tr.recv_exact(payload_len) if payload_len > 0 else b""
        if result != RESULT_SUCCESS:
            raise PrismError(result, f"Command 0x{cmd:02X} failed (code {result})")
        return body

    def _default_buf_cap(self) -> int:
        """샘플링 속도에 따른 기본 링 버퍼 크기 (sample-set 수, ~2초 커버리지)."""
        r = self._sample_rate
        if r <=   8_000:  return    16_000   # ~2.0초 @   8kS/s
        if r <=  64_000:  return   128_000   # ~2.0초 @  64kS/s
        if r <= 128_000:  return   256_000   # ~2.0초 @ 128kS/s
        if r <= 256_000:  return   512_000   # ~2.0초 @ 256kS/s
        return                   1_024_000   # ~2.0초 @ 512kS/s

    # ── Device management ─────────────────────────────────────────────────

    def open(self) -> None:
        self._gpio_enable_ncm()  # Vbus HIGH

        # USB NCM 열거 완료까지 재시도 (최대 _NCM_OPEN_TIMEOUT 초)
        deadline = time.monotonic() + _NCM_OPEN_TIMEOUT
        while True:
            try:
                self._tr.connect(self._ip, self._port, timeout=1.0)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise PrismError(RESULT_TIMEOUT,
                                     "USB NCM connection timed out")
                time.sleep(_NCM_OPEN_INTERVAL)

        self._cmd(CMD_OPEN)
        # Fetch device info once
        body = self._cmd(CMD_INFO)
        if len(body) >= 41:
            self._info = {
                "num_ai_channels": body[0],
                "ai_min_code":     unpack_i32(body, 1),
                "ai_max_code":     unpack_i32(body, 5),
                "ai_min_voltage":  unpack_double(body, 9),
                "ai_max_voltage":  unpack_double(body, 17),
                "ai_min_range":    unpack_double(body, 25),
                "ai_max_range":    unpack_double(body, 33),
            }
        # Load factory calibration from device EEPROM
        for ch in range(4):
            try:
                body = self._cmd(CMD_CAL_READ, bytes([ch]))
                if len(body) >= 16:
                    self._cal_slope[ch]  = unpack_double(body, 0)
                    self._cal_offset[ch] = unpack_double(body, 8)
            except PrismError:
                pass

    def is_open(self) -> bool:
        return self._tr.is_connected

    def close(self) -> None:
        if self._scan.active:
            self._scan.stop_send()
            self._scan.join()
            self._cmd(CMD_SCAN_CLEANUP)  # 서버: STATE_SCAN_STOPPING → STATE_IDLE
            self._scan.cleanup()
        if self._tr.is_connected:
            self._cmd(CMD_CLOSE)
            self._tr.close()

    def info(self) -> dict:
        return dict(self._info)

    def fw_version(self) -> str:
        body = self._cmd(CMD_FW_VERSION)
        return body.decode("ascii", errors="replace")

    def serial(self) -> str:
        body = self._cmd(CMD_SERIAL)
        return body.decode("ascii", errors="replace")

    # ── Calibration ───────────────────────────────────────────────────────

    def cal_date(self) -> str:
        body = self._cmd(CMD_CAL_DATE)
        return body.decode("ascii", errors="replace")

    def cal_read(self, channel: int) -> tuple[float, float]:
        body = self._cmd(CMD_CAL_READ, bytes([channel]))
        return unpack_double(body, 0), unpack_double(body, 8)

    def cal_write(self, channel: int, slope: float, offset: float) -> None:
        if channel >= 4:
            raise PrismError(RESULT_BAD_PARAMETER, f"Invalid channel {channel}")
        self._cal_slope[channel]  = slope
        self._cal_offset[channel] = offset

    # ── IEPE ──────────────────────────────────────────────────────────────

    def iepe_read(self, channel: int) -> bool:
        body = self._cmd(CMD_IEPE_READ, bytes([channel]))
        return body[0] != 0

    def iepe_write(self, channel: int, enable: bool) -> None:
        self._cmd(CMD_IEPE_WRITE, bytes([channel, 1 if enable else 0]))

    # ── Sensitivity ───────────────────────────────────────────────────────

    def sens_read(self, channel: int) -> float:
        if channel >= 4:
            raise PrismError(RESULT_BAD_PARAMETER, f"Invalid channel {channel}")
        return self._sensitivity[channel]

    def sens_write(self, channel: int, value: float) -> None:
        if channel >= 4 or value <= 0.0:
            raise PrismError(RESULT_BAD_PARAMETER, f"Invalid channel or value")
        self._sensitivity[channel] = value

    # ── Clock ─────────────────────────────────────────────────────────────

    def clock_read(self) -> float:
        body = self._cmd(CMD_CLOCK_READ)
        return unpack_double(body)

    def clock_write(self, sample_rate: float) -> None:
        self._cmd(CMD_CLOCK_WRITE, pack_double(sample_rate))
        self._sample_rate = sample_rate

    # ── Scan ──────────────────────────────────────────────────────────────

    def scan_start(self, channel_mask: int,
                   samples_per_channel: int, options: int) -> None:
        # CMD_SCAN_START 페이로드: [channel_mask:1B] + [samples_per_channel:4B LE] + [options:4B LE] = 9바이트
        payload = (bytes([channel_mask])                                  # 1바이트: 채널 선택 비트마스크
                   + struct.pack("<II", samples_per_channel, options))    # 8바이트: 샘플 수 + 옵션 (little-endian uint32 x2)
        self._cmd(CMD_SCAN_START, payload)

        buf_cap = max(samples_per_channel, self._default_buf_cap())
        self._scan.start(
            self._tr, channel_mask, buf_cap, options,
            self._cal_slope, self._cal_offset, self._sensitivity,
        )

    def scan_stop(self) -> None:
        if not self._scan.active:
            raise PrismError(RESULT_RESOURCE_UNAVAIL, "No active scan")
        self._scan.stop_send()

    def scan_read(self, samples_per_channel: int,
                  timeout: float) -> tuple[list[float], int]:
        if not self._scan.active:
            raise PrismError(RESULT_RESOURCE_UNAVAIL, "No active scan")
        return self._scan.read(samples_per_channel, timeout)

    def scan_read_numpy(self, samples_per_channel: int, timeout: float):
        """scan_read()와 동일하지만 data를 numpy ndarray로 반환.
        shape: (samples_read, ch_count), dtype: float64"""
        if not self._scan.active:
            raise PrismError(RESULT_RESOURCE_UNAVAIL, "No active scan")
        return self._scan.read_numpy(samples_per_channel, timeout)

    def scan_status(self) -> tuple[int, int]:
        if not self._scan.active:
            raise PrismError(RESULT_RESOURCE_UNAVAIL, "No active scan")
        return self._scan.get_status()

    def scan_cleanup(self) -> None:
        if not self._scan.active:
            raise PrismError(RESULT_RESOURCE_UNAVAIL, "No active scan")
        status, _ = self._scan.get_status()
        if status & ScanStatus.RUNNING:
            raise PrismError(RESULT_BUSY, "scan_stop() must be called before scan_cleanup()")
        self._scan.join()
        self._cmd(CMD_SCAN_CLEANUP)
        self._scan.cleanup()

    def scan_ch_count(self) -> int:
        return self._scan.ch_count

    def scan_buf_size(self) -> int:
        if not self._scan.active:
            raise PrismError(RESULT_RESOURCE_UNAVAIL, "No active scan")
        return self._scan.buf_size
