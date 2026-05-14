/*
 * prismc.h — PRISM-CLib public API
 * Handle-based multi-device support: each prismc_open() returns an independent prismc_t*.
 */
#ifndef PRISMC_H
#define PRISMC_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── Result codes ──────────────────────────────────────────────────────── */
#define RESULT_SUCCESS           0
#define RESULT_BAD_PARAMETER    -1
#define RESULT_BUSY             -2
#define RESULT_TIMEOUT          -3
#define RESULT_LOCK_TIMEOUT     -4
#define RESULT_RESOURCE_UNAVAIL -6
#define RESULT_COMMS_FAILURE    -7
#define RESULT_UNDEFINED        -10

/* ── Scan options (OR-combine) ─────────────────────────────────────────── */
#define OPTS_DEFAULT          0x0000u
#define OPTS_NOSCALEDATA      0x0001u
#define OPTS_NOCALIBRATEDATA  0x0002u
#define OPTS_CONTINUOUS       0x0010u

/* ── Scan status flags ─────────────────────────────────────────────────── */
#define STATUS_HW_OVERRUN     0x0001u
#define STATUS_BUFFER_OVERRUN 0x0002u
#define STATUS_RUNNING        0x0008u

/* ── Hardware info structure ───────────────────────────────────────────── */
struct PrismcDeviceInfo {
    uint8_t NUM_AI_CHANNELS;
    int32_t AI_MIN_CODE;
    int32_t AI_MAX_CODE;
    double  AI_MIN_VOLTAGE;
    double  AI_MAX_VOLTAGE;
    double  AI_MIN_RANGE;
    double  AI_MAX_RANGE;
};

/* ── Opaque device handle ──────────────────────────────────────────────── */
typedef struct prismc_s prismc_t;

/* ── Device management ─────────────────────────────────────────────────── */
prismc_t           *prismc_open(const char *ip, uint16_t port);
int                 prismc_is_open(prismc_t *dev);
int                 prismc_close(prismc_t *dev);
struct PrismcDeviceInfo *prismc_info(prismc_t *dev);
int                 prismc_fw_version(prismc_t *dev, char *buffer);
int                 prismc_serial(prismc_t *dev, char *buffer);
const char         *prismc_error_msg(int result);

/* ── Calibration ───────────────────────────────────────────────────────── */
int prismc_cal_date(prismc_t *dev, char *buffer);
int prismc_cal_read(prismc_t *dev, uint8_t channel, double *slope, double *offset);
int prismc_cal_write(prismc_t *dev, uint8_t channel, double slope, double offset);

/* ── IEPE ──────────────────────────────────────────────────────────────── */
int prismc_iepe_read(prismc_t *dev, uint8_t channel, uint8_t *config);
int prismc_iepe_write(prismc_t *dev, uint8_t channel, uint8_t config);

/* ── Sensor sensitivity ────────────────────────────────────────────────── */
int prismc_sens_read(prismc_t *dev, uint8_t channel, double *value);
int prismc_sens_write(prismc_t *dev, uint8_t channel, double value);

/* ── Clock ─────────────────────────────────────────────────────────────── */
int prismc_clock_read(prismc_t *dev, double *sample_rate_per_channel);
int prismc_clock_write(prismc_t *dev, double sample_rate_per_channel);

/* ── Analog input scan ─────────────────────────────────────────────────── */
int prismc_scan_start(prismc_t *dev, uint8_t channel_mask,
                      uint32_t samples_per_channel, uint32_t options);
int prismc_scan_stop(prismc_t *dev);
int prismc_scan_read(prismc_t *dev, uint16_t *status,
                     int32_t samples_per_channel, double timeout,
                     double *buffer, uint32_t buffer_size_samples,
                     uint32_t *samples_read_per_channel);
int prismc_scan_status(prismc_t *dev, uint16_t *status,
                       uint32_t *samples_per_channel);
int prismc_scan_cleanup(prismc_t *dev);
int prismc_scan_ch_count(prismc_t *dev);
int prismc_scan_buf_size(prismc_t *dev, uint32_t *buffer_size_samples);

#ifdef __cplusplus
}
#endif

#endif /* PRISMC_H */
