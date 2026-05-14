/*
 * test_finite_scan.c — 유한 스캔 API 테스트
 * Build: make test
 * Run  : ./test/c/test_finite_scan 192.168.7.1 7777
 */
#include "prismc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_pass = 0, g_fail = 0;

static void check(int num, const char *name, int cond, const char *detail)
{
    if (cond) { g_pass++; printf("[PASS] %02d %-40s", num, name); }
    else       { g_fail++; printf("[FAIL] %02d %-40s", num, name); }
    if (detail && detail[0]) printf(": %s", detail);
    printf("\n");
}

/* RUNNING 플래그가 해제될 때까지 polling (최대 timeout_ms ms) */
static int wait_until_stopped(prismc_t *dev, int timeout_ms)
{
    uint16_t status;
    uint32_t avail;
    for (int i = 0; i < timeout_ms; i++) {
        prismc_scan_status(dev, &status, &avail);
        if (!(status & STATUS_RUNNING))
            return 1;
        /* 1 ms 대기 — 이식성 있는 busy-wait (CM4 Linux에서는 usleep 사용 가능) */
        volatile int j = 0;
        while (j++ < 50000);
    }
    return 0;
}

int main(int argc, char *argv[])
{
    const char *ip   = (argc > 1) ? argv[1] : "192.168.7.1";
    uint16_t    port = (argc > 2) ? (uint16_t)atoi(argv[2]) : 7777;
    char        detail[64];

    prismc_t *dev = prismc_open(ip, port);
    if (!dev) { fprintf(stderr, "prismc_open failed\n"); return 1; }

    uint8_t  ch_mask = 0x0F;
    uint32_t samples = 1024;

    /* 01  scan_start */
    int rc = prismc_scan_start(dev, ch_mask, samples, OPTS_DEFAULT);
    check(1, "scan_start", rc == RESULT_SUCCESS, "");

    /* 02  scan_status → RUNNING */
    uint16_t status = 0;
    uint32_t avail  = 0;
    prismc_scan_status(dev, &status, &avail);
    check(2, "scan_status RUNNING", (status & STATUS_RUNNING) != 0, "");

    /* 03  scan_ch_count */
    int n_ch = prismc_scan_ch_count(dev);
    snprintf(detail, sizeof(detail), "ch_count=%d", n_ch);
    check(3, "scan_ch_count", n_ch == 4, detail);

    /* 04  scan_buf_size */
    uint32_t buf_sz = 0;
    prismc_scan_buf_size(dev, &buf_sz);
    snprintf(detail, sizeof(detail), "buf_size=%u", buf_sz);
    check(4, "scan_buf_size", buf_sz >= samples, detail);

    /* 05  scan_read */
    uint32_t total_buf = samples * (uint32_t)n_ch;
    double  *buf       = (double *)malloc(total_buf * sizeof(double));
    uint32_t n_read    = 0;
    rc = prismc_scan_read(dev, &status, (int32_t)samples, 10.0,
                          buf, total_buf, &n_read);
    snprintf(detail, sizeof(detail), "%u samples/ch received", n_read);
    check(5, "scan_read", rc == RESULT_SUCCESS && n_read == samples, detail);
    free(buf);

    /* 06  RUNNING 해제 → scan_cleanup */
    int stopped = wait_until_stopped(dev, 5000);
    check(6, "RUNNING 해제 대기", stopped, "");
    rc = prismc_scan_cleanup(dev);
    check(7, "scan_cleanup", rc == RESULT_SUCCESS, "");

    /* 07  scan_start 재시작 */
    rc = prismc_scan_start(dev, ch_mask, samples, OPTS_DEFAULT);
    check(8, "scan_start (재시작)", rc == RESULT_SUCCESS, "");

    /* 08  scan_read (2차) */
    buf    = (double *)malloc(total_buf * sizeof(double));
    n_read = 0;
    rc = prismc_scan_read(dev, &status, (int32_t)samples, 10.0,
                          buf, total_buf, &n_read);
    snprintf(detail, sizeof(detail), "%u samples/ch received", n_read);
    check(9, "scan_read (2차)", rc == RESULT_SUCCESS && n_read == samples, detail);
    free(buf);

    stopped = wait_until_stopped(dev, 5000);
    check(10, "RUNNING 해제 대기 (2차)", stopped, "");
    prismc_scan_cleanup(dev);

    /* 09  오류: RUNNING 중 scan_cleanup → RESULT_BUSY */
    prismc_scan_start(dev, ch_mask, 100000, OPTS_CONTINUOUS);
    rc = prismc_scan_cleanup(dev);
    check(11, "RUNNING 중 scan_cleanup → RESULT_BUSY",
          rc == RESULT_BUSY, "");
    /* 정상 종료 */
    prismc_scan_stop(dev);
    wait_until_stopped(dev, 5000);
    prismc_scan_cleanup(dev);

    prismc_close(dev);
    printf("\nResults: %d/%d passed\n", g_pass, g_pass + g_fail);
    return (g_fail == 0) ? 0 : 1;
}
