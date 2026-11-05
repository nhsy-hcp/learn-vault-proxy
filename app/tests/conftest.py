"""Test fixtures: a fake Vault Proxy on a UNIX socket and fake connection pools."""

from __future__ import annotations

import json
import shutil
import socketserver
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from db_app.vault_client import VaultClient

Route = tuple[int, dict[str, str], Any]


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class FakeProxyState:
    routes: dict[str, Route | Callable[[], Route] | list[Route]] = field(default_factory=dict)
    requests: list[Recorded] = field(default_factory=list)

    def calls(self, method: str, path: str) -> list[Recorded]:
        return [r for r in self.requests if r.method == method and r.path == path]


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def _make_handler(state: FakeProxyState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            state.requests.append(Recorded(self.command, self.path, dict(self.headers), body))
            if self.headers.get("X-Vault-Request") != "true":
                self._reply(412, {}, {"errors": ["missing X-Vault-Request header"]})
                return
            key = f"{self.command} {self.path}"
            route = state.routes.get(key)
            if route is None:
                if self.command == "POST" and self.path == "/proxy/v1/cache-clear":
                    route = (200, {}, None)
                else:
                    route = (404, {}, {"errors": []})
            if isinstance(route, list):
                route = route.pop(0) if len(route) > 1 else route[0]
            if callable(route):
                route = route()
            self._reply(*route)

        do_GET = _handle
        do_POST = _handle

        def _reply(self, status: int, headers: dict[str, str], body: Any) -> None:
            payload = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            # A route may set its own Content-Length to simulate a truncated body (IncompleteRead).
            if "Content-Length" not in headers:
                self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def address_string(self) -> str:
            return "unix"

    return Handler


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    # Short path: AF_UNIX paths are limited to ~104 bytes on macOS.
    path = Path(tempfile.mkdtemp(prefix="vp-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def fake_proxy(socket_dir: Path) -> Iterator[tuple[FakeProxyState, VaultClient]]:
    state = FakeProxyState()
    sock = str(socket_dir / "proxy.sock")
    server = _UnixHTTPServer(sock, _make_handler(state))
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield state, VaultClient(sock, timeout=2.0)
    finally:
        server.shutdown()
        server.server_close()


def creds_response(
    username: str,
    lease_id: str = "db/creds/preview-payments/abc123XYZ789",
    lease_duration: int = 600,
) -> Route:
    return (
        200,
        {},
        {
            "lease_id": lease_id,
            "lease_duration": lease_duration,
            "renewable": True,
            "data": {"username": username, "password": f"pw-{username}-secret"},
        },
    )


def truncated_response() -> Route:
    """Promise more bytes than are sent: the client sees ``http.client.IncompleteRead``."""
    return (200, {"Content-Length": "4096"}, {"data": {}})


def kv_response(api_key: str, version: int, headers: dict[str, str] | None = None) -> Route:
    return (
        200,
        headers or {},
        {
            "data": {
                "data": {"api_key": api_key, "issuer": "demo-partner-api"},
                "metadata": {"version": version},
            }
        },
    )


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def execute(self, query: Any) -> FakeCursor:
        self.conn.executed.append(query)
        return self

    def fetchone(self) -> tuple[str]:
        return (self.conn.user,)

    def fetchall(self) -> list[dict[str, Any]]:
        return [{"id": 1, "name": "widget"}]

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        pass


class FakeConn:
    def __init__(self, user: str) -> None:
        self.user = user
        self.executed: list[Any] = []

    def execute(self, query: Any) -> FakeCursor:
        return FakeCursor(self).execute(query)

    def cursor(self, row_factory: Any = None) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        pass


class FakePool:
    def __init__(self, conninfo: str) -> None:
        self.conninfo = conninfo
        self.closed = False
        self.user = dict(p.split("=", 1) for p in conninfo.split())["user"]
        # Exceptions to raise from successive connection() calls before succeeding.
        self.failures: list[BaseException] = []

    @contextmanager
    def connection(self) -> Iterator[FakeConn]:
        if self.failures:
            raise self.failures.pop(0)
        yield FakeConn(self.user)

    def close(self) -> None:
        self.closed = True


class PoolFactory:
    def __init__(self) -> None:
        self.pools: list[FakePool] = []

    def __call__(self, conninfo: str) -> FakePool:
        pool = FakePool(conninfo)
        self.pools.append(pool)
        return pool


class Connector:
    """Probe-connection factory; raise queued errors, else succeed."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failures: list[BaseException] = []

    def __call__(self, conninfo: str) -> FakeConn:
        self.calls.append(conninfo)
        if self.failures:
            raise self.failures.pop(0)
        return FakeConn("probe")
