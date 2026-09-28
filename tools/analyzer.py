#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRISM ANALYZER - realtime waveform, FFT and 1/3-octave band analyzer for PRISM continuous scans.

    python3 analyzer.py [ip] [port] [sample_rate]
    sample_rate: 0=64K 1=128K 3=256K 4=512K 5=32K 6=16K 7=8K 8=4K 9=2K 10=1K 11=500
    (default: 192.168.7.1 7777 0)

Pick the channels and IEPE on/off in the window, then press Start.

Requirements: python3-tk, python3-matplotlib, numpy (all from apt).

Note:
    dB SPL assumes the input signal is in Pa (sound pressure).  For a signal
    in V or mV adjust CAL_DB_OFFSET or the sensitivity conversion to match
    the actual microphone calibration.
"""

# =========================
# GUI / realtime processing imports
# =========================
import sys
import time
import queue
import functools
import threading
import traceback
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox

import matplotlib
matplotlib.use("TkAgg")

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import PolyCollection
from matplotlib.figure import Figure
from matplotlib.transforms import Bbox

matplotlib.rcParams["axes.unicode_minus"] = False

from prismlib import prismlib, ScanOptions, ScanStatus, SR_NAME, sr_hz, sr_name


# =========================
# Configuration
# =========================

DEFAULT_IP = "192.168.7.1"
DEFAULT_PORT = 7777
DEFAULT_SAMPLE_RATE_INDEX = 0
DEFAULT_PERIOD_SEC = 0.20           # Display refresh interval (s)
TEXT_REFRESH_FRAMES = 3             # Numeric readouts are redrawn every N frames (text is costly to render)
DEFAULT_CHANNEL = 0                 # Show ch1
DEFAULT_SENSITIVITY = 100.0         # Sensor sensitivity (mV/unit)

SCAN_MASK = 0x0F                    # 4-channel scan mask
SCAN_BUFFER_SAMPLES = 1_000_000     # Device-side buffer; large enough to ride out GUI stalls
READ_SAMPLES_PER_CHANNEL = 8192     # Samples drained per read call

P_REF = 20e-6                       # Reference sound pressure 20 uPa
CAL_DB_OFFSET = 0.0                 # Calibration offset dB. Adjust with calibrator if needed.
EPS = 1e-30

ANALYSIS_WINDOW_SEC = 1.0           # Data window length for FFT / 1/3 octave analysis
MAX_ANALYSIS_SAMPLES = 131_072      # Limit computation at high sample rates
TIME_HISTORY_SEC = 10.0             # Time history display range (bottom right plot)
FFT_VIEW_MAX_HZ = 50_000.0           # FFT view upper limit Hz (ultrasonic; needs fs>=128K to reach 50kHz band, auto-clamped to Nyquist)

THIRD_OCTAVE_CENTER_FREQS = np.array([
    40, 50, 63, 80, 100, 125, 160, 200, 250, 315,
    400, 500, 630, 800, 1000, 1250, 1600, 2000,
    2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000,
    25000, 31500, 40000, 50000, 63000, 80000, 100000, 125000, 160000, 200000  # up to ~Nyquist (bands above fs/2 auto-skip)
], dtype=float)

# 1/3-octave proportional bandwidth: BW = fc*(2^(1/6)-2^(-1/6)) = 0.2315*fc.
# Higher-fc bands are wider, so for FLAT (white) noise the integrated band level rises
# ~+1.0 dB per band. To compare bands fairly, optionally normalize out this slope by
# referencing every band to the NORM_REF bandwidth (BW is proportional to fc):
#   norm_level = level - 10*log10(fc / NORM_REF).  After this, white noise is a flat line
#   and only genuine spectral coloration (a real peak/dip) stands out.
THIRD_OCTAVE_NORM_REF_HZ = 1000.0

# Band edges fc * 2^(-1/6) .. fc * 2^(+1/6), precomputed once.
THIRD_OCTAVE_F1 = THIRD_OCTAVE_CENTER_FREQS / (2.0 ** (1.0 / 6.0))
THIRD_OCTAVE_F2 = THIRD_OCTAVE_CENTER_FREQS * (2.0 ** (1.0 / 6.0))

# Focus band: track a user-defined frequency window (e.g. the ~22kHz feature).
# Stats are CUMULATIVE since start/band-change (not rolling): band avg + peak avg are running
# means; min/max are held extremes (peak-hold). Reset by Start or editing the Focus band.
FOCUS_BAND_MIN_HZ = 18_000.0
FOCUS_BAND_MAX_HZ = 26_000.0

# PSD exponential (time) averaging: averages the power spectrum across frames
# before deriving band level / peak frequency / FFT display, so the spectrum
# shape and peak frequency are stable.  alpha = period/tau per frame.
PSD_AVG_DEFAULT = False
PSD_AVG_TAU_SEC = 2.0

# Spur detection: a spur is an FFT bin that rises above the LOCAL noise floor
# (piecewise-median, follows the spectral slope) by more than a FREQUENCY-DEPENDENT margin.
# The margin is piecewise-linear between the configured "freq:margin_dB" points; a single point
# (e.g. "4k:10") gives a FLAT margin. Default: flag anything above 4 kHz that exceeds the floor
# by more than 10 dB. Edited in the "Config" dialog.
SPUR_THRESH_DEFAULT = "4k:10"             # flat +10 dB above floor (single point -> constant margin)
SPUR_BAND_LO_HZ = 4000.0                  # only scan >= 4 kHz (skip DC/1-f rolloff)
SPUR_ON_DEFAULT = True                    # spur detection enabled by default
SPUR_HOLD_SEC_DEFAULT = 3.0               # keep a spur shown this long after it stops being detected
SPUR_MAX_LIST = 6                         # how many spurs to list in the readout


# =========================
# DSP utility functions
# =========================
def interleaved_to_matrix(data, n_ch: int) -> np.ndarray:
    """Convert PRISM interleaved data to shape=(samples, channels) array."""
    arr = np.asarray(data, dtype=np.float64)
    if n_ch <= 0 or arr.size < n_ch:
        return np.empty((0, max(n_ch, 1)), dtype=np.float64)

    usable = (arr.size // n_ch) * n_ch
    return arr[:usable].reshape(-1, n_ch)


def demean(x: np.ndarray) -> np.ndarray:
    """Remove DC offset to reduce SPL/FFT bias from mic/DAQ offset."""
    if x.size == 0:
        return x
    return x - np.mean(x)


def rms(x: np.ndarray) -> float:
    """Compute RMS (root mean square)."""
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x))))


def pressure_to_db_spl(p_rms: float) -> float:
    """Convert RMS sound pressure to dB SPL."""
    return float(20.0 * np.log10(max(p_rms, EPS) / P_REF) + CAL_DB_OFFSET)


def overall_spl(x: np.ndarray) -> float:
    """Compute overall dB SPL across full band."""
    return pressure_to_db_spl(rms(demean(x)))


def compute_snr_ti(noise_psd: np.ndarray, signal_psd: np.ndarray, freq: np.ndarray, fs: float) -> float:
    """TI TIDUD62-style SNR: 20 * log(signal_RMS / noise_RMS).
    PSD is integrated over full band to get mean-square, then sqrt gives RMS.
    """
    band = (freq >= 20.0) & (freq <= fs / 2.0)
    if not np.any(band):
        return float("nan")

    noise_rms  = float(np.sqrt(max(np.sum(noise_psd[band]),  EPS)))
    signal_rms = float(np.sqrt(max(np.sum(signal_psd[band]), EPS)))

    if noise_rms <= 0 or signal_rms <= 0:
        return float("nan")

    return float(20.0 * np.log10(signal_rms / noise_rms))


def one_sided_psd(x: np.ndarray, fs: float) -> Tuple[np.ndarray, np.ndarray]:
    """Compute one-sided PSD (power spectral density)."""
    x = demean(np.asarray(x, dtype=np.float64))
    n = x.size
    if n < 8:
        return np.array([], dtype=float), np.array([], dtype=float)

    win = np.hanning(n)
    xw = x * win
    freq = np.fft.rfftfreq(n, d=1.0 / fs)
    spec = np.fft.rfft(xw)

    # Periodogram PSD normalized so sum(psd)*df matches time-domain mean-square.
    psd = (np.abs(spec) ** 2) / (fs * np.sum(win ** 2))
    if psd.size > 2:
        if n % 2 == 0:
            psd[1:-1] *= 2.0
        else:
            psd[1:] *= 2.0
    return freq, psd


def fft_db_amplitude(x: np.ndarray, fs: float) -> Tuple[np.ndarray, np.ndarray]:
    """Compute FFT dB amplitude for display (bottom-left plot)."""
    x = demean(np.asarray(x, dtype=np.float64))
    n = x.size
    if n < 8:
        return np.array([], dtype=float), np.array([], dtype=float)

    win = np.hanning(n)
    coherent_gain = max(np.sum(win) / n, EPS)
    freq = np.fft.rfftfreq(n, d=1.0 / fs)
    mag = np.abs(np.fft.rfft(x * win)) / (n * coherent_gain / 2.0)
    db = 20.0 * np.log10(np.maximum(mag, EPS))
    return freq, db


def third_octave_levels(x: np.ndarray, fs: float) -> np.ndarray:
    """Compute per-band dB SPL for 1/3 octave bands."""
    freq, psd = one_sided_psd(x, fs)
    if freq.size == 0:
        return np.full_like(THIRD_OCTAVE_CENTER_FREQS, np.nan, dtype=float)

    df = fs / max(len(demean(x)), 1)
    nyquist = fs / 2.0
    levels = []

    for fc in THIRD_OCTAVE_CENTER_FREQS:
        # 1/3 octave band edge: fc * 2^(+-1/6)
        f1 = fc / (2.0 ** (1.0 / 6.0))
        f2 = fc * (2.0 ** (1.0 / 6.0))

        if f1 >= nyquist:
            levels.append(np.nan)
            continue

        mask = (freq >= f1) & (freq < min(f2, nyquist))
        if not np.any(mask):
            levels.append(np.nan)
            continue

        mean_square = float(np.sum(psd[mask]) * df)
        levels.append(pressure_to_db_spl(np.sqrt(max(mean_square, EPS))))

    return np.asarray(levels, dtype=float)


def spectrum(x: np.ndarray, fs: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Single windowed FFT -> (freq, psd, db_amplitude, df).

    Combines what one_sided_psd() and fft_db_amplitude() did separately, so the realtime
    loop computes ONE rfft per frame instead of two (halves the per-frame DSP cost).
    """
    x = demean(np.asarray(x, dtype=np.float64))
    n = x.size
    if n < 8:
        empty = np.array([], dtype=float)
        return empty, empty, empty, 0.0

    win, win_sum, win_sumsq = _hann(n)
    freq = np.fft.rfftfreq(n, d=1.0 / fs)
    spec = np.fft.rfft(x * win)
    mag_raw = np.abs(spec)

    # One-sided PSD (matches one_sided_psd): sum(psd)*df == time-domain mean-square.
    psd = (mag_raw ** 2) / (fs * win_sumsq)
    if psd.size > 2:
        if n % 2 == 0:
            psd[1:-1] *= 2.0
        else:
            psd[1:] *= 2.0

    # dB amplitude for display (matches fft_db_amplitude): coherent-gain corrected.
    coherent_gain = max(win_sum / n, EPS)
    mag = mag_raw / (n * coherent_gain / 2.0)
    db = 20.0 * np.log10(np.maximum(mag, EPS))

    # Constant so an AVERAGED PSD can be shown on the SAME display-dB axis:
    #   display_db = 10*log10(psd) + k_db   (exact for interior bins; DC/Nyquist off by 3dB, irrelevant)
    k_db = 10.0 * np.log10(2.0 * fs * win_sumsq / (win_sum ** 2))

    return freq, psd, db, fs / n, k_db


