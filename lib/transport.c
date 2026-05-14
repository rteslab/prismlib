/*
 * transport.c — TCP socket abstraction for PRISM-CLib
 * POSIX sockets (Linux / Raspberry Pi CM4)
 */
#include "transport.h"

#include <sys/socket.h>
#include <sys/select.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>

int tcp_connect(const char *ip, uint16_t port)
{
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return -1;

    int flag = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(flag));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons(port);
    if (inet_pton(AF_INET, ip, &addr.sin_addr) != 1) {
        close(fd);
        return -1;
    }

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

/* Send exactly len bytes; returns 0 on success, -1 on error. */
int tcp_send_all(int fd, const void *buf, size_t len)
{
    const char *p = (const char *)buf;
    while (len > 0) {
        ssize_t n = send(fd, p, len, MSG_NOSIGNAL);
        if (n <= 0)
            return -1;
        p   += n;
        len -= (size_t)n;
    }
    return 0;
}

/* Receive exactly len bytes with timeout_ms (-1 = infinite).
 * Returns 0 on success, -1 on error/timeout/disconnect. */
int tcp_recv_all(int fd, void *buf, size_t len, int timeout_ms)
{
    char *p = (char *)buf;
    while (len > 0) {
        if (timeout_ms >= 0) {
            fd_set rfds;
            FD_ZERO(&rfds);
            FD_SET(fd, &rfds);
            struct timeval tv = {
                .tv_sec  = timeout_ms / 1000,
                .tv_usec = (timeout_ms % 1000) * 1000
            };
            int rc = select(fd + 1, &rfds, NULL, NULL, &tv);
            if (rc <= 0)
                return -1;
        }
        ssize_t n = recv(fd, p, len, 0);
        if (n <= 0)
            return -1;
        p   += n;
        len -= (size_t)n;
    }
    return 0;
}

void tcp_close(int fd)
{
    if (fd >= 0)
        close(fd);
}

/* 연결 성공까지 interval_ms 간격으로 재시도. timeout_ms 초과 시 -1 반환. */
int tcp_connect_retry(const char *ip, uint16_t port, int timeout_ms, int interval_ms)
{
    int elapsed = 0;
    while (elapsed < timeout_ms) {
        int fd = tcp_connect(ip, port);
        if (fd >= 0)
            return fd;
        usleep((useconds_t)interval_ms * 1000u);
        elapsed += interval_ms;
    }
    return -1;
}
