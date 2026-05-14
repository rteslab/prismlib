/*
 * test_continuous_scan.c — 연속 스캔 API 테스트
 * Build: make test
 * Run  : ./test/c/test_continuous_scan 192.168.7.1 7777
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

    int n_ch = 4;

    /* 01  scan_start CONTINUOUS */
    int rc = prismc_scan_start(dev, 0x0F, 100000, OPTS_CONTINUOUS);
    check(1, "scan_start CONTINUOUS", rc == RESULT_SUCCESS, "");
    if (rc != RESULT_SUCCESS) {
        prismc_close(dev);
        return 1;
    }

    /* 02  scan_status → RUNNING */
    uint16_t status = 0;
    uint32_t avail  = 0;
    prismc_scan_status(dev, &status, &avail);
    check(2, "scan_status RUNNING", (status & STATUS_RUNNING) != 0, "");

    n_ch = prismc_scan_ch_count(dev);

    /* 03  scan_read × 3회 반복 수신 */
    uint32_t read_buf_sz = 1000 * (uint32_t)n_ch;
    double  *buf         = (double *)malloc(read_buf_sz * sizeof(double));
    int      all_ok      = 1;
    uint32_t total       = 0;
    for (int i = 0; i < 3; i++) {
        uint32_t n_read = 0;
        rc = prismc_scan_read(dev, &status, 1000, 2.0,
                              buf, read_buf_sz, &n_read);
        if (rc != RESULT_SUCCESS || n_read == 0) { all_ok = 0; break; }
        total += n_read;
    }
    snprintf(detail, sizeof(detail), "1000 samples/ch × 3  (total=%u)", total);
    check(3, "scan_read × 3 수신", all_ok, detail);
    free(buf);

    /* 04  오류: RUNNING 중 scan_cleanup → RESULT_BUSY */
    rc = prismc_scan_cleanup(dev);
    check(4, "RUNNING 중 scan_cleanup → RESULT_BUSY",
          rc == RESULT_BUSY, "");

    /* 05  scan_stop */
    rc = prismc_scan_stop(dev);
    check(5, "scan_stop", rc == RESULT_SUCCESS, "");

    /* 06  RUNNING 해제 대기 (2초 이내) */
    int stopped = wait_until_stopped(dev, 2000);
    check(6, "RUNNING 해제 (2초 이내)", stopped, "");

    /* 07  scan_cleanup */
    rc = prismc_scan_cleanup(dev);
    check(7, "scan_cleanup", rc == RESULT_SUCCESS, "");

    prismc_close(dev);
    printf("\nResults: %d/%d passed\n", g_pass, g_pass + g_fail);
    return (g_fail == 0) ? 0 : 1;
}
