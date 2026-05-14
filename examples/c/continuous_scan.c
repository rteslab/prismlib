/*
 * continuous_scan.c — PRISM-CLib example: continuous scan until Ctrl-C
 * Build: make examples
 * Run  : ./continuous_scan 192.168.7.1 7777
 */
#include "prismc.h"
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <string.h>

static volatile int g_stop = 0;
static void on_sigint(int sig) { (void)sig; g_stop = 1; }

int main(int argc, char *argv[])
{
    const char *ip   = (argc > 1) ? argv[1] : "192.168.7.1";
    uint16_t    port = (argc > 2) ? (uint16_t)atoi(argv[2]) : 7777;

    signal(SIGINT, on_sigint);

    printf("Connecting to %s:%u ...\n", ip, port);
    prismc_t *dev = prismc_open(ip, port);
    if (!dev) {
        fprintf(stderr, "prismc_open failed\n");
        return 1;
    }

    for (int ch = 0; ch < 4; ch++) {
        prismc_iepe_write(dev, (uint8_t)ch, 1);
        prismc_sens_write(dev, (uint8_t)ch, 100.0);
    }

    int rc = prismc_scan_start(dev, 0x0F, 100000, OPTS_CONTINUOUS);
    if (rc != RESULT_SUCCESS) {
        fprintf(stderr, "scan_start: %s\n", prismc_error_msg(rc));
        prismc_close(dev);
        return 1;
    }
    printf("Scanning continuously. Press Ctrl-C to stop.\n");

    int      num_ch = prismc_scan_ch_count(dev);
    double   buf[4000];  /* 1000 sample-sets × 4 ch */
    uint16_t status  = STATUS_RUNNING;
    uint64_t total   = 0;

    while ((status & STATUS_RUNNING) && !g_stop) {
        uint32_t n_read;
        rc = prismc_scan_read(dev, &status, 1000, 1.0, buf, 4000, &n_read);
        if (rc != RESULT_SUCCESS) break;

        if (status & STATUS_BUFFER_OVERRUN) {
            fprintf(stderr, "WARNING: buffer overrun!\n");
            break;
        }
        total += n_read;
        printf("\r  Received: %llu samples/ch    ", (unsigned long long)total);
        fflush(stdout);
    }
    printf("\n");

    /* 스캔이 아직 실행 중이면 (Ctrl-C 또는 overrun break) 중단 후 스레드 종료 대기.
     * scan_stop()은 비동기(서버 응답 없음)이므로 RUNNING 해제까지 polling 필요. */
    uint32_t avail;
    prismc_scan_status(dev, &status, &avail);
    if (status & STATUS_RUNNING) {
        prismc_scan_stop(dev);
        do {
            prismc_scan_status(dev, &status, &avail);
        } while (status & STATUS_RUNNING);
    }

    prismc_scan_cleanup(dev);
    prismc_close(dev);
    printf("Done. Total %llu samples/ch received.\n", (unsigned long long)total);
    return 0;
}
