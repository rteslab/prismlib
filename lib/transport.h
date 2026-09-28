#ifndef TRANSPORT_H
#define TRANSPORT_H

#include <stdint.h>
#include <stddef.h>

int  tcp_connect(const char *ip, uint16_t port);
int  tcp_connect_retry(const char *ip, uint16_t port, int timeout_ms, int interval_ms);
int  tcp_send_all(int fd, const void *buf, size_t len);
int  tcp_recv_all(int fd, void *buf, size_t len, int timeout_ms);
void tcp_close(int fd);

/* UDP helpers (scan data reception) */
int udp_open_recv(uint16_t port);
/* Returns bytes received (>0), 0 on timeout, -1 on error. */
int udp_recv_frame(int fd, void *buf, size_t max_len, int timeout_ms);

/*
 * As udp_recv_frame(), but also waits on wake_fd.  Pass wake_fd < 0 to disable.
 * Returns UDP_RECV_WOKEN when wake_fd became readable; the caller drains it.
 */
#define UDP_RECV_WOKEN  (-2)
int udp_recv_frame_wake(int fd, int wake_fd, void *buf, size_t max_len,
                        int timeout_ms);

#endif /* TRANSPORT_H */
