#!/usr/bin/env python3
"""极简 SOCKS5 代理（只做 CONNECT，直连出网）

放在 WSL / 本地机器上跑，作为反向隧道的末端：远端服务器上的爬虫连到隧道端口后，
流量就从这个进程所在机器出网（出口 IP 即本机所在宽带的 IP）。

特性：
  - 只实现 SOCKS5 的 CONNECT，目标支持 IPv4 / IPv6 / 域名
  - 无认证，默认只监听 127.0.0.1（够隧道用，不会暴露到局域网）
  - 纯标准库，Python 3.7+ 直接跑

用法：
    python3 socks5.py                  # 监听 127.0.0.1:1081
    python3 socks5.py --port 1081
"""

import argparse
import select
import socket
import struct
import threading

BUFSIZE = 65536
IDLE_TIMEOUT = 120  # 空闲多久断开（秒）


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("对端提前关闭")
        buf += chunk
    return buf


def reply(sock: socket.socket, code: int) -> None:
    sock.sendall(b"\x05" + bytes([code]) + b"\x00\x01" + b"\x00" * 6)


def handle(client: socket.socket) -> None:
    remote = None
    try:
        client.settimeout(IDLE_TIMEOUT)
        ver, nmethods = recv_exact(client, 2)
        if ver != 5:
            return
        methods = recv_exact(client, nmethods)
        if 0x00 not in methods:
            client.sendall(b"\x05\xff")
            return
        client.sendall(b"\x05\x00")

        ver, cmd, _, atyp = recv_exact(client, 4)
        if cmd != 0x01:
            reply(client, 0x07)  # 只支持 CONNECT
            return
        if atyp == 0x01:
            host = socket.inet_ntoa(recv_exact(client, 4))
        elif atyp == 0x03:
            host = recv_exact(client, recv_exact(client, 1)[0]).decode()
        elif atyp == 0x04:
            host = socket.inet_ntop(socket.AF_INET6, recv_exact(client, 16))
        else:
            reply(client, 0x08)
            return
        port = struct.unpack("!H", recv_exact(client, 2))[0]

        remote = socket.create_connection((host, port), timeout=20)
        remote.settimeout(IDLE_TIMEOUT)
        reply(client, 0x00)

        pair = [client, remote]
        while True:
            readable, _, _ = select.select(pair, [], [], IDLE_TIMEOUT)
            if not readable:
                break
            for sock in readable:
                data = sock.recv(BUFSIZE)
                if not data:
                    return
                (remote if sock is client else client).sendall(data)
    except Exception:
        pass
    finally:
        for sock in (client, remote):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="极简 SOCKS5 代理（CONNECT 直连）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=1081, help="监听端口（默认 1081）")
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(128)
    print(f"[socks5] 监听 {args.host}:{args.port}，直连出网", flush=True)

    while True:
        try:
            client, _ = server.accept()
        except KeyboardInterrupt:
            break
        threading.Thread(target=handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
