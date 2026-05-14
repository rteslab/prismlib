/*
 * test_config.c — 설정 API 테스트 (cal / iepe / sens / clock)
 * Build: make test
 * Run  : ./test/c/test_config 192.168.7.1 7777
 */
#include "prismc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

static int g_pass = 0, g_fail = 0;

static void check(int num, const char *name, int cond, const char *detail)
{
    if (cond) { g_pass++; printf("[PASS] %02d %-40s", num, name); }
    else       { g_fail++; printf("[FAIL] %02d %-40s", num, name); }
    if (detail && detail[0]) printf(": %s", detail);
    printf("\n");
}

int main(int argc, char *argv[])
{
    const char *ip   = (argc > 1) ? argv[1] : "192.168.7.1";
    uint16_t    port = (argc > 2) ? (uint16_t)atoi(argv[2]) : 7777;
    char        detail[64];

    prismc_t *dev = prismc_open(ip, port);
    if (!dev) { fprintf(stderr, "prismc_open failed\n"); return 1; }

    /* ── Calibration ──────────────────────────────────────────────────────── */

    /* 01  cal_date */
    char date[16] = {0};
    int rc = prismc_cal_date(dev, date);
    int ok = (rc == RESULT_SUCCESS) && (strlen(date) == 10)
          && (date[4] == '-') && (date[7] == '-');
    snprintf(detail, sizeof(detail), "\"%s\"", date);
    check(1, "cal_date", ok, detail);

    /* 02~05  cal_read (4채널) */
    double orig_slope[4], orig_offset[4];
    for (int ch = 0; ch < 4; ch++) {
        rc = prismc_cal_read(dev, (uint8_t)ch, &orig_slope[ch], &orig_offset[ch]);
        ok = (rc == RESULT_SUCCESS) && (orig_slope[ch] > 0.0);
        snprintf(detail, sizeof(detail), "slope=%.6f  offset=%.6f",
                 orig_slope[ch], orig_offset[ch]);
        char name[24];
        snprintf(name, sizeof(name), "cal_read(%d)", ch);
        check(2 + ch, name, ok, detail);
    }

    /* 06  cal_write → RESULT_SUCCESS / 오류: 잘못된 채널 */
    rc = prismc_cal_write(dev, 0, 1.5, 0.1);
    check(6, "cal_write(0, 1.5, 0.1)", rc == RESULT_SUCCESS, "");
    int rc_err = prismc_cal_write(dev, 4, 1.0, 0.0);  /* 채널 4 = 범위 초과 */
    check(7, "cal_write(4, ...) → BAD_PARAM",
          rc_err == RESULT_BAD_PARAMETER, "");
    prismc_cal_write(dev, 0, orig_slope[0], orig_offset[0]);  /* 복원 */

    /* ── IEPE ─────────────────────────────────────────────────────────────── */

    /* 08  iepe_read */
    uint8_t iepe0 = 0xFF;
    rc = prismc_iepe_read(dev, 0, &iepe0);
    ok = (rc == RESULT_SUCCESS) && (iepe0 == 0 || iepe0 == 1);
    snprintf(detail, sizeof(detail), "ch0=%s", iepe0 ? "True" : "False");
    check(8, "iepe_read(0)", ok, detail);

    /* 09  iepe_write True → read back */
    prismc_iepe_write(dev, 0, 1);
    uint8_t iepe_rb = 0;
    prismc_iepe_read(dev, 0, &iepe_rb);
    check(9, "iepe_write(0,1) → iepe_read", iepe_rb == 1, "ch0=True");

    /* 10  iepe_write False → read back */
    prismc_iepe_write(dev, 0, 0);
    prismc_iepe_read(dev, 0, &iepe_rb);
    check(10, "iepe_write(0,0) → iepe_read", iepe_rb == 0, "ch0=False");

    /* ── Sensitivity ──────────────────────────────────────────────────────── */

    /* 11  sens_read 기본값 */
    double sv = 0.0;
    rc = prismc_sens_read(dev, 0, &sv);
    ok = (rc == RESULT_SUCCESS) && (fabs(sv - 1000.0) < 1e-6);
    snprintf(detail, sizeof(detail), "ch0=%.1f mV/unit", sv);
    check(11, "sens_read(0) default", ok, detail);

    /* 12  sens_write → read back */
    prismc_sens_write(dev, 0, 100.0);
    double sv2 = 0.0;
    prismc_sens_read(dev, 0, &sv2);
    ok = fabs(sv2 - 100.0) < 1e-6;
    snprintf(detail, sizeof(detail), "ch0=%.1f mV/unit", sv2);
    check(12, "sens_write(0, 100.0) → sens_read", ok, detail);
    prismc_sens_write(dev, 0, 1000.0);  /* 복원 */

    /* 13  sens_write 오류: 음수 값 */
    rc = prismc_sens_write(dev, 0, -1.0);
    check(13, "sens_write(0, -1.0) → BAD_PARAM",
          rc == RESULT_BAD_PARAMETER, "");

    /* ── Clock ────────────────────────────────────────────────────────────── */

    /* 14  clock_read */
    double rate = 0.0;
    rc = prismc_clock_read(dev, &rate);
    ok = (rc == RESULT_SUCCESS) && (rate > 0.0);
    snprintf(detail, sizeof(detail), "%.1f S/s", rate);
    check(14, "clock_read", ok, detail);

    /* 15  clock_write 51200 → read back */
    prismc_clock_write(dev, 51200.0);
    double r2 = 0.0;
    prismc_clock_read(dev, &r2);
    ok = fabs(r2 - 51200.0) / 51200.0 < 0.01;
    snprintf(detail, sizeof(detail), "%.1f S/s", r2);
    check(15, "clock_write(51200.0) → clock_read", ok, detail);

    /* 16  clock_write 64000 복원 */
    prismc_clock_write(dev, 64000.0);
    double r3 = 0.0;
    prismc_clock_read(dev, &r3);
    ok = fabs(r3 - 64000.0) / 64000.0 < 0.01;
    snprintf(detail, sizeof(detail), "%.1f S/s", r3);
    check(16, "clock_write(64000.0) → clock_read", ok, detail);

    prismc_close(dev);
    printf("\nResults: %d/%d passed\n", g_pass, g_pass + g_fail);
    return (g_fail == 0) ? 0 : 1;
}
