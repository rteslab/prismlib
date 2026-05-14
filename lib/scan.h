#ifndef SCAN_H
#define SCAN_H

#include <stdint.h>
#include <stddef.h>
#include <pthread.h>

/* Per-device scan context — embedded inside prismc_t */
typedef struct {
    /* socket (borrowed from prismc_t, not owned) */
    int      sockfd;

    /* scan parameters */
    uint8_t  ch_mask;
    int      ch_count;
    uint32_t options;
    uint32_t buf_cap;   /* ring buffer capacity in samples-per-channel */

    /* per-channel scale factors (copied from prismc_t at scan_start) */
    double cal_slope[4];
    double cal_offset[4];
    double sensitivity[4];

    /* ring buffer — interleaved: ch_count doubles per sample-set */
    double  *ring;          /* malloc'd: buf_cap * ch_count * sizeof(double) */
    uint32_t head;          /* write index (in sample-sets) */
    uint32_t tail;          /* read  index (in sample-sets) */
    uint32_t avail;         /* available sample-sets */

    pthread_mutex_t mtx;
    pthread_cond_t  cond;

    /* status flags (STATUS_HW_OVERRUN | STATUS_BUFFER_OVERRUN | STATUS_RUNNING) */
    uint16_t status;

    /* lifecycle */
    int      active;        /* 1 after scan_start, 0 after scan_cleanup */
    pthread_t tid;
    int       thread_exit;  /* set to 1 to ask thread to exit */
} ScanCtx_t;

void scan_ctx_init(ScanCtx_t *ctx);

int  scan_start(ScanCtx_t *ctx, int sockfd,
                uint8_t ch_mask, uint32_t buf_cap_per_ch,
                uint32_t options,
                double *cal_slope, double *cal_offset, double *sensitivity);

/* Signal stop to the device (send CMD_SCAN_STOP over the socket).
 * The scan thread will exit on its own after receiving the STOPPED frame. */
int  scan_stop_send(ScanCtx_t *ctx);

/* Wait for the scan thread to exit (after STOPPED frame received).
 * Must be called before scan_cleanup(). */
int  scan_join(ScanCtx_t *ctx);

/* Free ring buffer and reset context. Call after scan_join(). */
void scan_cleanup(ScanCtx_t *ctx);

int  scan_read(ScanCtx_t *ctx,
               int32_t req_per_ch, double timeout_s,
               double *out, uint32_t out_size, uint32_t *read_per_ch,
               uint16_t *status_out);

int      scan_get_status(ScanCtx_t *ctx, uint16_t *status_out, uint32_t *avail_out);
int      scan_ch_count(ScanCtx_t *ctx);
uint32_t scan_buf_size(ScanCtx_t *ctx);

#endif /* SCAN_H */