@functools.lru_cache(maxsize=4)
def _hann(n: int):
    """Hann window and its sums for a given length - the length rarely changes."""
    win = np.hanning(n)
    return win, float(np.sum(win)), float(np.sum(win ** 2))


def db_from_psd(psd: np.ndarray, k_db: float) -> np.ndarray:
    """Convert a (possibly time-averaged) one-sided PSD back to the display-dB scale."""
    return 10.0 * np.log10(np.maximum(psd, EPS)) + k_db


def third_octave_from_psd(freq: np.ndarray, psd: np.ndarray, df: float, nyquist: float) -> np.ndarray:
    """1/3-octave band dB SPL from a precomputed PSD (avoids recomputing the FFT)."""
    if freq.size == 0:
        return np.full_like(THIRD_OCTAVE_CENTER_FREQS, np.nan, dtype=float)

    # freq is ascending, so every band is one contiguous slice [lo, hi): sum it from a
    # cumulative sum instead of building one boolean mask per band (38 passes over the PSD).
    csum = np.concatenate(([0.0], np.cumsum(psd)))
    lo = np.searchsorted(freq, THIRD_OCTAVE_F1, side="left")
    hi = np.searchsorted(freq, np.minimum(THIRD_OCTAVE_F2, nyquist), side="left")
    mean_square = (csum[hi] - csum[lo]) * df
    levels = 20.0 * np.log10(np.sqrt(np.maximum(mean_square, EPS)) / P_REF) + CAL_DB_OFFSET
    levels[(THIRD_OCTAVE_F1 >= nyquist) | (hi <= lo)] = np.nan
    return levels


def band_metrics(freq: np.ndarray, psd: np.ndarray, db: np.ndarray, df: float,
                 fmin: float, fmax: float) -> Tuple[float, float, float]:
    """For a focus window [fmin,fmax]: (band level dB SPL, peak freq Hz, peak amp dB).

    - band level: integrates PSD over the WHOLE window -> total band power. This is dominated
      by the broadband floor across the window, so it does NOT track a narrow spur well.
    - peak freq / peak amp: the strongest single bin (the SPUR height you see in the FFT).
      Use peak amp to compare a narrow spur across experiments; use band level for broadband.
    """
    if freq.size == 0:
        return float("nan"), float("nan"), float("nan")
    mask = (freq >= fmin) & (freq <= fmax)
    if not np.any(mask):
        return float("nan"), float("nan"), float("nan")

    mean_square = float(np.sum(psd[mask]) * df)
    level = pressure_to_db_spl(np.sqrt(max(mean_square, EPS)))

    dbm = db[mask]
    fm = freq[mask]
    valid = np.isfinite(dbm)
    if np.any(valid):
        i = int(np.argmax(dbm[valid]))
        peak_freq = float(fm[valid][i])
        peak_db = float(dbm[valid][i])
    else:
        peak_freq = float("nan")
        peak_db = float("nan")
    return level, peak_freq, peak_db


# =========================
# Spur detection
# =========================
def parse_spur_points(text: str) -> Tuple[np.ndarray, np.ndarray]:
    """Parse 'freq:margin' (or 'margin@freq') comma/semicolon list -> (freqs, margins) sorted by freq.

    Frequency accepts a k/K suffix (12k = 12000). Margin is dB above the local floor.
    Falls back to SPUR_THRESH_DEFAULT on empty/garbage input.
    """
    def _num(s: str) -> float:
        s = s.strip().lower().replace("db", "").replace("hz", "")
        mult = 1.0
        if s.endswith("k"):
            mult, s = 1000.0, s[:-1]
        return float(s) * mult

    pts = []
    for tok in text.replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            if "@" in tok:            # "margin@freq"  (matches the "+10dB@12k" notation)
                m, f = tok.split("@", 1)
                pts.append((_num(f), _num(m)))
            elif ":" in tok:          # "freq:margin"
                f, m = tok.split(":", 1)
                pts.append((_num(f), _num(m)))
        except Exception:
            pass

    if not pts:
        for tok in SPUR_THRESH_DEFAULT.split(","):
            f, m = tok.split(":")
            pts.append((_num(f), _num(m)))

    pts.sort()
    xs = np.array([p[0] for p in pts], dtype=float)
    ys = np.array([p[1] for p in pts], dtype=float)
    return xs, ys


