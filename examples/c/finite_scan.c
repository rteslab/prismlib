/*
 * finite_scan.c — PRISM-CLib example: finite scan (ch0~ch3, 1024 samples)
 * Build: make examples
 * Run  : ./finite_scan 192.168.7.1 7777
 */
#include "prismc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char *argv[])
{
    const char *ip   = (argc > 1) ? argv[1] : "192.168.7.1";
    uint16_t    port = (argc > 2) ? (uint16_t)atoi(argv[2]) : 7777;

    uint8_t  channel_mask = 0x0F;
    uint32_t samples      = 1024;
    int      num_ch       = 4;

    printf("Connecting to %s:%u ...\n", ip, port);
    prismc_t *dev = prismc_open(ip, port);
    if (!dev) {
        fprintf(stderr, "prismc_open failed\n");
        return 1;
    }

    char ver[32], sn[32];
    prismc_fw_version(dev, ver);
    prismc_serial(dev, sn);
    printf("FW version : %s\n", ver);
    printf("Serial     : %s\n", sn);

    /* IEPE on, sensitivity 100 mV/g */
    for (int ch = 0; ch < num_ch; ch++) {
        prismc_iepe_write(dev, (uint8_t)ch, 1);
        prismc_sens_write(dev, (uint8_t)ch, 100.0);
    }

    int rc = prismc_scan_start(dev, channel_mask, samples, OPTS_DEFAULT);
    if (rc != RESULT_SUCCESS) {
        fprintf(stderr, "scan_start failed: %s\n", prismc_error_msg(rc));
        prismc_close(dev);
        return 1;
    }

    uint32_t buf_size = samples * (uint32_t)num_ch;
    double  *buf      = (double *)malloc(buf_size * sizeof(double));
    uint16_t status;
    uint32_t n_read;

    rc = prismc_scan_read(dev, &status, (int32_t)samples, 10.0,
                          buf, buf_size, &n_read);
    if (rc != RESULT_SUCCESS) {
        fprintf(stderr, "scan_read failed: %s\n", prismc_error_msg(rc));
    } else {
        printf("Read %u samples/ch\n", n_read);
        for (uint32_t i = 0; i < n_read && i < 5; i++) {
            printf("[%4u] ch0=%8.4f g  ch1=%8.4f g  ch2=%8.4f g  ch3=%8.4f g\n",
                   i,
                   buf[i * 4 + 0], buf[i * 4 + 1],
                   buf[i * 4 + 2], buf[i * 4 + 3]);
        }
        if (n_read > 5) printf("  ... (%u more)\n", n_read - 5);
    }

    free(buf);

    /* 유한 스캔 완료 후 수신 스레드가 RUNNING 플래그를 해제할 때까지 대기.
     * scan_read() 반환 시점과 스레드 ~RUNNING 설정 사이에 race condition이 있으므로
     * scan_cleanup() 호출 전에 반드시 확인한다. */
    uint32_t avail;
    do {
        prismc_scan_status(dev, &status, &avail);
    } while (status & STATUS_RUNNING);

    prismc_scan_cleanup(dev);
    prismc_close(dev);
    return 0;
}
