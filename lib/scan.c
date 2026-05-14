/*
 * scan.c — Background scan thread and ring buffer for PRISM-CLib
 *
 * The device pushes 771-byte frames continuously:
 *   [CMD_SCAN_DATA:1][n_samples=64:1][status:1][768-byte ADC data]
 * ADC data layout: [s0_ch0 3B][s0_ch1 3B][s0_ch2 3B][s0_ch3 3B] × 64 (LE int24)
 *
 * Thread reads frames, converts to physical doubles, and writes to the ring
 * buffer. Exits when the SCAN_STOPPED flag is set in a received frame.
 */
#include "scan.h"
#include "transport.h"
#include "protocol.h"
#include "../include/prismc.h"

#include <stdlib.h>
#include <string.h>
#include <time.h>

/* ── Conversion ─────────────────────────────────────────────────────────── */

static double convert_sample(int32_t raw, double cal_slope, double cal_offset,
                              double sensitivity, uint32_t options)
{
    double code = (double)raw;
    if (!(options & OPTS_NOCALIBRATEDATA))
        code = (code - cal_offset) * cal_slope;

    if (options & OPTS_NOSCALEDATA)
        return code;

    double voltage = code / 8388607.0 * 5.0;
    return voltage / (sensitivity / 1000.0);
}

/* ── Ring buffer helpers ────────────────────────────────────────────────── */

static void ring_push(ScanCtx_t *ctx, const double *sample_set)
{
    if (ctx->avail >= ctx->buf_cap) {
        ctx->status |= STATUS_BUFFER_OVERRUN;
        ctx->tail    = (ctx->tail + 1) % ctx->buf_cap;
    } else {
        ctx->avail++;
    }
    double *dst = ctx->ring + ctx->head * (uint32_t)ctx->ch_count;
    memcpy(dst, sample_set, (size_t)ctx->ch_count * sizeof(double));
    ctx->head = (ctx->head + 1) % ctx->buf_cap;
}

/* ── Scan thread ─────────────────────────────────────────────────────────── */

static void *scan_thread(void *arg)
{
    ScanCtx_t *ctx = (ScanCtx_t *)arg;
    uint8_t frame[SCAN_FRAME_SIZE];
    double  sample_set[SCAN_N_CH];

    while (1) {
        /* Receive one full 771-byte frame */
        if (tcp_recv_all(ctx->sockfd, frame, SCAN_FRAME_SIZE, 5000) != 0)
            break;

        if (!proto_is_scan_frame(frame))
            break;

        uint8_t        srv_status = proto_scan_frame_status(frame);
        const uint8_t *payload    = proto_scan_frame_payload(frame);

        if (srv_status & SRV_HW_OVERRUN) {
            pthread_mutex_lock(&ctx->mtx);
            ctx->status |= STATUS_HW_OVERRUN;
            pthread_mutex_unlock(&ctx->mtx);
        }

        /* Convert each sample-set and push to ring buffer */
        pthread_mutex_lock(&ctx->mtx);
        const uint8_t *p = payload;
        for (uint32_t s = 0; s < SCAN_N_SAMPLES; s++) {
            for (int ch = 0; ch < ctx->ch_count; ch++) {
                int32_t raw = proto_int24_to_int32(p);
                p += SCAN_BYTES_PER_S;
                sample_set[ch] = convert_sample(raw,
                    ctx->cal_slope[ch], ctx->cal_offset[ch],
                    ctx->sensitivity[ch], ctx->options);
            }
            ring_push(ctx, sample_set);
        }
        pthread_cond_broadcast(&ctx->cond);
        pthread_mutex_unlock(&ctx->mtx);

        if (srv_status & SRV_SCAN_STOPPED)
            break;
    }

    pthread_mutex_lock(&ctx->mtx);
    ctx->status &= (uint16_t)~STATUS_RUNNING;
    pthread_cond_broadcast(&ctx->cond);
    pthread_mutex_unlock(&ctx->mtx);
    return NULL;
}

/* ── Public API ─────────────────────────────────────────────────────────── */

void scan_ctx_init(ScanCtx_t *ctx)
{
    memset(ctx, 0, sizeof(*ctx));
    pthread_mutex_init(&ctx->mtx, NULL);
    pthread_cond_init(&ctx->cond, NULL);
}