def local_floor_db(freq: np.ndarray, db: np.ndarray, n_seg: int = 40) -> np.ndarray:
    """Piecewise-median noise floor that follows the spectral slope (robust to spurs).

    Splits the band into n_seg segments, takes the median in each (spurs are a minority so
    the median tracks the floor, not the peaks), and interpolates back to every bin.
    """
    n = freq.size
    if n < 8:
        return np.full(n, float(np.median(db)) if n else 0.0)
    edges = np.linspace(freq[0], freq[-1], n_seg + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    # freq is ascending, so each segment is one contiguous slice.
    bounds = np.concatenate(([0], np.searchsorted(freq, edges[1:-1], side="left"), [n]))
    fl = np.full(n_seg, np.nan)
    for i in range(n_seg):
        seg = db[bounds[i]:bounds[i + 1]]
        if seg.size:
            fl[i] = np.median(seg)
    good = np.isfinite(fl)
    if int(good.sum()) < 2:
        return np.full(n, float(np.median(db)))
    return np.interp(freq, centers[good], fl[good])


def detect_spurs(freq: np.ndarray, db: np.ndarray, thr_freqs: np.ndarray, thr_margins: np.ndarray,
                 f_lo: float, f_hi: float):
    """Find spurs = bins exceeding the local floor by more than margin(freq).

    Returns (spurs, floor_median, (band_freq, floor+margin threshold curve)).
    spurs: list of (freq_hz, db, margin_above_floor_db), ONE entry per contiguous group
    (so a multi-bin peak counts once, reported at its highest bin).
    """
    m = (freq >= f_lo) & (freq <= f_hi) & np.isfinite(db)
    f = freq[m]
    d = db[m]
    if f.size < 8 or thr_freqs.size == 0:
        return [], float("nan"), (f, np.array([], dtype=float))

    floor = local_floor_db(f, d)
    margin = np.interp(f, thr_freqs, thr_margins)   # np.interp clamps flat beyond the end points
    thresh = floor + margin
    over = d > thresh

    # Vectorized contiguous-group detection (only iterate over the few groups, not every bin).
    oi = over.astype(np.int8)
    dd = np.diff(oi)
    starts = list(np.where(dd == 1)[0] + 1)
    ends = list(np.where(dd == -1)[0] + 1)
    if oi.size and oi[0]:
        starts = [0] + starts
    if oi.size and oi[-1]:
        ends = ends + [oi.size]

    spurs = []
    for s, e in zip(starts, ends):
        k = s + int(np.argmax(d[s:e]))
        spurs.append((float(f[k]), float(d[k]), float(d[k] - floor[k])))

    return spurs, float(np.median(d)), (f, thresh)


# =========================
# Ring buffer for fast data accumulation
# =========================
class RingBuffer:
    def __init__(self, capacity: int):
        self.capacity = max(8, int(capacity))
        self.data = np.zeros(self.capacity, dtype=np.float64)
        self.pos = 0
        self.count = 0

    def reset(self, capacity: Optional[int] = None) -> None:
        if capacity is not None and int(capacity) != self.capacity:
            self.capacity = max(8, int(capacity))
            self.data = np.zeros(self.capacity, dtype=np.float64)
        else:
            self.data.fill(0.0)
        self.pos = 0
        self.count = 0

    def extend(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=np.float64).ravel()
        if x.size == 0:
            return

        if x.size >= self.capacity:
            self.data[:] = x[-self.capacity:]
            self.pos = 0
            self.count = self.capacity
            return

        end = self.pos + x.size
        if end <= self.capacity:
            self.data[self.pos:end] = x
        else:
            split = self.capacity - self.pos
            self.data[self.pos:] = x[:split]
            self.data[:end - self.capacity] = x[split:]

        self.pos = end % self.capacity
        self.count = min(self.capacity, self.count + x.size)

    def get(self) -> np.ndarray:
        if self.count == 0:
            return np.array([], dtype=np.float64)
        if self.count < self.capacity:
            return self.data[:self.count].copy()
        return np.concatenate((self.data[self.pos:], self.data[:self.pos]))


# =========================
# PRISM reader thread
# =========================
@dataclass
class PrismConfig:
    ip: str
    port: int
    sample_rate_index: int
    sensitivity: float
    use_iepe: bool
    scan_mask: int = SCAN_MASK       # scan only needed channel(s) -> less data at high fs


class PrismReader(threading.Thread):
    """Read device in a separate thread to prevent GUI freeze."""

    def __init__(
        self,
        config: PrismConfig,
        data_queue: queue.Queue,
        message_queue: queue.Queue,
        stop_event: threading.Event,
    ):
        super().__init__(daemon=True)
        self.config = config
        self.data_queue = data_queue
        self.message_queue = message_queue
        self.stop_event = stop_event
        self.total_samples = 0

    def run(self) -> None:
        prism = None
        use_iepe = False
        try:
            fs = sr_hz(self.config.sample_rate_index)
            self.message_queue.put(("status", f"Connecting to {self.config.ip}:{self.config.port} ..."))

            prism = prismlib(self.config.ip, self.config.port)
            prism.open()
            prism.sampleRate_write(self.config.sample_rate_index)

            for ch in range(4):
                prism.sens_write(ch, self.config.sensitivity)

            use_iepe = self.config.use_iepe
            if use_iepe:
                for ch in range(4):
                    prism.iepe_write(ch, True)
                self.message_queue.put(("status", "Waiting for IEPE stabilization..."))
                time.sleep(2.0)

            prism.scan_start(self.config.scan_mask, SCAN_BUFFER_SAMPLES, ScanOptions.CONTINUOUS)
            n_ch = prism.scan_ch_count()
            self.message_queue.put(("connected", f"Scanning [{sr_name(self.config.sample_rate_index)}] mask=0x{self.config.scan_mask:X}, channels={n_ch}"))

            overrun_count = 0
            status = ScanStatus.RUNNING
            while (status & ScanStatus.RUNNING) and not self.stop_event.is_set():
                # numpy form: no per-sample Python list conversion in the hot loop
                mat, status = prism.scan_read_numpy(READ_SAMPLES_PER_CHANNEL, timeout=0.5)

                if status & ScanStatus.BUFFER_OVERRUN:
                    # Do NOT stop on a transient overrun - keep running (data gap only).
                    # Warn at most ~once per 10 events to avoid flooding the message queue.
                    overrun_count += 1
                    if overrun_count % 10 == 1:
                        self.message_queue.put(("warning", f"buffer overrun x{overrun_count} (display may glitch; lower fs/period if persistent)"))
                    if not (status & ScanStatus.RUNNING):
                        break   # driver ended the scan; nothing left to read

                if mat.size == 0:
                    continue

                self.total_samples += mat.shape[0]
                packet = (time.monotonic(), fs, mat, n_ch, self.total_samples)

                # Keep latest data even if GUI is temporarily slow
                try:
                    self.data_queue.put_nowait(packet)
                except queue.Full:
                    try:
                        self.data_queue.get_nowait()
                    except queue.Empty:
                        pass
                    self.data_queue.put_nowait(packet)

        except Exception:
            self.message_queue.put(("error", traceback.format_exc()))

        finally:
            if prism is not None:
                try:
                    if prism.scan_status()[0] & ScanStatus.RUNNING:
                        prism.scan_stop()
                        while prism.scan_status()[0] & ScanStatus.RUNNING:
                            time.sleep(0.005)
                    prism.scan_cleanup()
                except Exception:
                    pass

                if use_iepe:
                    try:
                        for ch in range(4):
                            prism.iepe_write(ch, False)
                    except Exception:
                        pass

                try:
                    prism.close()
                except Exception:
                    pass

            self.message_queue.put(("stopped", "Stopped"))


# =========================
# GUI App
# =========================
class RealtimeOctaveApp:
    def __init__(self, root: tk.Tk, default_ip: str, default_port: int, default_sr: int):
        self.root = root
        self.root.title("PRISM ANALYZER - 1/3 Octave Band (1.9.8)")
        # Default window: 16:9 like a typical monitor, 80 % of the screen width,
        # and never larger than the screen (the device desktop is 1920x1080).
        # The canvas scales with the window, so resizing/maximizing is fine.
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = int(sw * 0.8)
        h = w * 9 // 16
        if h > sh - 120:                     # tall screens: fit the height instead
            h = sh - 120
            w = h * 16 // 9
        self.root.geometry(f"{w}x{h}")

        self.data_queue: queue.Queue = queue.Queue(maxsize=20)
        self.message_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.reader: Optional[PrismReader] = None

        self.current_fs = sr_hz(default_sr)
        self.ring = RingBuffer(capacity=int(self.current_fs * ANALYSIS_WINDOW_SEC))
        self.history_t = []
        self.history_rms = []
        self.start_time = time.monotonic()
        self.last_total_samples = 0

        # TI-method SNR measurement state
        self.snr_noise_psd = None    # Step 1: noise PSD
        self.snr_signal_psd = None   # Step 2: signal PSD
        self.snr_freq = None         # Shared frequency axis
        self.snr_value = float("nan")  # Final SNR result
        self.snr_capture_req = None  # "noise"|"signal"|None

        # Focus-band CUMULATIVE stats (since start / band change - NOT rolling)
        self.focus_acc = None         # accumulator dict: n, sums, min/max for band level & peak amp
        self.last_focus_key = None    # (fmin,fmax) to detect band edits -> reset stats
        self.psd_avg = None           # EMA of the PSD (power domain), None until first frame
        self._config_win = None       # handle to the (single) Config settings dialog
        self.floor_acc = None         # cumulative noise-floor stats (n/sum/lo/hi)
        self.spur_held = {}           # held spurs: freq_key -> (last_seen_t, freq, db, margin)

        self._build_controls(default_ip, default_port, default_sr)
        self._build_plots()
        self._schedule_update()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI layout ----------
    def _build_controls(self, default_ip: str, default_port: int, default_sr: int) -> None:
        top = ttk.Frame(self.root, padding=(8, 6))
        top.pack(side=tk.TOP, fill=tk.X)

        self.ip_var = tk.StringVar(value=default_ip)
        self.port_var = tk.StringVar(value=str(default_port))
        self.sr_var = tk.StringVar(value=sr_name(default_sr))
        self.period_var = tk.StringVar(value=f"{DEFAULT_PERIOD_SEC:.2f}")
        self.channel_var = tk.StringVar(value="ch1")
        self.iepe_var = tk.BooleanVar(value=True)
        self.sens_var = tk.StringVar(value=str(DEFAULT_SENSITIVITY))

        # Settings edited in the Config dialog (IP/Port/Period/Sens/Norm BW/PSD Avg/tau/Spur).
        self.norm_var = tk.BooleanVar(value=False)
        self.focus_min_var = tk.StringVar(value=str(int(FOCUS_BAND_MIN_HZ)))
        self.focus_max_var = tk.StringVar(value=str(int(FOCUS_BAND_MAX_HZ)))
        self.fftmax_var = tk.StringVar(value="auto")
        self.psd_avg_var = tk.BooleanVar(value=PSD_AVG_DEFAULT)
        self.psd_tau_var = tk.StringVar(value=str(PSD_AVG_TAU_SEC))
        self.spur_on_var = tk.BooleanVar(value=SPUR_ON_DEFAULT)
        self.spur_thr_var = tk.StringVar(value=SPUR_THRESH_DEFAULT)
        self.spur_lo_var = tk.StringVar(value=str(int(SPUR_BAND_LO_HZ)))
        self.spur_hi_var = tk.StringVar(value="auto")
        self.spur_hold_var = tk.StringVar(value=str(SPUR_HOLD_SEC_DEFAULT))

        # --- Toolbar keeps only frequently-changed controls ---
        ttk.Label(top, text="Sample Rate:").pack(side=tk.LEFT)
        # SR_NAME has None for the unsupported enum value - list only real rates.
        ttk.Combobox(top, textvariable=self.sr_var, values=[n for n in SR_NAME if n], width=8, state="readonly").pack(side=tk.LEFT, padx=(4, 14))

        ttk.Label(top, text="Channel:").pack(side=tk.LEFT)
        ttk.Combobox(top, textvariable=self.channel_var, values=["ch1", "ch2", "ch3", "ch4"], width=5, state="readonly").pack(side=tk.LEFT, padx=(4, 14))

        ttk.Checkbutton(top, text="IEPE", variable=self.iepe_var).pack(side=tk.LEFT, padx=(0, 14))

        ttk.Label(top, text="Focus(Hz):").pack(side=tk.LEFT)
        ttk.Entry(top, textvariable=self.focus_min_var, width=7).pack(side=tk.LEFT, padx=(4, 2))
        ttk.Entry(top, textvariable=self.focus_max_var, width=7).pack(side=tk.LEFT, padx=(2, 14))

        ttk.Label(top, text="View(Hz):").pack(side=tk.LEFT)
        ttk.Entry(top, textvariable=self.fftmax_var, width=7).pack(side=tk.LEFT, padx=(4, 14))

        self.start_btn = ttk.Button(top, text="Start", command=self.start_scan)
        self.start_btn.pack(side=tk.LEFT, padx=(4, 4))

        self.stop_btn = ttk.Button(top, text="Stop", command=self.stop_scan, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(4, 4))

        # Config popup (IP/Port/Period/Sens/Norm BW/PSD Avg/tau/Spur) - placed after Start/Stop
        ttk.Button(top, text="Config…", command=self._open_config_dialog).pack(side=tk.LEFT, padx=(4, 14))

        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=2)

        self.noise_btn = ttk.Button(top, text="[1] Measure Noise", command=self.capture_noise)
        self.noise_btn.pack(side=tk.LEFT, padx=(4, 4))

        self.signal_btn = ttk.Button(top, text="[2] Measure Signal", command=self.capture_signal)
        self.signal_btn.pack(side=tk.LEFT, padx=(4, 4))

        self.reset_snr_btn = ttk.Button(top, text="Reset SNR", command=self.reset_snr)
        self.reset_snr_btn.pack(side=tk.LEFT, padx=(4, 14))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(top, textvariable=self.status_var).pack(side=tk.LEFT, padx=(8, 0))

    def _build_plots(self) -> None:
        fig = Figure(figsize=(15, 8), dpi=100)
        # Outer margins are set explicitly - the matplotlib defaults leave
        # 10-12 % empty on every side of the canvas.
        gs = fig.add_gridspec(
            2,
            2,
            width_ratios=[2.15, 1.05],
            height_ratios=[1.15, 1.0],
            left=0.05,
            right=0.985,
            top=0.95,
            bottom=0.10,
            hspace=0.32,
            wspace=0.18,
        )

        self.ax_oct = fig.add_subplot(gs[0, 0])
        self.ax_num = fig.add_subplot(gs[0, 1])
        self.ax_fft = fig.add_subplot(gs[1, 0])
        self.ax_time = fig.add_subplot(gs[1, 1])

        # Top left: 1/3 octave bar chart
        x = np.arange(len(THIRD_OCTAVE_CENTER_FREQS))
        # One collection for all bars: 38 separate Rectangle patches cost ~25 ms per
        # frame to draw on the CM4, one PolyCollection ~2 ms.
        self._oct_x = x
        self.oct_poly = PolyCollection(self._bar_verts(np.zeros(x.size)), facecolors="C0",
                                       edgecolors="none")
        self.ax_oct.add_collection(self.oct_poly)
        self.ax_oct.set_xlim(-0.6, x.size - 0.4)
        self.ax_oct.set_title("1/3 Octave Band Levels (dB SPL)", fontsize=10)
        self.ax_oct.set_ylabel("dB SPL")
        self.ax_oct.set_ylim(0, 90)
        self.ax_oct.set_xticks(x)
        self.ax_oct.set_xticklabels([self._freq_label(f) for f in THIRD_OCTAVE_CENTER_FREQS], rotation=90, fontsize=8)
        self.ax_oct.grid(axis="y", alpha=0.25)

        # Top right: current SPL number
        self.ax_num.axis("off")
        self.spl_text = self.ax_num.text(
            0.5,
            0.92,
            "--.- dB SPL",
            ha="center",
            va="center",
            fontsize=24,
            fontweight="bold",
            transform=self.ax_num.transAxes,
        )
        self.snr_text = self.ax_num.text(
            0.5,
            0.79,
            "SNR: --.- dB",
            ha="center",
            va="center",
            fontsize=16,
            fontweight="bold",
            color="steelblue",
            transform=self.ax_num.transAxes,
        )
        self.snr_label_text = self.ax_num.text(
            0.5,
            0.72,
            "Step 1: Noise  |  Step 2: Signal",
            ha="center",
            va="center",
            fontsize=8,
            color="gray",
            transform=self.ax_num.transAxes,
        )
        # Focus-band rolling stats (avg is the number to compare across experiments)
        self.focus_text = self.ax_num.text(
            0.5,
            0.52,
            "Focus --.- - --.- kHz   (n=0)\nband avg --.- dB   (min --.- / max --.-)\nPEAK --.- kHz  avg --.-  (min --.- / max --.-) dB",
            ha="center",
            va="center",
            fontsize=11,                 # fits the panel at the default (16:9, 1536 px) window
            fontweight="bold",
            color="firebrick",
            linespacing=1.5,
            transform=self.ax_num.transAxes,
        )
        # Noise-floor readout (cumulative avg/min/max, same style as the Focus peak) - always on
        self.floor_text = self.ax_num.text(
            0.5,
            0.28,
            "Noise floor  avg --.-  (min --.- / max --.-) dB",
            ha="center",
            va="center",
            fontsize=12,
            fontweight="bold",
            color="seagreen",
            transform=self.ax_num.transAxes,
        )
        # Spur list (held with timeout, ascending frequency) - small, below the floor line
        self.spur_text = self.ax_num.text(
            0.5,
            0.12,
            "",
            ha="center",
            va="center",
            fontsize=9,
            color="purple",
            linespacing=1.4,
            transform=self.ax_num.transAxes,
        )

        # Bottom left: FFT - shown as the peak envelope per display column (see
        # _update_fft_plot), so the line stays short and cheap to draw.
        self.fft_line, = self.ax_fft.plot([], [], linewidth=0.9)
        # Spur overlay on the FFT: dashed threshold curve (floor+margin) and red-x markers
        self.spur_thresh_line, = self.ax_fft.plot([], [], color="orange", linewidth=0.8,
                                                   linestyle="--", alpha=0.7)
        self.spur_markers, = self.ax_fft.plot([], [], linestyle="none", marker="x",
                                              color="red", markersize=8, markeredgewidth=1.5)
        self.ax_fft.set_title("FFT of Signal", fontsize=10)
        self.ax_fft.set_xlabel("Frequency (Hz)")
        self.ax_fft.set_ylabel("Amplitude (dB)")
        self.ax_fft.set_xlim(10, self.current_fs / 2.0)   # default view = Nyquist
        self.ax_fft.set_ylim(-120, 20)
        self.ax_fft.grid(alpha=0.25)

        # Bottom right: RMS time history
        self.time_line, = self.ax_time.plot([], [], linewidth=1.0)
        self.ax_time.set_title("Time vs RMS", fontsize=10)
        self.ax_time.set_xlabel("Time (s, 0 = now)")
        self.ax_time.set_ylabel("RMS")
        # Fixed "seconds before now" axis: a scrolling axis would need a full redraw per frame.
        self.ax_time.set_xlim(-TIME_HISTORY_SEC, 0)
        self.ax_time.set_ylim(0, 1)
        self.ax_time.grid(alpha=0.25)

        # Blitting.  A full draw (axes, ticks, labels, grid - ~100 texts and ~230
        # lines) costs 0.6-1.3 s on the CM4.  Everything that changes per frame is
        # marked "animated": the full draw skips it, and each frame restores a cached
        # background and paints only these artists.  Two cached layers:
        #   _bg   static layer (axes, ticks, labels, grid) - redrawn only when it
        #         changes (axis limits, background texts, resize)
        #   _bg2  _bg + the numeric readouts - text is costly to render, so the
        #         readouts are refreshed every TEXT_REFRESH_FRAMES frames only
        self._plots = [self.oct_poly, self.fft_line, self.spur_thresh_line, self.spur_markers,
                       self.time_line]
        self._texts = [self.ax_fft.title, self.spl_text, self.focus_text, self.floor_text,
                       self.spur_text]
        for a in self._plots + self._texts:
            a.set_animated(True)
        self._bg = None                  # static layer; None = needs a full draw
        self._bg2 = None                 # static layer + readouts
        self._frame = 0
        self._oct_normalized = False     # current octave-chart mode (title/ylabel are static)

        self.canvas = FigureCanvasTkAgg(fig, master=self.root)
        self.canvas.mpl_connect("draw_event", self._on_full_draw)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # ---------- Blitting ----------
    def _on_full_draw(self, _event) -> None:
        """After every full draw (first draw, resize, limit change): cache the static
        background, then paint the animated artists so the screen is complete even
        when no frame follows (scan stopped)."""
        if self.canvas.is_saving():
            return
        fig = self.canvas.figure
        self._bg = self.canvas.copy_from_bbox(fig.bbox)
        for a in self._texts:
            fig.draw_artist(a)
        self._bg2 = self.canvas.copy_from_bbox(fig.bbox)
        for a in self._plots:
            fig.draw_artist(a)

    def _blit(self) -> None:
        """Paint the per-frame artists over the cached background - no full redraw."""
        if self._bg is None:
            self.canvas.draw()           # full draw; _on_full_draw caches and paints
            return
        fig = self.canvas.figure
        self._frame += 1
        texts_refreshed = self._bg2 is None or self._frame % TEXT_REFRESH_FRAMES == 0
        if texts_refreshed:
            self.canvas.restore_region(self._bg)
            for a in self._texts:
                fig.draw_artist(a)
            self._bg2 = self.canvas.copy_from_bbox(fig.bbox)
        else:
            self.canvas.restore_region(self._bg2)
        for a in self._plots:
            fig.draw_artist(a)
        # Copying to Tk costs ~55 us per 1000 px: copy the plot areas every frame
        # and the readout panel (up to the canvas edges, texts may overflow it) only
        # when the readouts were redrawn.
        left = Bbox.union([self.ax_oct.bbox, self.ax_fft.bbox])
        self.canvas.blit(left)
        self.canvas.blit(self.ax_time.bbox)
        if texts_refreshed:
            self.canvas.blit(Bbox.from_extents(left.x1, self.ax_time.bbox.y1,
                                               fig.bbox.x1, fig.bbox.y1))

    def _set_lims(self, ax, xlim=None, ylim=None) -> None:
        """Apply axis limits only when they differ - a change costs a full redraw."""
        changed = False
        if xlim is not None and tuple(ax.get_xlim()) != tuple(xlim):
            ax.set_xlim(*xlim)
            changed = True
        if ylim is not None and tuple(ax.get_ylim()) != tuple(ylim):
            ax.set_ylim(*ylim)
            changed = True
        if changed:
            self._bg = None

    def _bar_verts(self, heights: np.ndarray) -> np.ndarray:
        """Quad vertices (N, 4, 2) for bars of width 0.8 centred on the band index."""
        x = self._oct_x
        v = np.zeros((x.size, 4, 2))
        v[:, 0, 0] = v[:, 1, 0] = x - 0.4
        v[:, 2, 0] = v[:, 3, 0] = x + 0.4
        v[:, 1, 1] = v[:, 2, 1] = heights
        return v

    @staticmethod
    def _freq_label(freq: float) -> str:
        if freq >= 1000:
            return f"{freq / 1000:.1f} kHz"
        return f"{freq:.0f} Hz"

    # ---------- Start/Stop ----------
    def start_scan(self) -> None:
        if self.reader is not None and self.reader.is_alive():
            return

        try:
            ip = self.ip_var.get().strip()
            port = int(self.port_var.get().strip())
            sr_index = SR_NAME.index(self.sr_var.get())
            sensitivity = float(self.sens_var.get().strip())
            _ = self._update_period_ms()
        except Exception as exc:
            messagebox.showerror("Input error", f"Check input values.\n{exc}")
            return

        self.current_fs = sr_hz(sr_index)
        ring_capacity = int(min(self.current_fs * ANALYSIS_WINDOW_SEC, MAX_ANALYSIS_SAMPLES * 2))
        self.ring.reset(capacity=ring_capacity)
        # Fresh auto-range for the new run (while running, the ranges only grow).
        self.ax_oct.set_ylim(0, 90)
        self.ax_fft.set_ylim(-120, 20)
        self.ax_time.set_ylim(0, 1)
        self._bg = None
        self.history_t.clear()
        self.history_rms.clear()
        self.psd_avg = None            # reset spectral average for a new run
        self.focus_acc = None          # reset focus cumulative stats for a new run
        self.floor_acc = None          # reset noise-floor cumulative stats
        self.spur_held.clear()         # reset held spurs
        self.start_time = time.monotonic()
        self.last_total_samples = 0

        while not self.data_queue.empty():
            try:
                self.data_queue.get_nowait()
            except queue.Empty:
                break

        while not self.message_queue.empty():
            try:
                self.message_queue.get_nowait()
            except queue.Empty:
                break

        self.stop_event.clear()
        ch_idx = self._selected_channel_index()
        # Only the selected channel is scanned; changing it requires Stop -> Start.
        scan_mask = (1 << ch_idx) if 0 <= ch_idx < 4 else 0x01
        config = PrismConfig(
            ip=ip,
            port=port,
            sample_rate_index=sr_index,
            sensitivity=sensitivity,
            use_iepe=bool(self.iepe_var.get()),
            scan_mask=scan_mask,
        )
        self.reader = PrismReader(config, self.data_queue, self.message_queue, self.stop_event)
        self.reader.start()

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_var.set("Starting...")

    def stop_scan(self) -> None:
        self.stop_event.set()
        self.status_var.set("Stopping...")
        self.stop_btn.config(state=tk.DISABLED)

    def on_close(self) -> None:
        self.stop_event.set()
        if self.reader is not None and self.reader.is_alive():
            self.reader.join(timeout=2.0)
        self.root.destroy()

    # ---------- Realtime update ----------
    def _schedule_update(self) -> None:
        t0 = time.perf_counter()
        self._update_gui_once()
        # Next frame after the period minus what this one took; when a frame is
        # slow, still leave at least a third of its duration for the event loop so
        # the controls stay responsive.
        spent_ms = int((time.perf_counter() - t0) * 1000)
        gap_ms = max(self._update_period_ms() - spent_ms, min(spent_ms // 3, 100), 30)
        self.root.after(gap_ms, self._schedule_update)

    def _update_period_ms(self) -> int:
        try:
            period = float(self.period_var.get().strip())
        except Exception:
            period = DEFAULT_PERIOD_SEC
        period = min(max(period, 0.03), 5.0)
        return int(period * 1000)

    def _selected_channel_index(self) -> int:
        try:
            return int(self.channel_var.get().replace("ch", "")) - 1
        except Exception:
            return DEFAULT_CHANNEL

    def _drain_messages(self) -> None:
        while True:
            try:
                kind, msg = self.message_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "error":
                self.status_var.set("Error")
                self.start_btn.config(state=tk.NORMAL)
                self.stop_btn.config(state=tk.DISABLED)
                messagebox.showerror("PRISM error", msg)
            elif kind == "stopped":
                self.status_var.set(msg)
                self.start_btn.config(state=tk.NORMAL)
                self.stop_btn.config(state=tk.DISABLED)
            else:
                self.status_var.set(msg)

    def _drain_data(self) -> bool:
        got_data = False
        ch_idx = self._selected_channel_index()

        while True:
            try:
                _t, fs, mat, n_ch, total = self.data_queue.get_nowait()
            except queue.Empty:
                break

            got_data = True
            self.current_fs = fs
            self.last_total_samples = total

            if ch_idx >= n_ch:
                ch_idx = 0
            self.ring.extend(mat[:, ch_idx])

        return got_data

    def _update_gui_once(self) -> None:
        self._drain_messages()
        got_data = self._drain_data()

        # Restore button state if reader stopped externally
        if self.reader is not None and not self.reader.is_alive() and self.stop_btn["state"] != tk.DISABLED:
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)

        if not got_data:
            return

        x = self.ring.get()
        if x.size < 16:
            return

        analysis_n = min(x.size, int(self.current_fs * ANALYSIS_WINDOW_SEC), MAX_ANALYSIS_SAMPLES)
        x = x[-analysis_n:]

        spl = overall_spl(x)
        rms_value = rms(demean(x))
        freq, psd, fft_db, df, k_db = spectrum(x, self.current_fs)   # one FFT, reused below

        # Exponential PSD averaging (power domain). Selectable via "PSD Avg".
        if bool(self.psd_avg_var.get()) and psd.size:
            if self.psd_avg is None or self.psd_avg.size != psd.size:
                self.psd_avg = psd.copy()          # (re)init on start / sample-count change
            else:
                alpha = self._psd_alpha()
                self.psd_avg = alpha * psd + (1.0 - alpha) * self.psd_avg
            psd_use = self.psd_avg
            fft_db = db_from_psd(psd_use, k_db)     # show the smoothed spectrum
        else:
            self.psd_avg = None
            psd_use = psd

        levels = third_octave_from_psd(freq, psd_use, df, self.current_fs / 2.0)

        self._update_octave_plot(levels)
        self._update_spl_number(spl)
        # SNR is measured explicitly via buttons (TI method - not updated in real time)
        self._update_fft_plot(freq, fft_db)
        self._update_spurs(freq, fft_db)   # spur markers + floor/count readout
        # Focus: band-level stats from the raw PSD, peak frequency from the displayed (averaged) dB.
        self._update_focus(freq, psd, fft_db, df)
        self._update_time_plot(rms_value)

        self._blit()

    def _update_octave_plot(self, levels: np.ndarray) -> None:
        normalize = bool(self.norm_var.get())

        vals = np.asarray(levels, dtype=float)
        if normalize:
            # Remove the proportional-bandwidth slope so bands are comparable:
            # BW is proportional to fc, so subtract 10*log10(fc/ref). White noise -> flat line.
            vals = vals - 10.0 * np.log10(THIRD_OCTAVE_CENTER_FREQS / THIRD_OCTAVE_NORM_REF_HZ)

        show_levels = np.nan_to_num(vals, nan=0.0, neginf=0.0, posinf=0.0)
        # In normalized mode values can be negative; keep them, otherwise clamp at 0.
        self.oct_poly.set_verts(self._bar_verts(show_levels if normalize
                                                else np.maximum(show_levels, 0.0)))

        if normalize != self._oct_normalized:      # title/ylabel are in the static layer
            self._oct_normalized = normalize
            if normalize:
                self.ax_oct.set_title(
                    "1/3 Octave - BW-normalized to 1kHz (flat = white noise)", fontsize=10)
                self.ax_oct.set_ylabel("dB (norm)")
            else:
                self.ax_oct.set_title("1/3 Octave Band Levels (dB SPL)", fontsize=10)
                self.ax_oct.set_ylabel("dB SPL")
            self._bg = None

        finite = show_levels[np.isfinite(show_levels)]
        if finite.size:
            vmax = float(np.max(finite))
            if normalize:
                vmin = float(np.min(finite))
                lo_t = min(0.0, vmin) - 5.0
                hi_t = min(140.0, max(vmax + 10.0, 10.0))
            else:
                lo_t = 0.0
                hi_t = min(140.0, max(90.0, vmax + 10.0))
            # A limit change costs a full redraw (~0.7 s on the CM4): the range only
            # grows (with headroom) and shrinks only when it has become grossly too
            # large.  Start resets it.
            cur_lo, cur_hi = self.ax_oct.get_ylim()
            if lo_t < cur_lo or hi_t > cur_hi or cur_hi - hi_t > 40.0 or lo_t - cur_lo > 40.0:
                pad = 10.0 if hi_t < 140.0 and hi_t > (10.0 if normalize else 90.0) else 0.0
                self._set_lims(self.ax_oct, ylim=(lo_t, min(140.0, hi_t + pad)))

    # ---------- Focus band rolling stats ----------
    def _psd_alpha(self) -> float:
        """EMA weight per frame: alpha = period/tau (averages ~tau/period frames)."""
        try:
            tau = float(self.psd_tau_var.get())
        except Exception:
            tau = PSD_AVG_TAU_SEC
        tau = max(tau, 1e-3)
        period = self._update_period_ms() / 1000.0
        return float(min(1.0, max(period / tau, 1e-3)))

    def _read_fft_xmax(self, nyq: float) -> float:
        """FFT view upper limit: 'auto'/blank -> Nyquist; a number -> clamped to Nyquist."""
        s = self.fftmax_var.get().strip().lower()
        if s in ("", "auto", "nyq", "nyquist"):
            return nyq
        try:
            v = float(s)
        except Exception:
            return nyq
        return min(v, nyq) if v > 0 else nyq

    def _read_focus_band(self) -> Tuple[float, float]:
        try:
            fmin = float(self.focus_min_var.get())
            fmax = float(self.focus_max_var.get())
        except Exception:
            fmin, fmax = FOCUS_BAND_MIN_HZ, FOCUS_BAND_MAX_HZ
        if not (fmax > fmin >= 0):
            fmin, fmax = FOCUS_BAND_MIN_HZ, FOCUS_BAND_MAX_HZ
        return fmin, fmax

    def _update_focus(self, freq: np.ndarray, psd: np.ndarray, db: np.ndarray, df: float) -> None:
        if freq.size == 0:
            return

        fmin, fmax = self._read_focus_band()
        key = (fmin, fmax)
        if key != self.last_focus_key:
            self.focus_acc = None           # band edited -> reset cumulative stats
            self.last_focus_key = key

        level, peak_freq, peak_db = band_metrics(freq, psd, db, df, fmin, fmax)
        if not (np.isfinite(level) and np.isfinite(peak_db) and np.isfinite(peak_freq)):
            return

        # Cumulative since start/reset: avg = running mean, min/max = held extremes (do NOT decay).
        a = self.focus_acc
        if a is None:
            a = self.focus_acc = {"n": 0, "bsum": 0.0, "blo": level, "bhi": level,
                                  "psum": 0.0, "plo": peak_db, "phi": peak_db, "pfsum": 0.0}
        a["n"] += 1
        a["bsum"] += level
        a["blo"] = min(a["blo"], level)
        a["bhi"] = max(a["bhi"], level)
        a["psum"] += peak_db
        a["plo"] = min(a["plo"], peak_db)
        a["phi"] = max(a["phi"], peak_db)
        a["pfsum"] += peak_freq

        n = a["n"]
        self.focus_text.set_text(
            f"Focus {fmin / 1000:.1f} - {fmax / 1000:.1f} kHz   (n={n})\n"
            f"band avg {a['bsum'] / n:.1f} dB   (min {a['blo']:.1f} / max {a['bhi']:.1f})\n"
            f"PEAK {a['pfsum'] / n / 1000:.2f}kHz  avg {a['psum'] / n:.1f}  (min {a['plo']:.1f} / max {a['phi']:.1f}) dB"
        )

    # ---------- TI-method SNR measurement ----------
    def capture_noise(self) -> None:
        """Step 1: Capture PSD without signal as noise floor."""
        x = self.ring.get()
        if x.size < 16:
            messagebox.showwarning("SNR", "Not enough data. Start scan first.")
            return
        analysis_n = min(x.size, int(self.current_fs * ANALYSIS_WINDOW_SEC), MAX_ANALYSIS_SAMPLES)
        x = x[-analysis_n:]
        freq, psd = one_sided_psd(x, self.current_fs)
        if freq.size == 0:
            return
        self.snr_noise_psd = psd.copy()
        self.snr_freq = freq.copy()
        self.snr_signal_psd = None
        self.snr_value = float("nan")
        self.snr_label_text.set_text("Noise captured. Now play signal and press Step 2.")
        self.snr_text.set_text("SNR: --- dB")
        self.snr_text.set_color("gray")
        self.canvas.draw_idle()
        self.status_var.set("Noise PSD captured")

    def capture_signal(self) -> None:
        """Step 2: Capture PSD with signal playing and compute SNR."""
        if self.snr_noise_psd is None:
            messagebox.showwarning("SNR", "Please capture noise first (Step 1: Measure Noise).")
            return
        x = self.ring.get()
        if x.size < 16:
            messagebox.showwarning("SNR", "Not enough data.")
            return
        analysis_n = min(x.size, int(self.current_fs * ANALYSIS_WINDOW_SEC), MAX_ANALYSIS_SAMPLES)
        x = x[-analysis_n:]
        freq, psd = one_sided_psd(x, self.current_fs)
        if freq.size == 0:
            return
        # Require re-capture if frequency axis changed
        if freq.size != self.snr_freq.size:
            messagebox.showwarning("SNR", "Sample count changed. Please recapture noise (Step 1).")
            self.reset_snr()
            return
        self.snr_signal_psd = psd.copy()
        self.snr_value = compute_snr_ti(self.snr_noise_psd, self.snr_signal_psd, freq, self.current_fs)
        self._update_snr_number(self.snr_value)
        self.snr_label_text.set_text(f"TI method: Signal PSD / Noise PSD")
        self.canvas.draw_idle()
        self.status_var.set(f"SNR = {self.snr_value:.1f} dB (TI method)")

    def reset_snr(self) -> None:
        """Reset SNR measurement."""
        self.snr_noise_psd = None
        self.snr_signal_psd = None
        self.snr_freq = None
        self.snr_value = float("nan")
        self.snr_text.set_text("SNR: --- dB")
        self.snr_text.set_color("gray")
        self.snr_label_text.set_text("Step 1: Noise  |  Step 2: Signal")
        self.canvas.draw_idle()
        self.status_var.set("SNR reset")

    def _update_spl_number(self, spl: float) -> None:
        self.spl_text.set_text(f"{spl:.1f} dB SPL")

    def _update_snr_number(self, snr: float) -> None:
        if np.isfinite(snr):
            self.snr_text.set_text(f"SNR: {snr:.1f} dB")
            # Color indicates quality: good(green) / fair(orange) / poor(red)
            if snr >= 40:
                color = "green"
            elif snr >= 20:
                color = "darkorange"
            else:
                color = "red"
            self.snr_text.set_color(color)
        else:
            self.snr_text.set_text("SNR: --- dB")
            self.snr_text.set_color("gray")

    def _update_fft_plot(self, freq: np.ndarray, fft_db: np.ndarray) -> None:
        if freq.size == 0:
            return

        nyq = self.current_fs / 2.0
        xmax = self._read_fft_xmax(nyq)   # auto=Nyquist, or manual "View(Hz)"
        mask = (freq >= 10.0) & (freq <= xmax)
        f = freq[mask]
        y = fft_db[mask]
        if f.size == 0:
            return

        # Report the strongest spur in the displayed high-frequency band before downsampling
        peak_band_mask = (freq >= 9000.0) & (freq <= xmax)
        if np.any(peak_band_mask):
            pf = freq[peak_band_mask]
            py = fft_db[peak_band_mask]

            valid = np.isfinite(py)
            if np.any(valid):
                pf = pf[valid]
                py = py[valid]

                peak_idx = int(np.argmax(py))
                peak_freq = float(pf[peak_idx])
                peak_amp = float(py[peak_idx])

                noise_floor = float(np.median(py))
                peak_margin = peak_amp - noise_floor

                self.ax_fft.set_title(
                    f"FFT of Signal | Peak {peak_freq:.1f} Hz, "
                    f"{peak_amp:.1f} dB, +{peak_margin:.1f} dB",
                    fontsize=10
                )

        # Display only: the peak per display column (~500 columns), so a one-bin
        # spur is still visible (plain decimation dropped most of them at high
        # sample rates) while the line stays short and uncluttered.
        max_cols = 500
        if f.size > 2 * max_cols:
            n = (f.size // max_cols) * max_cols
            f = f[:n].reshape(max_cols, -1).mean(axis=1)
            y = y[:n].reshape(max_cols, -1).max(axis=1)
        self.fft_line.set_data(f, y)
        self._set_lims(self.ax_fft, xlim=(10.0, xmax))

        finite = y[np.isfinite(y)]
        if finite.size:
            lo = float(np.percentile(finite, 5)) - 10.0
            hi = float(np.max(finite)) + 10.0

            if hi - lo < 40.0:
                mid = (hi + lo) / 2.0
                lo = mid - 20.0
                hi = mid + 20.0

            # A limit change costs a full redraw (~0.7 s on the CM4), so the range
            # only grows (with 10 dB of headroom) and shrinks only when it has become
            # grossly too large.  Start resets it.
            cur_lo, cur_hi = self.ax_fft.get_ylim()
            if lo < cur_lo or hi > cur_hi or cur_hi - hi > 40.0 or lo - cur_lo > 40.0:
                self._set_lims(self.ax_fft, ylim=(lo - 10.0, hi + 10.0))

    # ---------- Spur detection ----------
    def _read_spur_band(self, nyq: float) -> Tuple[float, float]:
        """Spur scan band: Lo (Hz) and Hi ('auto'/blank -> Nyquist, else clamped)."""
        try:
            lo = float(self.spur_lo_var.get())
        except Exception:
            lo = SPUR_BAND_LO_HZ
        s = self.spur_hi_var.get().strip().lower()
        if s in ("", "auto", "nyq", "nyquist"):
            hi = nyq
        else:
            try:
                hi = min(float(s), nyq)
            except Exception:
                hi = nyq
        if not (hi > lo >= 0):
            lo, hi = SPUR_BAND_LO_HZ, nyq
        return lo, hi

    def _read_spur_hold(self) -> float:
        try:
            return max(0.0, float(self.spur_hold_var.get()))
        except Exception:
            return SPUR_HOLD_SEC_DEFAULT

    def _update_spurs(self, freq: np.ndarray, fft_db: np.ndarray) -> None:
        if freq.size == 0:
            return

        nyq = self.current_fs / 2.0
        f_lo, f_hi = self._read_spur_band(nyq)

        # ---- Noise floor (median): cumulative avg/min/max, same style as the Focus peak ----
        band = (freq >= f_lo) & (freq <= f_hi) & np.isfinite(fft_db)
        floor_med = float(np.median(fft_db[band])) if np.any(band) else float("nan")
        if np.isfinite(floor_med):
            a = self.floor_acc
            if a is None:
                a = self.floor_acc = {"n": 0, "sum": 0.0, "lo": floor_med, "hi": floor_med}
            a["n"] += 1
            a["sum"] += floor_med
            a["lo"] = min(a["lo"], floor_med)
            a["hi"] = max(a["hi"], floor_med)
            self.floor_text.set_text(
                f"Noise floor  avg {a['sum'] / a['n']:.1f}  (min {a['lo']:.1f} / max {a['hi']:.1f}) dB")

        # ---- Spur overlay (optional), with hold-timeout so brief spurs stay visible ----
        if not bool(self.spur_on_var.get()):
            self.spur_markers.set_data([], [])
            self.spur_thresh_line.set_data([], [])
            self.spur_text.set_text("")
            self.spur_held.clear()
            return

        xs, ys = parse_spur_points(self.spur_thr_var.get())
        spurs, _floor, (bf, thresh) = detect_spurs(freq, fft_db, xs, ys, f_lo, f_hi)

        # Threshold curve (floor + margin) so you can see what counts as a spur.
        # Display only: the curve is smooth (40 segments), so ~600 points are plenty
        # (the full band is up to ~250k bins at 512K).
        if bf.size:
            step = max(1, bf.size // 600)
            self.spur_thresh_line.set_data(bf[::step], thresh[::step])
        else:
            self.spur_thresh_line.set_data([], [])

        # Refresh currently-detected spurs; expire ones not seen for > hold seconds.
        now = time.monotonic()
        for f, d, m in spurs:
            key = int(round(f / 100.0))                 # ~100 Hz bins group the same spur across frames
            self.spur_held[key] = (now, f, d, m)
        hold = self._read_spur_hold()
        self.spur_held = {k: v for k, v in self.spur_held.items() if now - v[0] <= hold}

        held = sorted(self.spur_held.values(), key=lambda v: v[1])   # ascending frequency
        if held:
            self.spur_markers.set_data([v[1] for v in held], [v[2] for v in held])
            shown = held[:SPUR_MAX_LIST]
            items = [f"{v[3]:.0f}dB@{v[1] / 1000:.1f}kHz" for v in shown]
            lst = "\n".join("   ".join(items[i:i + 3]) for i in range(0, len(items), 3))
            more = f"   (+{len(held) - SPUR_MAX_LIST} more)" if len(held) > SPUR_MAX_LIST else ""
            self.spur_text.set_text(f"Spurs: {len(held)}\n{lst}{more}")
        else:
            self.spur_markers.set_data([], [])
            self.spur_text.set_text("Spurs: 0")

    def _open_config_dialog(self) -> None:
        """Popup holding the rarely-changed settings: connection, display/analysis, and spur."""
        if self._config_win is not None and self._config_win.winfo_exists():
            self._config_win.lift()
            return

        win = tk.Toplevel(self.root)
        self._config_win = win
        win.title("Config")
        win.geometry("520x560")
        win.transient(self.root)
        win.columnconfigure(0, weight=0)
        win.columnconfigure(1, weight=1)
        row = {"i": 0}   # mutable row counter shared with the helper closures below

        def _section(title: str) -> None:
            ttk.Separator(win, orient=tk.HORIZONTAL).grid(
                row=row["i"], column=0, columnspan=2, sticky="we", padx=8, pady=(12, 2)); row["i"] += 1
            ttk.Label(win, text=title, font=("", 9, "bold")).grid(
                row=row["i"], column=0, columnspan=2, sticky="w", padx=8); row["i"] += 1

        def _field(label: str, var, width: int = 16, hint: str = "") -> None:
            ttk.Label(win, text=label).grid(row=row["i"], column=0, sticky="w", padx=8, pady=3)
            ttk.Entry(win, textvariable=var, width=width).grid(
                row=row["i"], column=1, sticky="w", padx=8, pady=3); row["i"] += 1
            if hint:
                ttk.Label(win, text=hint, foreground="gray").grid(
                    row=row["i"], column=0, columnspan=2, sticky="w", padx=8); row["i"] += 1

        def _check(text: str, var) -> None:
            ttk.Checkbutton(win, text=text, variable=var).grid(
                row=row["i"], column=0, columnspan=2, sticky="w", padx=8, pady=3); row["i"] += 1

        _section("Connection   (applies on next Start)")
        _field("IP:", self.ip_var, 22)
        _field("Port:", self.port_var, 10)
        _field("Sensitivity:", self.sens_var, 10)

        _section("Display / analysis   (live)")
        _field("Period (s):", self.period_var, 8)
        _check("Norm BW  (flatten 1/3-oct white-noise slope)", self.norm_var)
        _check("PSD averaging  (smooth spectrum)", self.psd_avg_var)
        _field("PSD tau (s):", self.psd_tau_var, 8)

        _section("Spur detection")
        _check("Enable spur markers on FFT", self.spur_on_var)
        _field("Threshold (freq:margin_dB):", self.spur_thr_var, 30,
               "e.g.  12k:10, 30k:20   (piecewise-linear; freq accepts 'k'; also '10@12k')")
        _field("Scan Lo (Hz):", self.spur_lo_var, 10)
        _field("Scan Hi (Hz / auto):", self.spur_hi_var, 10)
        _field("Hold (s):", self.spur_hold_var, 8, "keep a spur shown this long after it disappears")

        pv = tk.StringVar(value="")

        def _validate() -> None:
            xs, ys = parse_spur_points(self.spur_thr_var.get())
            pv.set("spur points: " + ", ".join(f"{x / 1000:.1f}k:{y:.0f}dB" for x, y in zip(xs, ys)))

        ttk.Button(win, text="Validate spur", command=_validate).grid(
            row=row["i"], column=0, sticky="w", padx=8, pady=6)
        ttk.Label(win, textvariable=pv, foreground="steelblue").grid(
            row=row["i"], column=1, sticky="w", padx=8, pady=6); row["i"] += 1
        ttk.Button(win, text="OK", command=win.destroy).grid(
            row=row["i"], column=0, columnspan=2, pady=(12, 8))
        _validate()

    def _update_time_plot(self, rms_value: float) -> None:
        t = time.monotonic() - self.start_time
        self.history_t.append(t)
        self.history_rms.append(float(rms_value))

        while self.history_t and (t - self.history_t[0]) > TIME_HISTORY_SEC:
            self.history_t.pop(0)
            self.history_rms.pop(0)

        # x = seconds before now: the axis itself never moves.
        self.time_line.set_data(np.asarray(self.history_t, dtype=float) - t, self.history_rms)

        y = np.asarray(self.history_rms, dtype=float)
        y = y[np.isfinite(y)]
        if y.size:
            ymax = float(np.nanmax(y))
            # RMS >= 0, so show from 0 with headroom above (hysteresis: grow when the
            # data reaches the top, shrink only when it falls below half).
            top = ymax * 1.2 if ymax > 0 else 1.0
            cur_top = self.ax_time.get_ylim()[1]
            if top > cur_top or top < cur_top * 0.25:
                self._set_lims(self.ax_time, ylim=(0.0, top * 1.25))


# =========================
# Entry point
# =========================
def main() -> None:
    ip = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IP
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    sr = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_SAMPLE_RATE_INDEX

    if not (0 <= sr < len(SR_NAME) and SR_NAME[sr]):
        print("sample_rate: 0=64K 1=128K 3=256K 4=512K 5=32K 6=16K 7=8K 8=4K 9=2K 10=1K 11=500", file=sys.stderr)
        sys.exit(1)

    # Tk needs an X display.  Over a plain SSH session there is none - say so
    # instead of dumping a traceback.
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print("PRISM ANALYZER needs a graphical desktop: %s" % exc, file=sys.stderr)
        print("Run it from the device's desktop terminal, or over SSH with X forwarding (ssh -X).",
              file=sys.stderr)
        sys.exit(1)
    app = RealtimeOctaveApp(root, ip, port, sr)
    root.mainloop()


if __name__ == "__main__":
    main()
