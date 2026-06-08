from __future__ import annotations

import socket
import time
from typing import Any


class RedisWireError(RuntimeError):
    pass


def _encode(args: tuple[Any, ...]) -> bytes:
    chunks: list[bytes] = [f"*{len(args)}\r\n".encode("ascii")]
    for arg in args:
        data = str(arg).encode("utf-8")
        chunks.append(f"${len(data)}\r\n".encode("ascii"))
        chunks.append(data + b"\r\n")
    return b"".join(chunks)


def _read_line(sock_file) -> bytes:
    line = sock_file.readline()
    if not line:
        raise RedisWireError("redis closed the connection")
    if not line.endswith(b"\r\n"):
        raise RedisWireError(f"malformed redis line: {line!r}")
    return line[:-2]


def _parse(sock_file):
    prefix = sock_file.read(1)
    if not prefix:
        raise RedisWireError("redis returned an empty response")
    if prefix == b"+":
        return _read_line(sock_file).decode("utf-8")
    if prefix == b"-":
        raise RedisWireError(_read_line(sock_file).decode("utf-8", "replace"))
    if prefix == b":":
        return int(_read_line(sock_file))
    if prefix == b"$":
        length = int(_read_line(sock_file))
        if length < 0:
            return None
        data = sock_file.read(length)
        terminator = sock_file.read(2)
        if terminator != b"\r\n":
            raise RedisWireError("malformed redis bulk string")
        return data.decode("utf-8")
    if prefix == b"*":
        length = int(_read_line(sock_file))
        if length < 0:
            return None
        return [_parse(sock_file) for _ in range(length)]
    raise RedisWireError(f"unknown redis response prefix: {prefix!r}")


def command(port: int, *args: Any, host: str = "127.0.0.1", timeout: float = 2.0):
    with socket.create_connection((host, int(port)), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(_encode(args))
        with sock.makefile("rb") as sock_file:
            return _parse(sock_file)


def ping(port: int) -> bool:
    try:
        return command(port, "PING") == "PONG"
    except OSError:
        return False
    except RedisWireError:
        return False


def wait_until_ready(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ping(port):
            return
        time.sleep(0.05)
    raise RedisWireError(f"redis on port {port} did not become ready")


def get(port: int, key: str) -> str | None:
    return command(port, "GET", key)


def set(port: int, key: str, value: str) -> None:
    command(port, "SET", key, value)


def flushdb(port: int) -> None:
    command(port, "FLUSHDB")


def shutdown(port: int) -> None:
    try:
        command(port, "SHUTDOWN", "NOSAVE", timeout=0.5)
    except Exception:
        pass
