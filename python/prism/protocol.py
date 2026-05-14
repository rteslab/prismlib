# protocol.py — PRISM binary frame encode/decode
import struct
from .constants import (
    SRV_OK, SRV_BUSY, SRV_BAD_PARAM,
    RESULT_SUCCESS, RESULT_BUSY, RESULT_BAD_PARAMETER,
    RESULT_COMMS_FAILURE, RESULT_UNDEFINED,
    CMD_SCAN_DATA, SCAN_BYTES_PER_S,
)


def build_req(cmd: int, payload: bytes = b"") -> bytes:
    """[cmd:1][len:1][payload:N]"""
    return bytes([cmd, len(payload)]) + payload


def parse_res_hdr(hdr: bytes, expected_cmd: int) -> tuple[int, int]:
    """Parse 3-byte response header → (result_code, payload_len).
    Raises ValueError on unexpected cmd."""
    cmd, status, payload_len = hdr[0], hdr[1], hdr[2]
    if cmd != expected_cmd:
        raise ValueError(
            f"Unexpected response cmd 0x{cmd:02X} (expected 0x{expected_cmd:02X})"
        )
    return srv_status_to_result(status), payload_len


def srv_status_to_result(status: int) -> int:
    if status == SRV_OK:        return RESULT_SUCCESS
    if status == SRV_BUSY:      return RESULT_BUSY
    if status == SRV_BAD_PARAM: return RESULT_BAD_PARAMETER
    return RESULT_UNDEFINED


def int24_to_int32(data: bytes, offset: int) -> int:
    """3-byte little-endian int24 → signed int32."""
    v = data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16)
    if v & 0x800000:
        v -= 0x1000000
    return v


def pack_double(value: float) -> bytes:
    return struct.pack("<d", value)


def unpack_double(data: bytes, offset: int = 0) -> float:
    return struct.unpack_from("<d", data, offset)[0]


def unpack_i32(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def is_scan_frame(frame: bytes) -> bool:
    return len(frame) >= 1 and frame[0] == CMD_SCAN_DATA


def scan_frame_status(frame: bytes) -> int:
    return frame[2]


def scan_frame_payload(frame: bytes) -> bytes:
    return frame[3:]


def decode_scan_payload(payload: bytes, n_samples: int, ch_count: int,
                        cal_slope: list, cal_offset: list,
                        sensitivity: list, options: int) -> list[float]:
    """Convert raw 24-bit ADC payload to physical doubles.
    Returns interleaved list: [ch0_s0, ch1_s0, ..., ch0_s1, ...]
    """
    from .constants import ScanOptions
    out: list[float] = []
    pos = 0
    for _ in range(n_samples):
        for ch in range(ch_count):
            raw = int24_to_int32(payload, pos)
            pos += SCAN_BYTES_PER_S

            code = float(raw)
            if not (options & ScanOptions.NOCALIBRATEDATA):
                code = (code - cal_offset[ch]) * cal_slope[ch]

            if options & ScanOptions.NOSCALEDATA:
                out.append(code)
            else:
                voltage = code / 8_388_607.0 * 5.0
                out.append(voltage / (sensitivity[ch] / 1000.0))
    return out
