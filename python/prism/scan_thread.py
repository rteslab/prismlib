# scan_thread.py — Background scan thread and numpy ring buffer for PRISM-PyLib
import threading
import numpy as np
from .constants import (
    ScanStatus, CMD_SCAN_STOP,
    SRV_HW_OVERRUN, SRV_SCAN_STOPPED,
    SCAN_FRAME_SIZE, SCAN_N_SAMPLES, SCAN_N_CH,
)
from .protocol import (
    build_req, is_scan_frame, scan_frame_status,
    scan_frame_payload, decode_scan_payload,
)
from .transport import Transport


class ScanThread:
    def __init__(self):
        self._transport: Transport | None = None
        self._buf: np.ndarray | None = None  # shape (buf_cap, ch_count), float64
        self._buf_cap: int = 0
        self._wpos:  int = 0   # next write index
        self._depth: int = 0   # available sample-sets
        self._ch_count: int = 0
        self._ch_mask: int = 0
        self._options: int = 0
        self._cal_slope:   list = [1.0]    * 4
        self._cal_offset:  list = [0.0]    * 4
        self._sensitivity: list = [1000.0] * 4
        self._status: int = 0
        self._active: bool = False
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self, transport: Transport, ch_mask: int, buf_cap: int,
              options: int, cal_slope: list, cal_offset: list,
              sensitivity: list) -> None:
        self._transport   = transport
        self._ch_mask     = ch_mask
        self._buf_cap     = buf_cap
        self._options     = options
        self._cal_slope   = list(cal_slope)
        self._cal_offset  = list(cal_offset)
        self._sensitivity = list(sensitivity)

        self._ch_count = sum(1 for i in range(4) if ch_mask & (1 << i))
        # 메모리: buf_cap × ch_count × 8 bytes (float64)
        # 예) 1,024,000 × 4ch = 32,768,000 bytes ≈ 33 MB
        self._buf   = np.zeros((buf_cap, self._ch_count), dtype=np.float64)
        self._wpos  = 0
        self._depth = 0
        self._status = ScanStatus.RUNNING
        self._active = True

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop_send(self) -> None:
        """Send CMD_SCAN_STOP; server sets SRV_SCAN_STOPPED on next push frame."""
        if self._transport:
            self._transport.send_all(build_req(CMD_SCAN_STOP))

    def join(self) -> None:
        if self._thread and self._thread.is_alive():
            self._thread.join()

    def cleanup(self) -> None:
        with self._cond:
            self._buf      = None
            self._wpos     = 0
            self._depth    = 0
            self._active   = False
            self._status   = 0
            self._ch_count = 0

    # ── Receive thread ────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            while True:
                frame = self._transport.recv_exact(SCAN_FRAME_SIZE, timeout=5.0)
                if not is_scan_frame(frame):
                    break

                srv_status = scan_frame_status(frame)
                payload    = scan_frame_payload(frame)

                samples = decode_scan_payload(
                    payload, SCAN_N_SAMPLES, self._ch_count,
                    self._cal_slope, self._cal_offset,
                    self._sensitivity, self._options,
                )
                arr = np.array(samples, dtype=np.float64).reshape(SCAN_N_SAMPLES, self._ch_count)

                with self._cond:
                    if srv_status & SRV_HW_OVERRUN:
                        self._status |= ScanStatus.HW_OVERRUN
                    if self._write_batch(arr):
                        self._status |= ScanStatus.BUFFER_OVERRUN
                    self._cond.notify_all()

                if srv_status & SRV_SCAN_STOPPED:
                    break
        except OSError:
            pass
        finally:
            with self._cond:
                self._status &= ~ScanStatus.RUNNING
                self._cond.notify_all()

    def _write_batch(self, arr: np.ndarray) -> bool:
        """Write arr (shape N×ch) into ring; return True if buffer overrun occurred."""
        n = len(arr)
        overrun = (self._depth + n) > self._buf_cap

        end = self._buf_cap - self._wpos
        if n <= end:
            self._buf[self._wpos:self._wpos + n] = arr
        else:
            self._buf[self._wpos:] = arr[:end]
            self._buf[:n - end]    = arr[end:]

        self._wpos  = (self._wpos + n) % self._buf_cap
        self._depth = min(self._depth + n, self._buf_cap)
        return overrun

    # ── Ring read helpers ─────────────────────────────────────────────────────

    def _wait_for(self, n: int, timeout: float) -> None:
        """Wait until depth >= n or scan stopped. Caller must hold _cond."""
        if self._depth >= n or not (self._status & ScanStatus.RUNNING):
            return
        if timeout < 0:
            while self._depth < n and (self._status & ScanStatus.RUNNING):
                self._cond.wait()
        elif timeout > 0:
            self._cond.wait_for(
                lambda: self._depth >= n or not (self._status & ScanStatus.RUNNING),
                timeout=timeout,
            )

    def _read_raw(self, take: int) -> np.ndarray:
        """Pop `take` sample-sets from ring; caller holds lock. Returns (take, ch_count)."""
        rpos = (self._wpos - self._depth) % self._buf_cap
        end  = self._buf_cap - rpos
        if take <= end:
            result = self._buf[rpos:rpos + take].copy()
        else:
            result = np.concatenate([self._buf[rpos:], self._buf[:take - end]])
        self._depth -= take
        return result

    # ── Public read API ───────────────────────────────────────────────────────

    def read(self, req_per_ch: int, timeout: float) -> tuple[list[float], int]:
        """Read sample-sets; returns (flat float list interleaved by channel, status)."""
        with self._cond:
            n              = self._depth if req_per_ch < 0 else req_per_ch
            actual_timeout = 0.0        if req_per_ch < 0 else timeout
            self._wait_for(n, actual_timeout)
            take = min(n, self._depth)
            arr  = self._read_raw(take)
            return arr.flatten().tolist(), self._status

    def read_numpy(self, req_per_ch: int, timeout: float) -> tuple[np.ndarray, int]:
        """Read sample-sets; returns (ndarray shape (take, ch_count) float64, status)."""
        with self._cond:
            n              = self._depth if req_per_ch < 0 else req_per_ch
            actual_timeout = 0.0        if req_per_ch < 0 else timeout
            self._wait_for(n, actual_timeout)
            take = min(n, self._depth)
            arr  = self._read_raw(take)
            return arr, self._status

    def get_status(self) -> tuple[int, int]:
        """Returns (status, available_sample_sets)."""
        with self._cond:
            return self._status, self._depth

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    @property
    def ch_count(self) -> int:
        return self._ch_count if self._active else 0

    @property
    def buf_size(self) -> int:
        return self._buf_cap
