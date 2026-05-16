/*
 * finite_scan.c — PRISM-CLib example: finite scan (ch1~ch4, N samples)
 * Build: make examples
 * Run  : ./finite_scan [ip] [port] [samples]
 *        ./finite_scan 192.168.7.1 7777 64000
 */
#include "prismlib.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

int main(int argc, char *argv[])
{
    const char *ip      = (argc > 1) ? argv[1] : "192.168.7.1";
    uint16_t    port    = (argc > 2) ? (uint16_t)atoi(argv[2]) : 7777;
    uint32_t    samples = (argc > 3) ? (uint32_t)atoi(argv[3]) : 64000;
    int         num_ch  = 4;
    uint8_t     ch_mask = 0x0F;

    printf("Connecting to %s:%u ...\n", ip, port);
    prismlib_t *dev = prismlib_open(ip, port);
    if (!dev) {
        fprintf(stderr, "prismlib_open failed\n");
        return 1;
    }

    char ver[32], sn[32];
    prismlib_fw_version(dev, ver);
    prismlib_serial(dev, sn);
    printf("FW version : %s\n", ver);
    printf("Serial     : %s\n", sn);

    for (int ch = 0; ch < num_ch; ch++)
        prismlib_sens_write(dev, (uint8_t)ch, 100.0);

    int use_iepe = 0;
    printf("IEPE를 활성화하시겠습니까? [y/n]: ");
    fflush(stdout);
    char ans[8] = {0};
    if (fgets(ans, sizeof(ans), stdin) && (ans[0] == 'y' || ans[0] == 'Y')) {
        use_iepe = 1;
        for (int ch = 0; ch < num_ch; ch++)
            prismlib_iepe_write(dev, (uint8_t)ch, 1);
        printf("IEPE 안정화 대기 중 (2초)...");
        fflush(stdout);
        sleep(2);
        printf(" 완료\n");
    }

    uint32_t buf_size = samples * (uint32_t)num_ch;
    double  *buf      = (double *)malloc(buf_size * sizeof(double));
    if (!buf) {
        fprintf(stderr, "malloc failed\n");
        if (use_iepe)
            for (int ch = 0; ch < num_ch; ch++)
                prismlib_iepe_write(dev, (uint8_t)ch, 0);
        prismlib_close(dev);
        return 1;
    }

    int rc = prismlib_scan_start(dev, ch_mask, samples, OPTS_DEFAULT);
    if (rc != RESULT_SUCCESS) {
        fprintf(stderr, "scan_start failed: %s\n", prismlib_error_msg(rc));
        free(buf);
        if (use_iepe)
            for (int ch = 0; ch < num_ch; ch++)
                prismlib_iepe_write(dev, (uint8_t)ch, 0);
        prismlib_close(dev);
        return 1;
    }

    uint16_t status;
    uint32_t n_read;
    rc = prismlib_scan_read(dev, &status, (int32_t)samples, 10.0,
                            buf, buf_size, &n_read);
    if (rc != RESULT_SUCCESS)
        fprintf(stderr, "scan_read failed: %s\n", prismlib_error_msg(rc));

    uint32_t avail;
    do {
        prismlib_scan_status(dev, &status, &avail);
    } while (status & STATUS_RUNNING);

    prismlib_scan_cleanup(dev);

    if (use_iepe)
        for (int ch = 0; ch < num_ch; ch++)
            prismlib_iepe_write(dev, (uint8_t)ch, 0);

    printf("Read %u samples/ch\n", n_read);
    for (uint32_t i = 0; i < n_read && i < 5; i++) {
        printf("[%4u]", i);
        for (int ch = 0; ch < num_ch; ch++)
            printf("  ch%d=%8.4f g", ch + 1, buf[i * (uint32_t)num_ch + ch]);
        printf("\n");
    }
    if (n_read > 5) printf("  ... (%u more)\n", n_read - 5);

    char fname[64];
    time_t t = time(NULL);
    struct tm *ti = localtime(&t);
    strftime(fname, sizeof(fname), "finite_scan_%Y%m%d_%H%M%S.txt", ti);
    FILE *fp = fopen(fname, "w");
    if (fp) {
        fprintf(fp, "sample");
        for (int ch = 0; ch < num_ch; ch++)
            fprintf(fp, "\tch%d(g)", ch + 1);
        fprintf(fp, "\n");
        for (uint32_t i = 0; i < n_read; i++) {
            fprintf(fp, "%u", i);
            for (int ch = 0; ch < num_ch; ch++)
                fprintf(fp, "\t%.6f", buf[i * (uint32_t)num_ch + ch]);
            fprintf(fp, "\n");
        }
        fclose(fp);
        printf("저장 완료: %s\n", fname);
    }

    free(buf);
    prismlib_close(dev);
    return 0;
}
