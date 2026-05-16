/*
 * continuous_scan.c — PRISM-CLib example: continuous scan until Ctrl-C
 * Build: make examples
 * Run  : ./continuous_scan [ip] [port] [sample_rate]
 *        sample_rate: 0=64K 1=128K 2=170K 3=256K 4=512K  (default: 0)
 */
#include "prismlib.h"
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <string.h>
#include <math.h>
#include <unistd.h>

static double calc_rms(const double *data, int channel, int num_channels,
                       uint32_t num_samples_per_channel)
{
    double value = 0.0;
    for (uint32_t i = 0; i < num_samples_per_channel; i++)
    {
        double v = data[i * num_channels + channel];
        value += (v * v) / num_samples_per_channel;
    }
    return sqrt(value);
}

static const char *SR_NAME[] = {"64K", "128K", "170K", "256K", "512K"};

static volatile int g_stop = 0;
static void on_sigint(int sig) { (void)sig; g_stop = 1; }

int main(int argc, char *argv[])
{
    const char *ip   = (argc > 1) ? argv[1] : "192.168.7.1";
    uint16_t    port = (argc > 2) ? (uint16_t)atoi(argv[2]) : 7777;
    int         sr   = (argc > 3) ? atoi(argv[3]) : 0;

    if (sr < 0 || sr > 4) {
        fprintf(stderr, "sample_rate: 0=64K 1=128K 2=170K 3=256K 4=512K\n");
        return 1;
    }

    signal(SIGINT, on_sigint);

    printf("Connecting to %s:%u ...\n", ip, port);
    prismlib_t *dev = prismlib_open(ip, port);
    if (!dev) {
        fprintf(stderr, "prismlib_open failed\n");
        return 1;
    }

    prismlib_sampleRate_write(dev, (PrismSampleRate_e)sr);

    for (int ch = 0; ch < 4; ch++)
        prismlib_sens_write(dev, (uint8_t)ch, 100.0);

    int use_iepe = 0;
    printf("IEPE를 활성화하시겠습니까? [y/n]: ");
    fflush(stdout);
    char ans[8] = {0};
    if (fgets(ans, sizeof(ans), stdin) && (ans[0] == 'y' || ans[0] == 'Y')) {
        use_iepe = 1;
        for (int ch = 0; ch < 4; ch++)
            prismlib_iepe_write(dev, (uint8_t)ch, 1);
        printf("IEPE 안정화 대기 중 (2초)...");
        fflush(stdout);
        sleep(2);
        printf(" 완료\n");
    }

    int rc = prismlib_scan_start(dev, 0x0F, 100000, OPTS_CONTINUOUS);
    if (rc != RESULT_SUCCESS) {
        fprintf(stderr, "scan_start: %s\n", prismlib_error_msg(rc));
        if (use_iepe)
            for (int ch = 0; ch < 4; ch++)
                prismlib_iepe_write(dev, (uint8_t)ch, 0);
        prismlib_close(dev);
        return 1;
    }
    int      num_ch  = prismlib_scan_ch_count(dev);
    int      buf_len = num_ch * 1000;
    double  *buf     = malloc((size_t)buf_len * sizeof(double));

    printf("Scanning [%s] continuously. Press Ctrl-C to stop.\n", SR_NAME[sr]);

    /* 헤더 출력 */
    printf("%14s%14s", "Samples Read", "Total");
    for (int ch = 0; ch < num_ch; ch++)
        printf("        ch%d RMS", ch + 1);
    printf("\n");

    uint16_t status  = STATUS_RUNNING;
    uint64_t total   = 0;

    while ((status & STATUS_RUNNING) && !g_stop) {
        uint32_t n_read;
        rc = prismlib_scan_read(dev, &status, 1000, 1.0, buf, (uint32_t)buf_len, &n_read);
        if (rc != RESULT_SUCCESS) break;

        if (status & STATUS_BUFFER_OVERRUN) {
            fprintf(stderr, "WARNING: buffer overrun!\n");
            break;
        }
        total += n_read;

        if (n_read > 0) {
            printf("\r%14u%14llu", n_read, (unsigned long long)total);
            for (int ch = 0; ch < num_ch; ch++)
                printf("%14.5f", calc_rms(buf, ch, num_ch, n_read));
            fflush(stdout);
        }
    }
    printf("\n");

    uint32_t avail;
    prismlib_scan_status(dev, &status, &avail);
    if (status & STATUS_RUNNING) {
        prismlib_scan_stop(dev);
        do {
            prismlib_scan_status(dev, &status, &avail);
        } while (status & STATUS_RUNNING);
    }

    if (use_iepe)
        for (int ch = 0; ch < 4; ch++)
            prismlib_iepe_write(dev, (uint8_t)ch, 0);

    prismlib_scan_cleanup(dev);
    free(buf);
    prismlib_close(dev);
    printf("Done. Total %llu samples/ch received.\n", (unsigned long long)total);
    return 0;
}
