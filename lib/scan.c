/*
 * scan.c — Background scan thread and ring buffer for PRISM-CLib
 *
 * The device pushes scan frames via UDP (port 7778):
 *   [cnt_lo:1][cnt_hi:1][CMD_SCAN_DATA:1][n_lo:1][n_hi:1][status:1][n_samples * 4ch * 3B int24-LE]
 *
 * TCP is used only for control commands (CMD_SCAN_START / CMD_SCAN_STOP).
 * Thread exits when SRV_SCAN_STOPPED flag is set or thread_exit is signalled.
 */
#include "scan.h"
#include "transport.h"
#include "protocol.h"
#include "../include/prismlib.h"

#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/select.h>
#include <unistd.h>
#include <fcntl.h>

#define UDP_DATA_PORT   7778u
#define UDP_RECV_TMO_MS  200   /* periodic wakeup to check thread_exit */

/* ── Wake pipe ──────────────────────────────────────────────────────────── */

/* Wake the receive thread so it re-checks thread_exit without waiting for the
   receive timeout.  Safe to call when no pipe exists. */
static void scan_wake(ScanCtx_t *ctx)
{
    if (ctx->wake_fd[1] >= 0)
        (void)write(ctx->wake_fd[1], "w", 1);
}

static void scan_wake_drain(ScanCtx_t *ctx)
{
    uint8_t scratch[16];
    while (ctx->wake_fd[0] >= 0 &&
           read(ctx->wake_fd[0], scratch, sizeof(scratch)) > 0)
        ;   /* read end is non-blocking; drain whatever is queued */
}

static int scan_wake_open(ScanCtx_t *ctx)
{
    if (pipe(ctx->wake_fd) != 0) {
        ctx->wake_fd[0] = -1;
        ctx->wake_fd[1] = -1;
        return -1;
    }
    /* Non-blocking read end so draining never stalls the thread. */
    fcntl(ctx->wake_fd[0], F_SETFL,
          fcntl(ctx->wake_fd[0], F_GETFL, 0) | O_NONBLOCK);
    return 0;
}

static void scan_wake_close(ScanCtx_t *ctx)
{
    for (int i = 0; i < 2; i++) {
        if (ctx->wake_fd[i] >= 0) {
            close(ctx->wake_fd[i]);
            ctx->wake_fd[i] = -1;
        }
    }
}

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

/* ── Scan thread (TCP) ───────────────────────────────────────────────────── */

/* TCP scan frame: [CMD:1][n_lo:1][n_hi:1][status:1][n_samples * 4ch * 3B] */
#define TCP_RECV_TMO_MS  200   /* periodic wakeup to check thread_exit */

