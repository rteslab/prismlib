/*
 * prismlib.c — PRISM-CLib main API implementation (handle-based)
 *
 * Request wire format : [cmd:1][payload_len:1][payload:N]
 * Response wire format: [cmd:1][status:1][payload_len:1][payload:N]
 * Scan push frame (UDP port 7778): [cnt:2][CMD_SCAN_DATA:1][n_samples:2][status:1][n_samples * 4ch * 3B ADC data]
 */
#include "../include/prismlib.h"
#include "transport.h"
#include "protocol.h"
#include "scan.h"
#include "led.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>
#include <time.h>

#define NCM_OPEN_TIMEOUT_MS  10000   /* max wait for TCP connection */
#define NCM_OPEN_INTERVAL_MS   200   /* TCP retry interval */

/* ── Internal handle structure ──────────────────────────────────────────── */

struct prismlib_s {
    int      sockfd;
    int      connected;
    char     serial[64];   /* device serial number, fetched in prismlib_open() */

    struct PrismlibDeviceInfo info;

    /* per-device calibration / sensitivity (client-side copies) */
    double cal_slope[4];
    double cal_offset[4];
    double sensitivity[4];
    double sample_rate;

    ScanCtx_t scan;
    LedCtx_t  led;
};

/* ── Helpers ────────────────────────────────────────────────────────────── */

/* Send a request and receive the full response.
 * payload_out must be at least 256 bytes; actual length in *plen_out. */
static int do_cmd(struct prismlib_s *dev, uint8_t cmd,
                  const uint8_t *req_payload, uint8_t req_len,
                  uint8_t *res_payload, uint8_t *plen_out)
{
    uint8_t req[258];
    int req_sz = proto_build_req(cmd, req_payload, req_len, req);
    if (tcp_send_all(dev->sockfd, req, (size_t)req_sz) != 0)
        return RESULT_COMMS_FAILURE;

    uint8_t hdr[3];
    if (tcp_recv_all(dev->sockfd, hdr, 3, 3000) != 0)
        return RESULT_TIMEOUT;

    int result;
    uint8_t plen;
    if (proto_parse_res_hdr(hdr, cmd, &result, &plen) < 0)
        return result;

    if (plen > 0) {
        if (tcp_recv_all(dev->sockfd, res_payload, plen, 3000) != 0)
            return RESULT_TIMEOUT;
    }
    if (plen_out)
        *plen_out = plen;
    return result;
}

static void reset_cal_sens(struct prismlib_s *dev)
{
    for (int i = 0; i < 4; i++) {
        dev->cal_slope[i]   = 1.0;
        dev->cal_offset[i]  = 0.0;
        dev->sensitivity[i] = 1000.0;
    }
    dev->sample_rate = 64000.0;
}

/* 샘플링 속도 기반 기본 링 버퍼 크기 (~2초 커버리지) */
static uint32_t default_buf_cap(double sample_rate)
{
    if (sample_rate <=   8000.0)  return    16000u;  /* ~2.0초 @   8kS/s */
    if (sample_rate <=  64000.0)  return   128000u;  /* ~2.0초 @  64kS/s */
    if (sample_rate <= 128000.0)  return   256000u;  /* ~2.0초 @ 128kS/s */
    if (sample_rate <= 256000.0)  return   512000u;  /* ~2.0초 @ 256kS/s */
    return                                1024000u;  /* ~2.0초 @ 512kS/s */
}

/* ── Device management ──────────────────────────────────────────────────── */