int scan_start(ScanCtx_t *ctx, int sockfd,
               uint8_t ch_mask, uint32_t buf_cap_per_ch,
               uint32_t options,
               double *cal_slope, double *cal_offset, double *sensitivity)
{
    ctx->sockfd   = sockfd;
    ctx->ch_mask  = ch_mask;
    ctx->options  = options;
    ctx->buf_cap  = buf_cap_per_ch;
    ctx->head     = 0;
    ctx->tail     = 0;
    ctx->avail    = 0;
    ctx->status   = STATUS_RUNNING;
    ctx->active   = 1;

    /* Count active channels */
    ctx->ch_count = 0;
    for (int i = 0; i < 4; i++) {
        if (ch_mask & (uint8_t)(1u << i)) {
            ctx->ch_count++;
            ctx->cal_slope[i]   = cal_slope[i];
            ctx->cal_offset[i]  = cal_offset[i];
            ctx->sensitivity[i] = sensitivity[i];
        }
    }
    if (ctx->ch_count == 0)
        return RESULT_BAD_PARAMETER;

    size_t ring_bytes = (size_t)buf_cap_per_ch * (size_t)ctx->ch_count * sizeof(double);
    ctx->ring = (double *)malloc(ring_bytes);
    if (!ctx->ring)
        return RESULT_RESOURCE_UNAVAIL;

    if (pthread_create(&ctx->tid, NULL, scan_thread, ctx) != 0) {
        free(ctx->ring);
        ctx->ring = NULL;
        return RESULT_UNDEFINED;
    }
    return RESULT_SUCCESS;
}

int scan_stop_send(ScanCtx_t *ctx)
{
    uint8_t req[2];
    int len = proto_build_req((uint8_t)CMD_SCAN_STOP, NULL, 0, req);
    /* Server sends no response; scan thread will see STOPPED frame */
    if (tcp_send_all(ctx->sockfd, req, (size_t)len) != 0)
        return RESULT_COMMS_FAILURE;
    return RESULT_SUCCESS;
}

int scan_join(ScanCtx_t *ctx)
{
    if (ctx->active && ctx->tid) {
        pthread_join(ctx->tid, NULL);
        ctx->tid = 0;
    }
    return RESULT_SUCCESS;
}

void scan_cleanup(ScanCtx_t *ctx)
{
    free(ctx->ring);
    ctx->ring   = NULL;
    ctx->active = 0;
    ctx->head   = 0;
    ctx->tail   = 0;
    ctx->avail  = 0;
    ctx->status = 0;
}

int scan_read(ScanCtx_t *ctx,
              int32_t req_per_ch, double timeout_s,
              double *out, uint32_t out_size, uint32_t *read_per_ch,
              uint16_t *status_out)
{
    if (!ctx->active)
        return RESULT_RESOURCE_UNAVAIL;

    pthread_mutex_lock(&ctx->mtx);

    /* -1 means read everything available immediately */
    if (req_per_ch < 0) {
        req_per_ch = (int32_t)ctx->avail;
        timeout_s  = 0.0;
    }

    uint32_t need = (uint32_t)req_per_ch;

    if (ctx->avail < need && (ctx->status & STATUS_RUNNING)) {
        if (timeout_s < 0.0) {
            /* infinite wait */
            while (ctx->avail < need && (ctx->status & STATUS_RUNNING))
                pthread_cond_wait(&ctx->cond, &ctx->mtx);
        } else if (timeout_s > 0.0) {
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            long ns = (long)(timeout_s * 1e9);
            ts.tv_sec  += ns / 1000000000L;
            ts.tv_nsec += ns % 1000000000L;
            if (ts.tv_nsec >= 1000000000L) { ts.tv_sec++; ts.tv_nsec -= 1000000000L; }
            while (ctx->avail < need && (ctx->status & STATUS_RUNNING)) {
                if (pthread_cond_timedwait(&ctx->cond, &ctx->mtx, &ts) != 0)
                    break;
            }
        }
    }

    uint32_t n = ctx->avail < need ? ctx->avail : need;
    uint32_t max_sets = out_size / (uint32_t)ctx->ch_count;
    if (n > max_sets)
        n = max_sets;

    for (uint32_t i = 0; i < n; i++) {
        const double *src = ctx->ring + ctx->tail * (uint32_t)ctx->ch_count;
        memcpy(&out[i * (uint32_t)ctx->ch_count], src,
               (size_t)ctx->ch_count * sizeof(double));
        ctx->tail = (ctx->tail + 1) % ctx->buf_cap;
    }
    ctx->avail -= n;

    *read_per_ch = n;
    *status_out  = ctx->status;
    pthread_mutex_unlock(&ctx->mtx);
    return RESULT_SUCCESS;
}

int scan_get_status(ScanCtx_t *ctx, uint16_t *status_out, uint32_t *avail_out)
{
    if (!ctx->active)
        return RESULT_RESOURCE_UNAVAIL;
    pthread_mutex_lock(&ctx->mtx);
    *status_out = ctx->status;
    *avail_out  = ctx->avail;
    pthread_mutex_unlock(&ctx->mtx);
    return RESULT_SUCCESS;
}

int scan_ch_count(ScanCtx_t *ctx)
{
    return ctx->active ? ctx->ch_count : 0;
}

uint32_t scan_buf_size(ScanCtx_t *ctx)
{
    return ctx->buf_cap;
}