static void *scan_thread_tcp(void *arg)
{
    ScanCtx_t *ctx = (ScanCtx_t *)arg;
    uint8_t    payload[SCAN_PAYLOAD_MAX];
    double     sample_set[SCAN_N_CH];

    while (!ctx->thread_exit) {
        fd_set rfd;
        FD_ZERO(&rfd);
        FD_SET(ctx->sockfd, &rfd);
        struct timeval tv = { .tv_sec = 0, .tv_usec = TCP_RECV_TMO_MS * 1000 };
        int sel = select(ctx->sockfd + 1, &rfd, NULL, NULL, &tv);
        if (sel < 0)
            break;
        if (sel == 0)
            continue;   /* timeout — re-check thread_exit */

        /* Read CMD byte */
        uint8_t cmd;
        if (tcp_recv_all(ctx->sockfd, &cmd, 1, 3000) != 0)
            break;

        if (cmd == CMD_SCAN_STOP) {
            /* Consume remaining 2 bytes of the stop response, then exit. */
            uint8_t rest[2];
            tcp_recv_all(ctx->sockfd, rest, 2, 3000);
            break;
        }

        if (cmd != CMD_SCAN_DATA)
            break;  /* unexpected command — can't recover stream position */

        /* Read [n_lo n_hi status] */
        uint8_t hdr[3];
        if (tcp_recv_all(ctx->sockfd, hdr, 3, 3000) != 0)
            break;
        uint16_t n_samples  = (uint16_t)hdr[0] | ((uint16_t)hdr[1] << 8);
        uint8_t  srv_status = hdr[2];

        uint32_t payload_len = (uint32_t)n_samples * SCAN_N_CH * SCAN_BYTES_PER_S;
        if (payload_len == 0 || payload_len > SCAN_PAYLOAD_MAX)
            break;

        if (tcp_recv_all(ctx->sockfd, payload, payload_len, 5000) != 0)
            break;

        if (srv_status & SRV_HW_OVERRUN) {
            pthread_mutex_lock(&ctx->mtx);
            ctx->status |= STATUS_HW_OVERRUN;
            pthread_mutex_unlock(&ctx->mtx);
        }

        pthread_mutex_lock(&ctx->mtx);
        const uint8_t *p = payload;
        for (uint32_t s = 0; s < n_samples; s++) {
            int out_ch = 0;
            for (int ch = 0; ch < (int)SCAN_N_CH; ch++) {
                int32_t raw = proto_int24_to_int32(p);
                p += SCAN_BYTES_PER_S;
                if (!(ctx->ch_mask & (uint8_t)(1u << ch)))
                    continue;
                sample_set[out_ch++] = convert_sample(raw,
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

/* ── Scan thread (UDP) ───────────────────────────────────────────────────── */

/* UDP datagram buffer: 2-byte counter + 4-byte scan header + max payload */
#define UDP_DGRAM_MAX  (2u + SCAN_HEADER_SIZE + SCAN_PAYLOAD_MAX)

static void *scan_thread(void *arg)
{
    ScanCtx_t *ctx = (ScanCtx_t *)arg;
    uint8_t dgram[UDP_DGRAM_MAX];
    double  sample_set[SCAN_N_CH];

    while (!ctx->thread_exit) {
        int n = udp_recv_frame_wake(ctx->udp_fd, ctx->wake_fd[0],
                                    dgram, sizeof(dgram), UDP_RECV_TMO_MS);
        if (n == UDP_RECV_WOKEN) {
            scan_wake_drain(ctx);
            continue;   /* woken by scan_stop_send — re-check thread_exit */
        }
        if (n < 0)
            break;
        if (n == 0)
            continue;   /* timeout — re-check thread_exit */

        /* UDP frame: [cnt:2][CMD_SCAN_DATA:1][n_lo:1][n_hi:1][status:1][data...] */
        if ((size_t)n < 2u + SCAN_HEADER_SIZE)
            continue;

        const uint8_t *hdr = dgram + 2;   /* skip 2-byte counter */
        if (!proto_is_scan_frame(hdr))
            continue;

        /* Sequence counter: consecutive frames differ by exactly 1 (mod 65536).
         * The device does not advance it when it skips a chunk, so a gap here is
         * a frame that was sent and never arrived. */
        uint16_t cnt = (uint16_t)dgram[0] | ((uint16_t)dgram[1] << 8);
        if (ctx->have_cnt) {
            uint16_t gap = (uint16_t)(cnt - ctx->last_cnt - 1u);
            if (gap) {
                pthread_mutex_lock(&ctx->mtx);
                ctx->lost_frames += gap;
                ctx->status |= STATUS_DATA_LOST;
                pthread_mutex_unlock(&ctx->mtx);
            }
        }
        ctx->have_cnt = 1;
        ctx->last_cnt = cnt;

        uint16_t n_samples   = (uint16_t)hdr[1] | ((uint16_t)hdr[2] << 8);
        uint8_t  srv_status  = hdr[3];
        uint32_t payload_len = (uint32_t)n_samples * SCAN_N_CH * SCAN_BYTES_PER_S;

        if (payload_len == 0 || (size_t)n < 2u + SCAN_HEADER_SIZE + payload_len)
            continue;

        if (srv_status & SRV_HW_OVERRUN) {
            pthread_mutex_lock(&ctx->mtx);
            ctx->status |= STATUS_HW_OVERRUN;
            pthread_mutex_unlock(&ctx->mtx);
        }

        /* Convert each sample-set and push to ring buffer */
        pthread_mutex_lock(&ctx->mtx);
        const uint8_t *p = dgram + 2u + SCAN_HEADER_SIZE;
        for (uint32_t s = 0; s < n_samples; s++) {
            int out_ch = 0;
            for (int ch = 0; ch < (int)SCAN_N_CH; ch++) {
                int32_t raw = proto_int24_to_int32(p);
                p += SCAN_BYTES_PER_S;
                if (!(ctx->ch_mask & (uint8_t)(1u << ch)))
                    continue;
                sample_set[out_ch++] = convert_sample(raw,
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
    ctx->udp_fd     = -1;
    ctx->wake_fd[0] = -1;
    ctx->wake_fd[1] = -1;
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
    ctx->have_cnt = 0;
    ctx->last_cnt = 0;
    ctx->lost_frames = 0;

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

    void *(*thread_fn)(void *);
    if (options & OPTS_TCP_DATA) {
        /* TCP data mode: scan frames arrive on the control socket; no UDP. */
        thread_fn = scan_thread_tcp;
    } else {
        ctx->udp_fd = udp_open_recv((uint16_t)UDP_DATA_PORT);
        if (ctx->udp_fd < 0) {
            free(ctx->ring);
            ctx->ring = NULL;
            return RESULT_RESOURCE_UNAVAIL;
        }
        (void)scan_wake_open(ctx);   /* optional: thread exits on timeout without it */
        thread_fn = scan_thread;
    }

    ctx->thread_exit = 0;
    if (pthread_create(&ctx->tid, NULL, thread_fn, ctx) != 0) {
        scan_wake_close(ctx);
        if (ctx->udp_fd >= 0) {
            tcp_close(ctx->udp_fd);
            ctx->udp_fd = -1;
        }
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
    if (tcp_send_all(ctx->sockfd, req, (size_t)len) != 0)
        return RESULT_COMMS_FAILURE;

    if (!(ctx->options & OPTS_TCP_DATA)) {
        /* UDP mode: consume the CMD_SCAN_STOP response here.
         * (TCP mode: scan_thread_tcp consumes it and exits on its own.) */
        uint8_t hdr[3];
        tcp_recv_all(ctx->sockfd, hdr, sizeof(hdr), 3000);
    }

    ctx->thread_exit = 1;
    scan_wake(ctx);
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

int scan_is_running(ScanCtx_t *ctx)
{
    uint16_t st;

    if (!ctx->active || !ctx->tid)
        return 0;
    if (ctx->thread_exit)
        return 0;       /* stop requested; the thread has just not woken yet */

    pthread_mutex_lock(&ctx->mtx);
    st = ctx->status;
    pthread_mutex_unlock(&ctx->mtx);

    return (st & STATUS_RUNNING) ? 1 : 0;
}

void scan_cleanup(ScanCtx_t *ctx)
{
    scan_wake_close(ctx);
    if (ctx->udp_fd >= 0) {
        tcp_close(ctx->udp_fd);
        ctx->udp_fd = -1;
    }
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

uint32_t scan_lost(ScanCtx_t *ctx)
{
    pthread_mutex_lock(&ctx->mtx);
    uint32_t n = ctx->lost_frames;
    pthread_mutex_unlock(&ctx->mtx);
    return n;
}

int scan_ch_count(ScanCtx_t *ctx)
{
    return ctx->active ? ctx->ch_count : 0;
}

uint32_t scan_buf_size(ScanCtx_t *ctx)
{
    return ctx->buf_cap;
}