prismlib_t *prismlib_open(const char *ip, uint16_t port)
{
    int fd = tcp_connect_retry(ip, port, NCM_OPEN_TIMEOUT_MS, NCM_OPEN_INTERVAL_MS);
    if (fd < 0)
        return NULL;

    struct prismlib_s *dev = (struct prismlib_s *)calloc(1, sizeof(*dev));
    if (!dev) { tcp_close(fd); return NULL; }

    dev->sockfd    = fd;
    dev->connected = 1;
    reset_cal_sens(dev);
    scan_ctx_init(&dev->scan);
    led_ctx_init(&dev->led);

    /* Send CMD_OPEN and receive device info */
    uint8_t res[256];
    uint8_t plen;
    if (do_cmd(dev, (uint8_t)CMD_OPEN, NULL, 0, res, &plen) != RESULT_SUCCESS) {
        tcp_close(fd);
        free(dev);
        return NULL;
    }


    /* Fetch device info */
    if (do_cmd(dev, (uint8_t)CMD_INFO, NULL, 0, res, &plen) == RESULT_SUCCESS
        && plen >= 41) {
        const uint8_t *p = res;
        dev->info.NUM_AI_CHANNELS = proto_unpack_u8(p);       p += 1;
        dev->info.AI_MIN_CODE     = proto_unpack_i32(p);      p += 4;
        dev->info.AI_MAX_CODE     = proto_unpack_i32(p);      p += 4;
        dev->info.AI_MIN_VOLTAGE  = proto_unpack_double(p);   p += 8;
        dev->info.AI_MAX_VOLTAGE  = proto_unpack_double(p);   p += 8;
        dev->info.AI_MIN_RANGE    = proto_unpack_double(p);   p += 8;
        dev->info.AI_MAX_RANGE    = proto_unpack_double(p);
    }

    led_ctx_open(&dev->led);   /* GPIO request: PWR=1 ERR=0 ALARM=0 */

    /* Fetch serial number — used as cal file key */
    {
        uint8_t sres[64]; uint8_t splen = 0;
        if (do_cmd(dev, (uint8_t)CMD_SERIAL, NULL, 0, sres, &splen) == RESULT_SUCCESS
                && splen > 0 && splen < (uint8_t)sizeof(dev->serial)) {
            memcpy(dev->serial, sres, splen);
            dev->serial[splen] = '\0';
        } else {
            strncpy(dev->serial, "unknown", sizeof(dev->serial) - 1);
        }
    }

    /* Load factory calibration from device EEPROM */
    for (int i = 0; i < 4; i++) {
        uint8_t cal_req[1] = { (uint8_t)i };
        if (do_cmd(dev, (uint8_t)CMD_CAL_READ, cal_req, 1, res, &plen) == RESULT_SUCCESS
            && plen >= 16) {
            dev->cal_slope[i]  = proto_unpack_double(&res[0]);
            dev->cal_offset[i] = proto_unpack_double(&res[8]);
        }
    }

    return dev;
}

int prismlib_is_open(prismlib_t *dev)
{
    return (dev && dev->connected) ? 1 : 0;
}


int prismlib_close(prismlib_t *dev)
{
    if (!dev)
        return RESULT_BAD_PARAMETER;

    if (dev->scan.active) {
        scan_stop_send(&dev->scan);
        scan_join(&dev->scan);
        uint8_t res[4];
        do_cmd(dev, (uint8_t)CMD_SCAN_CLEANUP, NULL, 0, res, NULL); /* 서버: STATE_SCAN_STOPPING → STATE_IDLE */
        scan_cleanup(&dev->scan);
    }

    uint8_t res[8];
    do_cmd(dev, (uint8_t)CMD_CLOSE, NULL, 0, res, NULL);

    led_ctx_close(&dev->led);   /* GPIO release + restore ACT trigger */

    tcp_close(dev->sockfd);
    dev->sockfd    = -1;
    dev->connected = 0;
    free(dev);
    return RESULT_SUCCESS;
}

struct PrismlibDeviceInfo *prismlib_info(prismlib_t *dev)
{
    if (!dev || !dev->connected)
        return NULL;
    return &dev->info;
}

int prismlib_fw_version(prismlib_t *dev, char *buffer)
{
    if (!dev || !buffer)
        return RESULT_BAD_PARAMETER;
    uint8_t res[64];
    uint8_t plen = 0;
    int rc = do_cmd(dev, (uint8_t)CMD_FW_VERSION, NULL, 0, res, &plen);
    if (rc != RESULT_SUCCESS)
        return rc;
    memcpy(buffer, res, plen);
    buffer[plen] = '\0';
    return RESULT_SUCCESS;
}

int prismlib_serial(prismlib_t *dev, char *buffer)
{
    if (!dev || !buffer)
        return RESULT_BAD_PARAMETER;
    uint8_t res[64];
    uint8_t plen = 0;
    int rc = do_cmd(dev, (uint8_t)CMD_SERIAL, NULL, 0, res, &plen);
    if (rc != RESULT_SUCCESS)
        return rc;
    memcpy(buffer, res, plen);
    buffer[plen] = '\0';
    return RESULT_SUCCESS;
}

const char *prismlib_error_msg(int result)
{
    switch (result) {
        case RESULT_SUCCESS:           return "Success";
        case RESULT_BAD_PARAMETER:     return "Bad parameter";
        case RESULT_BUSY:              return "Device busy (scan running)";
        case RESULT_TIMEOUT:           return "Response timeout";
        case RESULT_LOCK_TIMEOUT:      return "Resource lock timeout";
        case RESULT_RESOURCE_UNAVAIL:  return "Resource unavailable (scan not active)";
        case RESULT_COMMS_FAILURE:     return "Communications failure";
        default:                       return "Undefined error";
    }
}

/* ── Calibration ────────────────────────────────────────────────────────── */

