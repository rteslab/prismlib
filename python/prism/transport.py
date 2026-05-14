# transport.py — TCP socket wrapper for PRISM-PyLib
import socket


class Transport:
    def __init__(self):
        self._sock: socket.socket | None = None

    def connect(self, ip: str, port: int, timeout: float = 5.0) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.settimeout(timeout)
        s.connect((ip, port))
        s.settimeout(None)  # switch to blocking after connect
        self._sock = s

    def send_all(self, data: bytes) -> None:
        if self._sock is None:
            raise OSError("Not connected")
        self._sock.sendall(data)

    def recv_exact(self, n: int, timeout: float | None = 5.0) -> bytes:
        if self._sock is None:
            raise OSError("Not connected")
        self._sock.settimeout(timeout)
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise OSError("Connection closed by peer")
            buf.extend(chunk)
        self._sock.settimeout(None)
        return bytes(buf)

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    @property
    def is_connected(self) -> bool:
        return self._sock is not None
