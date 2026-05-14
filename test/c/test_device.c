/*
 * test_device.c — 장치 연결 / 기본 정보 테스트
 * Build: make test
 * Run  : ./test/c/test_device 192.168.7.1 7777
 */
#include "prismc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_pass = 0, g_fail = 0;

static void check(int num, const char *name, int cond, const char *detail)
{
    if (cond) { g_pass++; printf("[PASS] %02d %-35s", num, name); }
    else       { g_fail++; printf("[FAIL] %02d %-35s", num, name); }
    if (detail && detail[0]) printf(": %s", detail);
    printf("\n");
}

int main(int argc, char *argv[])
{
    const char *ip   = (argc > 1) ? argv[1] : "192.168.7.1";
    uint16_t    port = (argc > 2) ? (uint16_t)atoi(argv[2]) : 7777;

    /* 01  open */
    prismc_t *dev = prismc_open(ip, port);
    check(1, "open", dev != NULL, "");

    if (!dev) {
        printf("\nResults: 0/1 passed\n");
        return 1;
    }

    /* 02  fw_version */
    char ver[32] = {0};
    int rc = prismc_fw_version(dev, ver);
    check(2, "fw_version", rc == RESULT_SUCCESS && strlen(ver) > 0, ver);

    /* 03  serial */
    char sn[32] = {0};
    rc = prismc_serial(dev, sn);
    check(3, "serial", rc == RESULT_SUCCESS && strlen(sn) > 0, sn);

    /* 04  info */
    struct PrismcDeviceInfo *inf = prismc_info(dev);
    int ok = (inf != NULL)
          && (inf->AI_MIN_CODE < inf->AI_MAX_CODE)
          && (inf->NUM_AI_CHANNELS > 0);
    char detail[64] = {0};
    if (inf)
        snprintf(detail, sizeof(detail), "num_ch=%u  min_code=%d  max_code=%d",
                 inf->NUM_AI_CHANNELS, inf->AI_MIN_CODE, inf->AI_MAX_CODE);
    check(4, "info", ok, detail);

    /* 05  close */
    rc = prismc_close(dev);
    check(5, "close", rc == RESULT_SUCCESS, "");

    printf("\nResults: %d/%d passed\n", g_pass, g_pass + g_fail);
    return (g_fail == 0) ? 0 : 1;
}
