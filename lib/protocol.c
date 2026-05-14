/*
 * protocol.c — PRISM binary frame encode/decode
 *
 * Wire formats (all little-endian):
 *   Request : [cmd:1][payload_len:1][payload:payload_len]
 *   Response: [cmd:1][status:1][payload_len:1][payload:payload_len]
 *   ScanPush: [CMD_SCAN_DATA:1][n_samples:1][status:1][768-byte ADC data]
 */
#include "protocol.h"
#include "../include/prismc.h"

#include <string.h>

/* ── Pack helpers ───────────────────────────────────────────────────────── */

void proto_pack_u8(uint8_t *p, uint8_t v)   { p[0] = v; }
void proto_pack_u32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
void proto_pack_double(uint8_t *p, double v) { memcpy(p, &v, 8); }

uint8_t proto_unpack_u8(const uint8_t *p)    { return p[0]; }
int32_t proto_unpack_i32(const uint8_t *p)   { int32_t v; memcpy(&v, p, 4); return v; }
double  proto_unpack_double(const uint8_t *p){ double  v; memcpy(&v, p, 8); return v; }

/* Sign-extend 3-byte little-endian int24 to int32 */
int32_t proto_int24_to_int32(const uint8_t *p)
{
    int32_t v = (int32_t)((uint32_t)p[0]
                         | ((uint32_t)p[1] << 8)
                         | ((uint32_t)p[2] << 16));
    if (v & 0x800000)
        v |= (int32_t)0xFF000000;
    return v;
}

/* ── Request builder ────────────────────────────────────────────────────── */

int proto_build_req(uint8_t cmd, const uint8_t *payload, uint8_t payload_len,
                    uint8_t *out_buf)
{
    out_buf[0] = cmd;
    out_buf[1] = payload_len;
    if (payload_len > 0 && payload)
        memcpy(&out_buf[2], payload, payload_len);
    return 2 + payload_len;
}

/* ── Response header parser ─────────────────────────────────────────────── */

int proto_parse_res_hdr(const uint8_t hdr[3], uint8_t expected_cmd,
                        int *result_out, uint8_t *payload_len_out)
{
    if (hdr[0] != expected_cmd) {
        *result_out = RESULT_COMMS_FAILURE;
        return -1;
    }
    *result_out      = proto_srv_status_to_result(hdr[1]);
    *payload_len_out = hdr[2];
    return (int)hdr[2];
}

/* ── Scan frame helpers ─────────────────────────────────────────────────── */

int proto_is_scan_frame(const uint8_t *frame)
{
    return frame[0] == (uint8_t)CMD_SCAN_DATA;
}

uint8_t proto_scan_frame_status(const uint8_t *frame)
{
    return frame[2];
}

const uint8_t *proto_scan_frame_payload(const uint8_t *frame)
{
    return &frame[3];
}

/* ── Status mapping ─────────────────────────────────────────────────────── */

int proto_srv_status_to_result(uint8_t s)
{
    switch (s) {
        case SRV_OK:        return RESULT_SUCCESS;
        case SRV_BUSY:      return RESULT_BUSY;
        case SRV_BAD_PARAM: return RESULT_BAD_PARAMETER;
        default:            return RESULT_UNDEFINED;
    }
}