int prismlib_cal_date(prismlib_t *dev, char *buffer)
{
    if (!dev || !buffer)
        return RESULT_BAD_PARAMETER;
    uint8_t res[32];
    uint8_t plen = 0;
    int rc = do_cmd(dev, (uint8_t)CMD_CAL_DATE, NULL, 0, res, &plen);
    if (rc != RESULT_SUCCESS)
        return rc;
    memcpy(buffer, res, plen);
    buffer[plen] = '\0';
    return RESULT_SUCCESS;
}

int prismlib_cal_read(prismlib_t *dev, uint8_t channel, double *slope, double *offset)
{
    if (!dev || channel >= 4 || !slope || !offset)
        return RESULT_BAD_PARAMETER;
    *slope  = dev->cal_slope[channel];
    *offset = dev->cal_offset[channel];
    return RESULT_SUCCESS;
}

int prismlib_cal_write(prismlib_t *dev, uint8_t channel, double slope, double offset)
{
    if (!dev || channel >= 4)
        return RESULT_BAD_PARAMETER;
    if (dev->scan.active)
        return RESULT_BUSY;
    dev->cal_slope[channel]  = slope;
    dev->cal_offset[channel] = offset;
    return RESULT_SUCCESS;
}


/* ── IEPE ───────────────────────────────────────────────────────────────── */

int prismlib_iepe_read(prismlib_t *dev, uint8_t channel, uint8_t *config)
{
    if (!dev || channel >= 4 || !config)
        return RESULT_BAD_PARAMETER;
    uint8_t req[1] = { channel };
    uint8_t res[4];
    uint8_t plen = 0;
    int rc = do_cmd(dev, (uint8_t)CMD_IEPE_READ, req, 1, res, &plen);
    if (rc != RESULT_SUCCESS)
        return rc;
    *config = res[0];
    return RESULT_SUCCESS;
}

int prismlib_iepe_write(prismlib_t *dev, uint8_t channel, uint8_t config)
{
    if (!dev || channel >= 4)
        return RESULT_BAD_PARAMETER;
    uint8_t req[2] = { channel, config };
    uint8_t res[4];
    return do_cmd(dev, (uint8_t)CMD_IEPE_WRITE, req, 2, res, NULL);
}

int prismlib_iepe_diag(prismlib_t *dev, uint8_t *fault_mask)
{
    if (!dev || !fault_mask)
        return RESULT_BAD_PARAMETER;
    uint8_t res[4];
    uint8_t plen = 0;
    int rc = do_cmd(dev, (uint8_t)CMD_IEPE_DIAG, NULL, 0, res, &plen);
    if (rc != RESULT_SUCCESS)
        return rc;
    /* 하드웨어 배선상 ERR 핀 순서가 채널 번호와 반대(bit0=ch4, bit3=ch1)이므로
     * bit 순서를 반전하여 bit0=ch1, bit3=ch4 로 정규화한다. */
    uint8_t m = res[0];
    *fault_mask = (uint8_t)(((m & 0x01u) << 3) | ((m & 0x02u) << 1) |
                            ((m & 0x04u) >> 1) | ((m & 0x08u) >> 3));
    return RESULT_SUCCESS;
}

/* ── Sensitivity ────────────────────────────────────────────────────────── */

int prismlib_sens_read(prismlib_t *dev, uint8_t channel, double *value)
{
    if (!dev || channel >= 4 || !value)
        return RESULT_BAD_PARAMETER;
    *value = dev->sensitivity[channel];
    return RESULT_SUCCESS;
}

int prismlib_sens_write(prismlib_t *dev, uint8_t channel, double value)
{
    if (!dev || channel >= 4 || value <= 0.0)
        return RESULT_BAD_PARAMETER;
    if (dev->scan.active)
        return RESULT_BUSY;
    dev->sensitivity[channel] = value;
    return RESULT_SUCCESS;
}

/* ── Sampling rate ──────────────────────────────────────────────────────── */

static const double sr_hz[5] = { 64000.0, 128000.0, 170000.0, 256000.0, 512000.0 };

int prismlib_sampleRate_read(prismlib_t *dev, PrismSampleRate_e *sample_rate)
{
    if (!dev || !sample_rate)
        return RESULT_BAD_PARAMETER;
    uint8_t res[4];
    uint8_t plen = 0;
    int rc = do_cmd(dev, (uint8_t)CMD_SAMPLERATE_READ, NULL, 0, res, &plen);
    if (rc != RESULT_SUCCESS)
        return rc;
    if (res[0] > PRISM_SR_512K)
        return RESULT_BAD_PARAMETER;
    *sample_rate = (PrismSampleRate_e)res[0];
    return RESULT_SUCCESS;
}

