#!/usr/bin/env python3
"""fft_scan.py — FFT 주파수 분석

유한 스캔으로 데이터를 수집하고 각 채널의 주파수 스펙트럼을 분석합니다.
피크 주파수와 고조파를 출력하고 결과를 CSV로 저장할 수 있습니다.

Usage:
    python fft_scan.py [ip] [port] [sample_rate] [channel_mask] [samples] [--csv]
    sample_rate:  0=64K 1=128K 2=170K 3=256K 4=512K  (기본 0=64K)
    channel_mask: 0x01~0x0F                           (기본 0x0F=전체)
    samples:      수집 샘플 수 (기본: sample_rate = 1초 분량)

Examples:
    python fft_scan.py 192.168.7.1 7777 1 0x01 128000
    python fft_scan.py 192.168.7.1 7777 0 0x0F 64000 --csv

Requires: numpy
"""
import sys
import time
import datetime
import numpy as np
from prismlib import prismlib, ScanOptions, ScanStatus

_SR_HZ   = [64_000, 128_000, 170_000, 256_000, 512_000]
_SR_NAME = ["64K",  "128K",  "170K",  "256K",  "512K" ]

N_HARMONICS = 5      # 기본파 포함 출력할 피크 수
MIN_FREQ_HZ = 5.0    # 이 주파수 미만은 피크 탐색 제외 (DC 제거)


# ── 인자 파싱 ──────────────────────────────────────────────────────────────────
ip      = sys.argv[1]         if len(sys.argv) > 1 else "192.168.7.1"
port    = int(sys.argv[2])    if len(sys.argv) > 2 else 7777
sr      = int(sys.argv[3])    if len(sys.argv) > 3 else 0
ch_mask = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x0F
save_csv = "--csv" in sys.argv

if not (0 <= sr <= 4):
    print("sample_rate: 0=64K 1=128K 2=170K 3=256K 4=512K", file=sys.stderr)
    sys.exit(1)
if not (0x01 <= ch_mask <= 0x0F):
    print("channel_mask: 0x01~0x0F", file=sys.stderr)
    sys.exit(1)

fs         = _SR_HZ[sr]
n_samples  = int(sys.argv[5]) if len(sys.argv) > 5 else fs   # 기본: 1초 분량
active_chs = [ch for ch in range(4) if ch_mask & (1 << ch)]
n_ch       = len(active_chs)


# ── FFT 유틸 ──────────────────────────────────────────────────────────────────

def analyze(signal, fs):
    """Hann 윈도우 적용, 단측 진폭 스펙트럼 반환: (freqs, amplitude)"""
    n        = len(signal)
    win      = np.hanning(n)
    gain     = win.mean()                   # 윈도우 진폭 보정 계수
    fft_vals = np.fft.rfft(signal * win)
    amp      = np.abs(fft_vals) / (n * gain)
    amp[1:-1] *= 2                          # 단측 스펙트럼 보정 (DC·Nyquist 제외)
    freqs    = np.fft.rfftfreq(n, d=1.0 / fs)
    return freqs, amp


def find_peaks(freqs, amp, n=N_HARMONICS):
    """기본파와 고조파 목록 반환: [(freq_hz, amplitude), ...]"""
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


# ── 스캔 및 분석 ───────────────────────────────────────────────────────────────
print(f"Connecting to {ip}:{port} ...")
prism = prismlib(ip, port)
prism.open()

try:
    prism.sampleRate_write(sr)
    print(f"Sample rate  : {_SR_NAME[sr]} ({fs:,} Hz)")
    print(f"Channels     : {'+'.join(f'ch{c+1}' for c in active_chs)}")
    print(f"Samples/ch   : {n_samples:,}  (주파수 분해능: {fs / n_samples:.3f} Hz)")
    ans = input("IEPE를 활성화하시겠습니까? [y/n]: ").strip().lower()
    use_iepe = ans in ("y", "yes")

    if use_iepe:
        for ch in active_chs:
            prism.iepe_write(ch, True)
        print("IEPE 안정화 대기 중 (2초)...", end="", flush=True)
        time.sleep(2.0)
        print(" 완료")

    print("Collecting ...", flush=True)

    prism.scan_start(ch_mask, n_samples, ScanOptions.DEFAULT)
    data_flat, status = prism.scan_read(n_samples, timeout=60.0)

    if status & ScanStatus.BUFFER_OVERRUN:
        print("WARNING: buffer overrun — 결과가 부정확할 수 있습니다.")

    while prism.scan_status()[0] & ScanStatus.RUNNING:
        time.sleep(0.005)
    prism.scan_cleanup()

    if use_iepe:
        for ch in active_chs:
            prism.iepe_write(ch, False)

    n_got = len(data_flat) // n_ch
    if n_got < n_samples:
        print(f"WARNING: {n_got} 샘플 수신 (요청 {n_samples}) — FFT 크기를 {n_got}으로 조정")
        n_samples = n_got

    # ── raw 데이터 저장 ─────────────────────────────────────────────────────────
    raw_fname = datetime.datetime.now().strftime("raw_scan_%Y%m%d_%H%M%S.txt")
    with open(raw_fname, "w") as f:
        f.write("sample\t" + "\t".join(f"ch{c+1}" for c in active_chs) + "\n")
        for i in range(n_samples):
            vals = data_flat[i * n_ch:(i + 1) * n_ch]
            f.write(f"{i}\t" + "\t".join(f"{v:.6f}" for v in vals) + "\n")
    print(f"Raw 데이터 저장: {raw_fname}  ({n_samples} samples/ch × {n_ch} ch)")

    # ── FFT 분석 및 출력 ────────────────────────────────────────────────────────
    print()
    print("─" * 64)
    print(f"{'채널':<6}  {'기본파 (Hz)':>13}  {'진폭':>12}  고조파")
    print("─" * 64)

    spectra = []
    for col, ch in enumerate(active_chs):
        signal     = np.array(data_flat[col::n_ch][:n_samples])
        freqs, amp = analyze(signal, fs)
        peaks      = find_peaks(freqs, amp)
        spectra.append((ch, freqs, amp, peaks))

        if not peaks:
            print(f"ch{ch+1:<4}  (유효한 피크 없음)")
            continue

        fund_hz, fund_amp = peaks[0]
        harmonics_str = "  ".join(
            f"{h_hz:.1f}Hz({h_amp:.4f})"
            for h_hz, h_amp in peaks[1:]
        )
        print(f"ch{ch+1:<4}  {fund_hz:>13.2f}  {fund_amp:>12.6f}  {harmonics_str}")

    print("─" * 64)

    # ── CSV 저장 ─────────────────────────────────────────────────────────────────
    if save_csv and spectra:
        import os
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        fname     = f"fft_{timestamp}.csv"
        header    = "frequency_hz," + ",".join(f"ch{c+1}_amplitude" for c in active_chs)
        ref_freqs = spectra[0][1]
        cols      = [ref_freqs] + [amp for _, _, amp, _ in spectra]
        np.savetxt(fname, np.column_stack(cols),
                   delimiter=",", header=header, comments="", fmt="%.6g")
        print(f"\nCSV 저장: {os.path.abspath(fname)}")

finally:
    prism.close()
