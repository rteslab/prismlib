/*
 * prismlib.h — PRISM-CLib public API
 * Handle-based multi-device support: each prismlib_open() returns an independent prismlib_t*.
 */
#ifndef prismlib_H
#define prismlib_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── GPIO LED identifiers (Raspberry Pi CM4) ───────────────────────────── */
#define PRISM_LED_PWR   21   /* ACT LED  (GPIO 21) */
#define PRISM_LED_ERR   20   /* GPIO 20 */
#define PRISM_LED_ALARM 16   /* GPIO 16 */

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
#define OPTS_TCP_DATA         0x0020u  /* push scan data over TCP instead of UDP */

/* ── Scan status flags ─────────────────────────────────────────────────── */
#define STATUS_HW_OVERRUN     0x0001u
#define STATUS_BUFFER_OVERRUN 0x0002u
#define STATUS_RUNNING        0x0008u

/* ── Hardware info structure ───────────────────────────────────────────── */
struct PrismlibDeviceInfo {
    uint8_t NUM_AI_CHANNELS;
    int32_t AI_MIN_CODE;
    int32_t AI_MAX_CODE;
    double  AI_MIN_VOLTAGE;
    double  AI_MAX_VOLTAGE;
    double  AI_MIN_RANGE;
    double  AI_MAX_RANGE;
};

/* ── Opaque device handle ──────────────────────────────────────────────── */
typedef struct prismlib_s prismlib_t;

/* ── Device management ─────────────────────────────────────────────────── */
prismlib_t           *prismlib_open(const char *ip, uint16_t port);
int                 prismlib_is_open(prismlib_t *dev);
int                 prismlib_close(prismlib_t *dev);

struct PrismlibDeviceInfo *prismlib_info(prismlib_t *dev);
int                 prismlib_fw_version(prismlib_t *dev, char *buffer);
int                 prismlib_serial(prismlib_t *dev, char *buffer);
const char         *prismlib_error_msg(int result);

/* ── Calibration ───────────────────────────────────────────────────────── */
int prismlib_cal_date(prismlib_t *dev, char *buffer);
int prismlib_cal_read(prismlib_t *dev, uint8_t channel, double *slope, double *offset);
int prismlib_cal_write(prismlib_t *dev, uint8_t channel, double slope, double offset);

/* ── IEPE ──────────────────────────────────────────────────────────────── */
int prismlib_iepe_read(prismlib_t *dev, uint8_t channel, uint8_t *config);
int prismlib_iepe_write(prismlib_t *dev, uint8_t channel, uint8_t config);
/* fault_mask: bit0=ch1, bit1=ch2, bit2=ch3, bit3=ch4 (1=fault) */
int prismlib_iepe_diag(prismlib_t *dev, uint8_t *fault_mask);

/* ── Sensor sensitivity ────────────────────────────────────────────────── */
int prismlib_sens_read(prismlib_t *dev, uint8_t channel, double *value);
int prismlib_sens_write(prismlib_t *dev, uint8_t channel, double value);

/* ── Sampling rate ─────────────────────────────────────────────────────── */
typedef enum {
    PRISM_SR_64K  = 0,   /*  64 kS/s */
    PRISM_SR_128K = 1,   /* 128 kS/s */
    PRISM_SR_170K = 2,   /* 170 kS/s */
    PRISM_SR_256K = 3,   /* 256 kS/s */
    PRISM_SR_512K = 4,   /* 512 kS/s */
} PrismSampleRate_e;

int prismlib_sampleRate_read(prismlib_t *dev, PrismSampleRate_e *sample_rate);
int prismlib_sampleRate_write(prismlib_t *dev, PrismSampleRate_e sample_rate);

/* ── Analog input scan ─────────────────────────────────────────────────── */
int prismlib_scan_start(prismlib_t *dev, uint8_t channel_mask,
                      uint32_t samples_per_channel, uint32_t options);
int prismlib_scan_stop(prismlib_t *dev);
int prismlib_scan_read(prismlib_t *dev, uint16_t *status,
                     int32_t samples_per_channel, double timeout,
                     double *buffer, uint32_t buffer_size_samples,
                     uint32_t *samples_read_per_channel);
int prismlib_scan_status(prismlib_t *dev, uint16_t *status,
                       uint32_t *samples_per_channel);
int prismlib_scan_cleanup(prismlib_t *dev);
int prismlib_scan_ch_count(prismlib_t *dev);
int prismlib_scan_buf_size(prismlib_t *dev, uint32_t *buffer_size_samples);

/* ── LED control (Raspberry Pi GPIO — only meaningful when running on-device)
 *   Requires libgpiod. No-op stubs are used if built without HAVE_GPIOD.    */

/* led_id: PRISM_LED_PWR / PRISM_LED_ERR / PRISM_LED_ALARM */
int prismlib_led_write(prismlib_t *dev, int led_id, int state);

/* enable=0: save current RUN(ACT) trigger and switch to manual control.
 * enable=1: restore saved trigger (return to kernel control).              */
int prismlib_run_led_kernel(prismlib_t *dev, int enable);

/* Set RUN(ACT) LED on/off. prismlib_run_led_kernel(dev,0) must be called first. */
int prismlib_run_led_write(prismlib_t *dev, int state);

#ifdef __cplusplus
}
#endif

#endif /* prismlib_H */
