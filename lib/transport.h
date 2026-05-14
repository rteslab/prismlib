#ifndef TRANSPORT_H
#define TRANSPORT_H

#include <stdint.h>
#include <stddef.h>

int  tcp_connect(const char *ip, uint16_t port);
int  tcp_connect_retry(const char *ip, uint16_t port, int timeout_ms, int interval_ms);
int  tcp_send_all(int fd, const void *buf, size_t len);
int  tcp_recv_all(int fd, void *buf, size_t len, int timeout_ms);
void tcp_close(int fd);

#endif /* TRANSPORT_H */