int prismlib_sampleRate_write(prismlib_t *dev, PrismSampleRate_e sample_rate)
{
    if (!dev || sample_rate > PRISM_SR_512K)
        return RESULT_BAD_PARAMETER;
    uint8_t req[1] = { (uint8_t)sample_rate };
    uint8_t res[4];
    int rc = do_cmd(dev, (uint8_t)CMD_SAMPLERATE_WRITE, req, 1, res, NULL);
    if (rc == RESULT_SUCCESS)
        dev->sample_rate = sr_hz[sample_rate];
    return rc;
}

/* ── Scan ───────────────────────────────────────────────────────────────── */

int prismlib_scan_start(prismlib_t *dev, uint8_t channel_mask,
                      uint32_t samples_per_channel, uint32_t options)
{
    if (!dev || !channel_mask)
        return RESULT_BAD_PARAMETER;
    if (dev->scan.active)
        return RESULT_BUSY;

    uint8_t req[9];
    req[0] = channel_mask;
    proto_pack_u32(&req[1], samples_per_channel);
    proto_pack_u32(&req[5], options);

    uint8_t res[4];
    int rc = do_cmd(dev, (uint8_t)CMD_SCAN_START, req, 9, res, NULL);
    if (rc != RESULT_SUCCESS)
        return rc;

    /* 샘플링 속도 기반 기본 버퍼 크기 (~2초 커버리지), spc가 더 크면 spc 사용 */
    uint32_t def_cap = default_buf_cap(dev->sample_rate);
    uint32_t buf_cap = samples_per_channel > def_cap ? samples_per_channel : def_cap;

    rc = scan_start(&dev->scan, dev->sockfd,
                    channel_mask, buf_cap, options,
                    dev->cal_slope, dev->cal_offset, dev->sensitivity);
    return rc;
}

int prismlib_scan_stop(prismlib_t *dev)
{
    if (!dev)
        return RESULT_BAD_PARAMETER;
    if (!dev->scan.active)
        return RESULT_RESOURCE_UNAVAIL;
    return scan_stop_send(&dev->scan);
}

int prismlib_scan_read(prismlib_t *dev, uint16_t *status,
                     int32_t samples_per_channel, double timeout,
                     double *buffer, uint32_t buffer_size_samples,
                     uint32_t *samples_read_per_channel)
{
    if (!dev || !status || !buffer || !samples_read_per_channel)
        return RESULT_BAD_PARAMETER;
    return scan_read(&dev->scan,
                     samples_per_channel, timeout,
                     buffer, buffer_size_samples,
                     samples_read_per_channel, status);
}

int prismlib_scan_status(prismlib_t *dev, uint16_t *status,
                       uint32_t *samples_per_channel)
{
    if (!dev || !status || !samples_per_channel)
        return RESULT_BAD_PARAMETER;
    return scan_get_status(&dev->scan, status, samples_per_channel);
}

int prismlib_scan_cleanup(prismlib_t *dev)
{
    if (!dev)
        return RESULT_BAD_PARAMETER;
    if (!dev->scan.active)
        return RESULT_RESOURCE_UNAVAIL;

    uint16_t status;
    uint32_t avail;
    scan_get_status(&dev->scan, &status, &avail);
    if (status & STATUS_RUNNING)
        return RESULT_BUSY;  /* prismlib_scan_stop() must be called first */

    scan_join(&dev->scan);

    uint8_t res[4];
    int rc = do_cmd(dev, (uint8_t)CMD_SCAN_CLEANUP, NULL, 0, res, NULL);

    scan_cleanup(&dev->scan);
    return rc;
}

int prismlib_scan_ch_count(prismlib_t *dev)
{
    if (!dev)
        return 0;
    return scan_ch_count(&dev->scan);
}

int prismlib_scan_buf_size(prismlib_t *dev, uint32_t *buffer_size_samples)
{
    if (!dev || !buffer_size_samples)
        return RESULT_BAD_PARAMETER;
    if (!dev->scan.active)
        return RESULT_RESOURCE_UNAVAIL;
    *buffer_size_samples = scan_buf_size(&dev->scan);
    return RESULT_SUCCESS;
}

/* ── LED control ────────────────────────────────────────────────────────── */

int prismlib_led_write(prismlib_t *dev, int led_id, int state)
{
    if (!dev)
        return RESULT_BAD_PARAMETER;
    return led_write(&dev->led, led_id, state);
}

int prismlib_run_led_kernel(prismlib_t *dev, int enable)
{
    if (!dev)
        return RESULT_BAD_PARAMETER;
    return run_led_kernel(&dev->led, enable);
}

int prismlib_run_led_write(prismlib_t *dev, int state)
{
    if (!dev)
        return RESULT_BAD_PARAMETER;
    return run_led_write(&dev->led, state);
}
