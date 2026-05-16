/*
 * fft_scan.c — FFT 주파수 분석
 * Build: make examples
 * Run  : ./examples/c/fft_scan [ip] [port] [sample_rate] [channel_mask] [samples] [--csv]
 *        sample_rate:  0=64K 1=128K 2=170K 3=256K 4=512K  (기본 0)
 *        channel_mask: 0x01~0x0F                           (기본 0x0F)
 *        samples:      수집 샘플 수, 2의 거듭제곱 권장       (기본: sample_rate ≈ 1초 분량)
 */
#include "prismlib.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <unistd.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* ── 상수 ────────────────────────────────────────────────────────────────── */
static const int   SR_HZ[]        = {64000, 128000, 170000, 256000, 512000};
static const char *SR_NAME[]      = {"64K",  "128K",  "170K",  "256K",  "512K"};
/* 각 샘플레이트의 ≈1초 분량, 2의 거듭제곱 */
static const int   SR_DEFAULT_N[] = {65536, 131072, 131072, 262144, 524288};

#define N_HARMONICS  5
#define MIN_FREQ_HZ  5.0

/* ── Cooley-Tukey radix-2 DIT in-place FFT ──────────────────────────────── */
static void fft_inplace(double *re, double *im, int n)
{
    int i, j = 0;
    for (i = 1; i < n; i++) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            double tr = re[i]; re[i] = re[j]; re[j] = tr;
            double ti = im[i]; im[i] = im[j]; im[j] = ti;
        }
    }
    int len;
    for (len = 2; len <= n; len <<= 1) {
        double ang = -2.0 * M_PI / len;
        double wre = cos(ang), wim = sin(ang);
        for (i = 0; i < n; i += len) {
            double cr = 1.0, ci = 0.0;
            for (j = 0; j < len / 2; j++) {
                double ure = re[i+j],        uim = im[i+j];
                double k   = re[i+j+len/2];
                double l   = im[i+j+len/2];
                double vre = k * cr - l * ci, vim = k * ci + l * cr;
                re[i+j]       = ure + vre;  im[i+j]       = uim + vim;
                re[i+j+len/2] = ure - vre;  im[i+j+len/2] = uim - vim;
                double nr = cr * wre - ci * wim;
                ci = cr * wim + ci * wre;
                cr = nr;
            }
        }
    }
}

/* ── 단측 진폭 스펙트럼 ─────────────────────────────────────────────────── */
static double *compute_spectrum(const double *signal, int n, double fs,
                                double **freqs_out)
{
    int bins = n / 2 + 1;
    double *re   = (double *)malloc(n * sizeof(double));
    double *im   = (double *)calloc(n, sizeof(double));
    double *amp  = (double *)malloc(bins * sizeof(double));
    double *freq = (double *)malloc(bins * sizeof(double));
    if (!re || !im || !amp || !freq) {
        free(re); free(im); free(amp); free(freq);
        return NULL;
    }

    double win_sum = 0.0;
    int i;
    for (i = 0; i < n; i++) {
        double w = 0.5 * (1.0 - cos(2.0 * M_PI * i / (n - 1)));
        re[i]    = signal[i] * w;
        win_sum += w;
    }
    double gain = win_sum / n;

    fft_inplace(re, im, n);

    for (i = 0; i < bins; i++) {
        amp[i]  = sqrt(re[i]*re[i] + im[i]*im[i]) / (n * gain);
        if (i > 0 && i < bins - 1) amp[i] *= 2.0;
        freq[i] = (double)i * fs / n;
    }

    free(re);
    free(im);
    *freqs_out = freq;
    return amp;
}

/* ── 기본파 + 고조파 피크 탐색 ──────────────────────────────────────────── */
static int find_peaks(const double *freq, const double *amp, int bins,
                      double *peak_freq, double *peak_amp, int n)
{
    double freq_res = bins > 1 ? freq[1] : 1.0;
    int    min_bin  = (int)ceil(MIN_FREQ_HZ / freq_res);
    if (min_bin < 1) min_bin = 1;

    int pk = min_bin, i;
    for (i = min_bin + 1; i < bins; i++)
        if (amp[i] > amp[pk]) pk = i;
    double fund_hz = freq[pk];

    int count = 0, h;
    for (h = 1; h <= n; h++) {
        int tbin = (int)round(fund_hz * h / freq_res);
        if (tbin >= bins) break;
        int lo   = (tbin - 2 > min_bin) ? tbin - 2 : min_bin;
        int hi   = (tbin + 2 < bins)    ? tbin + 2 : bins - 1;
        int best = lo;
        for (i = lo + 1; i <= hi; i++)
            if (amp[i] > amp[best]) best = i;
        peak_freq[count] = freq[best];
        peak_amp[count]  = amp[best];
        count++;
    }
    return count;
}

