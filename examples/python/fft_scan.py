#!/usr/bin/env python3
"""fft_scan.py - FFT spectrum analysis

Runs a finite scan and analyses the spectrum of each channel.
Prints peak frequencies and harmonics, and can save the result as CSV.

Usage:
    python fft_scan.py [ip] [port] [sample_rate] [channel_mask] [samples] [--csv]
    sample_rate:  0=64K 1=128K 3=256K 4=512K 5=32K 6=16K 7=8K 8=4K 9=2K 10=1K 11=500  (default 0=64K)
    channel_mask: 0x01~0x0F                           (default 0x0F = all)
    samples:      sample count (default: sample_rate, i.e. one second)

Examples:
    python fft_scan.py 192.168.7.1 7777 1 0x01 128000
    python fft_scan.py 192.168.7.1 7777 0 0x0F 64000 --csv

Requires: numpy
"""
import sys
import time
import datetime
import numpy as np
from prismlib import prismlib, ScanOptions, ScanStatus, sr_hz, sr_name


N_HARMONICS = 5      # peaks to print, including the fundamental
MIN_FREQ_HZ = 5.0    # ignore peaks below this frequency (removes DC)


# ── Argument parsing ───────────────────────────────────────────────────────
ip      = sys.argv[1]         if len(sys.argv) > 1 else "192.168.7.1"
port    = int(sys.argv[2])    if len(sys.argv) > 2 else 7777
sr      = int(sys.argv[3])    if len(sys.argv) > 3 else 0
ch_mask = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x0F
save_csv = "--csv" in sys.argv

if sr_hz(sr) <= 0:
    print("sample_rate: 0=64K 1=128K 3=256K 4=512K 5=32K 6=16K 7=8K 8=4K 9=2K 10=1K 11=500", file=sys.stderr)
    sys.exit(1)
if not (0x01 <= ch_mask <= 0x0F):
    print("channel_mask: 0x01~0x0F", file=sys.stderr)
    sys.exit(1)

fs         = sr_hz(sr)
n_samples  = int(sys.argv[5]) if len(sys.argv) > 5 else fs   # default: one second
active_chs = [ch for ch in range(4) if ch_mask & (1 << ch)]
n_ch       = len(active_chs)


# ── FFT helpers ─────────────────────────────────────────────────────────────

def analyze(signal, fs):
    """Apply a Hann window and return the one-sided spectrum: (freqs, amplitude)."""
    n        = len(signal)
    win      = np.hanning(n)
    gain     = win.mean()                   # window amplitude correction
    fft_vals = np.fft.rfft(signal * win)
    amp      = np.abs(fft_vals) / (n * gain)
    amp[1:-1] *= 2                          # one-sided correction (not DC or Nyquist)
    freqs    = np.fft.rfftfreq(n, d=1.0 / fs)
    return freqs, amp


def find_peaks(freqs, amp, n=N_HARMONICS):
    """Return the fundamental and its harmonics: [(freq_hz, amplitude), ...]."""
    freq_res = float(freqs[1]) if len(freqs) > 1 else 1.0
    min_bin  = max(1, int(np.ceil(MIN_FREQ_HZ / freq_res)))

    peak_bin = min_bin + int(np.argmax(amp[min_bin:]))
    fund_hz  = float(freqs[peak_bin])

    result = []
    for h in range(1, n + 1):
        target_bin = int(round(fund_hz * h / freq_res))
        if target_bin >= len(freqs):
            break
        lo   = max(min_bin, target_bin - 2)
        hi   = min(len(freqs) - 1, target_bin + 2)
        best = lo + int(np.argmax(amp[lo : hi + 1]))
        result.append((float(freqs[best]), float(amp[best])))
    return result


# ── Scan and analysis ─────────────────────────────────────────────────────
print(f"Connecting to {ip}:{port} ...")
prism = prismlib(ip, port)
prism.open()

try:
    prism.sampleRate_write(sr)
    print(f"Sample rate  : {sr_name(sr)} ({fs:,} Hz)")
    print(f"Channels     : {'+'.join(f'ch{c+1}' for c in active_chs)}")
    print(f"Samples/ch   : {n_samples:,}  (resolution: {fs / n_samples:.3f} Hz)")
    ans = input("Enable IEPE? [y/n]: ").strip().lower()
    use_iepe = ans in ("y", "yes")

    if use_iepe:
        for ch in active_chs:
            prism.iepe_write(ch, True)
        print("Waiting for IEPE to settle (2 s)...", end="", flush=True)
        time.sleep(2.0)
        print(" done")

    print("Collecting ...", flush=True)

    prism.scan_start(ch_mask, n_samples, ScanOptions.DEFAULT)
    data_flat, status = prism.scan_read(n_samples, timeout=60.0)

    if status & ScanStatus.BUFFER_OVERRUN:
        print("WARNING: buffer overrun; results may be inaccurate.")

    while prism.scan_status()[0] & ScanStatus.RUNNING:
        time.sleep(0.005)
    prism.scan_cleanup()

    if use_iepe:
        for ch in active_chs:
            prism.iepe_write(ch, False)

    n_got = len(data_flat) // n_ch
    if n_got < n_samples:
        print(f"WARNING: received {n_got} samples (requested {n_samples}); FFT size set to {n_got}")
        n_samples = n_got

    # ── Raw data output ────────────────────────────────────────────────────
    raw_fname = datetime.datetime.now().strftime("raw_scan_%Y%m%d_%H%M%S.txt")
    with open(raw_fname, "w") as f:
        f.write("sample\t" + "\t".join(f"ch{c+1}" for c in active_chs) + "\n")
        for i in range(n_samples):
            vals = data_flat[i * n_ch:(i + 1) * n_ch]
            f.write(f"{i}\t" + "\t".join(f"{v:.6f}" for v in vals) + "\n")
    print(f"Raw data saved: {raw_fname}  ({n_samples} samples/ch x {n_ch} ch)")

    # ── FFT analysis and output ────────────────────────────────────────────
    print()
    print("─" * 64)
    print(f"{'channel':<8}  {'fundamental (Hz)':>16}  {'amplitude':>12}  harmonics")
    print("─" * 64)

    spectra = []
    for col, ch in enumerate(active_chs):
        signal     = np.array(data_flat[col::n_ch][:n_samples])
        freqs, amp = analyze(signal, fs)
        peaks      = find_peaks(freqs, amp)
        spectra.append((ch, freqs, amp, peaks))

        if not peaks:
            print(f"ch{ch+1:<6}  (no valid peak)")
            continue

        fund_hz, fund_amp = peaks[0]
        harmonics_str = "  ".join(
            f"{h_hz:.1f}Hz({h_amp:.4f})"
            for h_hz, h_amp in peaks[1:]
        )
        print(f"ch{ch+1:<6}  {fund_hz:>16.2f}  {fund_amp:>12.6f}  {harmonics_str}")

    print("─" * 64)

    # ── CSV output ─────────────────────────────────────────────────────────────
    if save_csv and spectra:
        import os
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        fname     = f"fft_{timestamp}.csv"
        header    = "frequency_hz," + ",".join(f"ch{c+1}_amplitude" for c in active_chs)
        ref_freqs = spectra[0][1]
        cols      = [ref_freqs] + [amp for _, _, amp, _ in spectra]
        np.savetxt(fname, np.column_stack(cols),
                   delimiter=",", header=header, comments="", fmt="%.6g")
        print(f"\nCSV saved: {os.path.abspath(fname)}")

finally:
    prism.close()
