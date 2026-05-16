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
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include <string.h>
#include <errno.h>

#define TCP_CONNECT_TIMEOUT_MS 500

int tcp_connect(const char *ip, uint16_t port)
{
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return -1;

    int flag = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY,  &flag, sizeof(flag));
    setsockopt(fd, IPPROTO_TCP, TCP_QUICKACK, &flag, sizeof(flag));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons(port);
    if (inet_pton(AF_INET, ip, &addr.sin_addr) != 1) {
        close(fd);
        return -1;
    }

    int saved_flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, saved_flags | O_NONBLOCK);

    int rc = connect(fd, (struct sockaddr *)&addr, sizeof(addr));
    if (rc == 0) {
        fcntl(fd, F_SETFL, saved_flags);
        return fd;
    }
    if (errno != EINPROGRESS) {
        close(fd);
        return -1;
    }

    fd_set wfds;
    FD_ZERO(&wfds);
    FD_SET(fd, &wfds);
    struct timeval tv = {
        .tv_sec  = TCP_CONNECT_TIMEOUT_MS / 1000,
        .tv_usec = (TCP_CONNECT_TIMEOUT_MS % 1000) * 1000
    };
    rc = select(fd + 1, NULL, &wfds, NULL, &tv);
    if (rc <= 0) {
        close(fd);
        return -1;
    }

    int err = 0;
    socklen_t errlen = sizeof(err);
    getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &errlen);
    if (err != 0) {
        close(fd);
        return -1;
    }

    fcntl(fd, F_SETFL, saved_flags);
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
        /* Linux resets TCP_QUICKACK after each ACK — re-arm every recv. */
        int flag = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_QUICKACK, &flag, sizeof(flag));
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

int udp_open_recv(uint16_t port)
{
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0)
        return -1;

    int flag = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &flag, sizeof(flag));

    int rcvbuf = 32 * 1024 * 1024;   /* 32 MB — 512kSPS 버퍼 오버런 방지 */
    setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons(port);
    addr.sin_addr.s_addr = INADDR_ANY;

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

int udp_recv_frame(int fd, void *buf, size_t max_len, int timeout_ms)
{
    fd_set rfds;
    FD_ZERO(&rfds);
    FD_SET(fd, &rfds);
    struct timeval tv = {
        .tv_sec  = timeout_ms / 1000,
        .tv_usec = (timeout_ms % 1000) * 1000
    };
    int rc = select(fd + 1, &rfds, NULL, NULL, &tv);
    if (rc < 0)
        return -1;
    if (rc == 0)
        return 0;
    ssize_t n = recv(fd, buf, max_len, 0);
    return (n < 0) ? -1 : (int)n;
}

/* 연결 성공까지 interval_ms 간격으로 재시도. timeout_ms 초과 시 -1 반환. */
int tcp_connect_retry(const char *ip, uint16_t port, int timeout_ms, int interval_ms)
{
    int elapsed = 0;
    while (elapsed < timeout_ms) {
        int fd = tcp_connect(ip, port);
        if (fd >= 0)
            return fd;
        struct timespec ts = { interval_ms / 1000,
                               (long)(interval_ms % 1000) * 1000000L };
        nanosleep(&ts, NULL);
        elapsed += interval_ms;
    }
    return -1;
}