/* ── scan 정지 대기 ─────────────────────────────────────────────────────── */
static void wait_scan_stopped(prismlib_t *dev)
{
    uint16_t st; uint32_t av;
    int i;
    for (i = 0; i < 5000; i++) {
        prismlib_scan_status(dev, &st, &av);
        if (!(st & STATUS_RUNNING)) return;
        volatile int j = 0; while (j++ < 50000);
    }
}

/* ── main ────────────────────────────────────────────────────────────────── */
int main(int argc, char *argv[])
{
    const char *ip      = (argc > 1) ? argv[1] : "192.168.7.1";
    int         port    = (argc > 2) ? atoi(argv[2]) : 7777;
    int         sr      = (argc > 3) ? atoi(argv[3]) : 0;
    int         ch_mask = (argc > 4) ? (int)strtol(argv[4], NULL, 0) : 0x0F;
    int         save_csv = 0, i;
    for (i = 1; i < argc; i++)
        if (strcmp(argv[i], "--csv") == 0) save_csv = 1;

    if (sr < 0 || sr > 4) {
        fprintf(stderr, "sample_rate: 0=64K 1=128K 2=170K 3=256K 4=512K\n");
        return 1;
    }
    if (ch_mask < 0x01 || ch_mask > 0x0F) {
        fprintf(stderr, "channel_mask: 0x01~0x0F\n");
        return 1;
    }

    int n_samples = (argc > 5) ? atoi(argv[5]) : SR_DEFAULT_N[sr];

    int active[4], n_ch = 0;
    for (i = 0; i < 4; i++)
        if (ch_mask & (1 << i)) active[n_ch++] = i;

    double fs = (double)SR_HZ[sr];

    printf("Connecting to %s:%d ...\n", ip, port);
    prismlib_t *dev = prismlib_open(ip, (uint16_t)port);
    if (!dev) { fprintf(stderr, "prismlib_open failed\n"); return 1; }

    prismlib_sampleRate_write(dev, (PrismSampleRate_e)sr);

    {
        char ch_str[32] = {0};
        for (i = 0; i < n_ch; i++) {
            char tmp[16];
            snprintf(tmp, sizeof(tmp), "%sch%d", i ? "+" : "", active[i] + 1);
            strncat(ch_str, tmp, sizeof(ch_str) - strlen(ch_str) - 1);
        }
        printf("Sample rate  : %s (%d Hz)\n", SR_NAME[sr], SR_HZ[sr]);
        printf("Channels     : %s\n", ch_str);
        printf("Samples/ch   : %d  (freq resolution: %.3f Hz)\n",
               n_samples, fs / n_samples);
    }

    int use_iepe = 0;
    printf("IEPE를 활성화하시겠습니까? [y/n]: ");
    fflush(stdout);
    char ans[8] = {0};
    if (fgets(ans, sizeof(ans), stdin) && (ans[0] == 'y' || ans[0] == 'Y')) {
        use_iepe = 1;
        for (i = 0; i < n_ch; i++)
            prismlib_iepe_write(dev, (uint8_t)active[i], 1);
        printf("IEPE 안정화 대기 중 (2초)...");
        fflush(stdout);
        sleep(2);
        printf(" 완료\n");
    }

    printf("Collecting ...\n");
    fflush(stdout);

    /* ── 스캔 ────────────────────────────────────────────────────────────── */
    uint32_t buf_size = (uint32_t)(n_samples * n_ch);
    double  *buf      = (double *)malloc(buf_size * sizeof(double));
    if (!buf) {
        if (use_iepe)
            for (i = 0; i < n_ch; i++)
                prismlib_iepe_write(dev, (uint8_t)active[i], 0);
        prismlib_close(dev);
        return 1;
    }

    uint16_t status = 0;
    uint32_t got    = 0;
    prismlib_scan_start(dev, (uint8_t)ch_mask, (uint32_t)n_samples, OPTS_DEFAULT);
    prismlib_scan_read(dev, &status, n_samples, 60.0, buf, buf_size, &got);

    if (status & STATUS_BUFFER_OVERRUN)
        printf("WARNING: buffer overrun — 결과가 부정확할 수 있습니다.\n");

    wait_scan_stopped(dev);
    prismlib_scan_cleanup(dev);

    if (use_iepe)
        for (i = 0; i < n_ch; i++)
            prismlib_iepe_write(dev, (uint8_t)active[i], 0);

    int n_got = (int)got;
    if (n_got < n_samples) {
        printf("WARNING: %d 샘플 수신 (요청 %d) — FFT 크기를 %d으로 조정\n",
               n_got, n_samples, n_got);
        n_samples = n_got;
    }

    /* ── raw 데이터 저장 ─────────────────────────────────────────────────── */
    time_t t = time(NULL);
    struct tm *ti = localtime(&t);

    char raw_fname[64];
    strftime(raw_fname, sizeof(raw_fname), "raw_scan_%Y%m%d_%H%M%S.txt", ti);
    FILE *rfp = fopen(raw_fname, "w");
    if (rfp) {
        fprintf(rfp, "sample");
        for (int col = 0; col < n_ch; col++)
            fprintf(rfp, "\tch%d", active[col] + 1);
        fprintf(rfp, "\n");
        for (i = 0; i < n_samples; i++) {
            fprintf(rfp, "%d", i);
            for (int col = 0; col < n_ch; col++)
                fprintf(rfp, "\t%.6f", buf[(size_t)i * n_ch + col]);
            fprintf(rfp, "\n");
        }
        fclose(rfp);
        printf("Raw 데이터 저장: %s  (%d samples/ch × %d ch)\n",
               raw_fname, n_samples, n_ch);
    }

    /* ── FFT 분석 및 출력 ────────────────────────────────────────────────── */
    int bins = n_samples / 2 + 1;

    printf("\n");
    printf("%-8s  %13s  %12s  harmonics\n", "채널", "기본파 (Hz)", "진폭");
    printf("────────────────────────────────────────────────────────────────\n");

    double **ch_freq = NULL, **ch_amp = NULL;
    if (save_csv) {
        ch_freq = (double **)calloc(n_ch, sizeof(double *));
        ch_amp  = (double **)calloc(n_ch, sizeof(double *));
    }

    double pk_f[N_HARMONICS], pk_a[N_HARMONICS];
    int col;
    for (col = 0; col < n_ch; col++) {
        double *sig = (double *)malloc(n_samples * sizeof(double));
        if (!sig) break;
        for (i = 0; i < n_samples; i++)
            sig[i] = buf[(size_t)i * n_ch + col];

        double *freq = NULL;
        double *amp  = compute_spectrum(sig, n_samples, fs, &freq);
        free(sig);
        if (!amp) continue;

        int n_peaks = find_peaks(freq, amp, bins, pk_f, pk_a, N_HARMONICS);
        int h;

        printf("ch%-6d  %13.2f  %12.6f  ",
               active[col] + 1,
               n_peaks > 0 ? pk_f[0] : 0.0,
               n_peaks > 0 ? pk_a[0] : 0.0);
        for (h = 1; h < n_peaks; h++)
            printf("%.1fHz(%.4f)  ", pk_f[h], pk_a[h]);
        printf("\n");

        if (save_csv && ch_freq && ch_amp) {
            ch_freq[col] = freq;
            ch_amp[col]  = amp;
        } else {
            free(freq);
            free(amp);
        }
    }

    printf("────────────────────────────────────────────────────────────────\n");

    /* ── CSV 저장 ─────────────────────────────────────────────────────────── */
    if (save_csv && ch_freq && ch_amp) {
        char fname[64];
        strftime(fname, sizeof(fname), "fft_%Y%m%d_%H%M%S.csv", ti);

        FILE *fp = fopen(fname, "w");
        if (fp) {
            fprintf(fp, "frequency_hz");
            for (col = 0; col < n_ch; col++)
                fprintf(fp, ",ch%d_amplitude", active[col] + 1);
            fprintf(fp, "\n");
            for (i = 0; i < bins; i++) {
                fprintf(fp, "%.6g", ch_freq[0][i]);
                for (col = 0; col < n_ch; col++)
                    fprintf(fp, ",%.6g", ch_amp[col][i]);
                fprintf(fp, "\n");
            }
            fclose(fp);
            printf("\nCSV saved: %s\n", fname);
        }
        for (col = 0; col < n_ch; col++) {
            free(ch_freq[col]);
            free(ch_amp[col]);
        }
        free(ch_freq);
        free(ch_amp);
    }

    free(buf);
    prismlib_close(dev);
    return 0;
}
