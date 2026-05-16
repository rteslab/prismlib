#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <stdint.h>
#include <stddef.h>

/* ── Command IDs ───────────────────────────────────────────────────────── */
#define CMD_OPEN            0x01u
#define CMD_CLOSE           0x02u
#define CMD_IS_OPEN         0x03u
#define CMD_INFO            0x04u
#define CMD_FW_VERSION      0x06u
#define CMD_SERIAL          0x07u
#define CMD_CAL_DATE        0x10u
#define CMD_CAL_READ        0x11u
#define CMD_IEPE_READ       0x20u
#define CMD_IEPE_WRITE      0x21u
#define CMD_IEPE_DIAG       0x22u
#define CMD_SAMPLERATE_READ  0x40u
#define CMD_SAMPLERATE_WRITE 0x41u
#define CMD_SCAN_START      0x50u
#define CMD_SCAN_STOP       0x51u
#define CMD_SCAN_DATA       0x52u
#define CMD_SCAN_STATUS     0x53u
#define CMD_SCAN_CLEANUP    0x54u
#define CMD_SCAN_CH_COUNT   0x55u
#define CMD_SCAN_BUF_SIZE   0x56u

/* ── Server status byte values ─────────────────────────────────────────── */
#define SRV_OK              0x00u
#define SRV_HW_OVERRUN      0x01u
#define SRV_SCAN_STOPPED    0x02u
#define SRV_BUSY            0xFEu
#define SRV_BAD_PARAM       0xFFu

/* ── Scan push-frame layout (UDP, port 7778) ────────────────────────────
 * [cnt_lo:1][cnt_hi:1][CMD_SCAN_DATA:1][n_samples_lo:1][n_samples_hi:1][status:1][n_samples * 4ch * 3B int24-LE]
 * cnt      : uint16 LE chunk counter, wraps at 65535; use to detect UDP loss.
 * n_samples: uint16 LE, up to UDP_CHUNK_SAMPLES (100) per datagram.
 * SCAN_HEADER_SIZE refers to the 4-byte scan-specific header (after the 2-byte counter).
 * ──────────────────────────────────────────────────────────────────────── */
#define SCAN_HEADER_SIZE        4u
#define SCAN_N_CH               4u
#define SCAN_BYTES_PER_S        3u
#define SCAN_N_SAMPLES_MAX      512u
#define SCAN_PAYLOAD_MAX        (SCAN_N_SAMPLES_MAX * SCAN_N_CH * SCAN_BYTES_PER_S)  /* 6144 B */

/* ── Request: [cmd:1][payload_len:1][payload:N] ─────────────────────────
 * Returns total bytes written into out_buf.                               */
int proto_build_req(uint8_t cmd, const uint8_t *payload, uint8_t payload_len,
                    uint8_t *out_buf);

/* ── Response header: first 3 bytes [cmd:1][status:1][payload_len:1] ────
 * Returns payload_len (>=0) on success; maps server error to ResultCode
 * via *result_out (RESULT_SUCCESS on STATUS_OK).                          */
int proto_parse_res_hdr(const uint8_t hdr[3], uint8_t expected_cmd,
                        int *result_out, uint8_t *payload_len_out);

/* ── Scan frame helpers ─────────────────────────────────────────────────── */
/* Returns 1 if frame[0]==CMD_SCAN_DATA */
int proto_is_scan_frame(const uint8_t *frame);
/* status byte from scan frame */
uint8_t proto_scan_frame_status(const uint8_t *frame);
/* pointer to raw ADC payload inside a complete frame buffer */
const uint8_t *proto_scan_frame_payload(const uint8_t *frame);

/* ── Data type helpers ──────────────────────────────────────────────────── */
int32_t proto_int24_to_int32(const uint8_t *p);  /* 3-byte LE int24 → int32 */

void    proto_pack_u8(uint8_t *p, uint8_t v);
void    proto_pack_u32(uint8_t *p, uint32_t v);
void    proto_pack_double(uint8_t *p, double v);
uint8_t proto_unpack_u8(const uint8_t *p);
int32_t proto_unpack_i32(const uint8_t *p);
double  proto_unpack_double(const uint8_t *p);

/* Map SRV_xxx status to RESULT_xxx */
int proto_srv_status_to_result(uint8_t srv_status);

#endif /* PROTOCOL_H */
