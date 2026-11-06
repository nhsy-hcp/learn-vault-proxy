"""Minimal Vault Proxy client over a UNIX socket, standard library only.

The app never holds a Vault token: Proxy runs with ``use_auto_auth_token = "force"`` and adds its
own. Every request carries ``X-Vault-Request: true`` because the listener sets
``require_request_header = true``.
"""

from __future__ import annotations

import http.client
import json
import logging
import socket
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

from db_app.logging_setup import log_event

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0
VAULT_REQUEST_HEADER = "X-Vault-Request"


class VaultError(Exception):
    """Vault returned an unexpected status, or the socket could not be reached."""

    def __init__(self, path: str, status: int | None, message: str = "") -> None:
        super().__init__(f"vault {path}: status={status} {message}".strip())
        self.path = path
        self.status = status


class VaultDenied(VaultError):
    """Vault returned 403 for this path.

    ``/try`` and ``run_query`` recovery never retry it. The background refresh loop does, with a
    bounded backoff: with ``use_auto_auth_token = "force"`` Proxy forwards requests without a token
    while it has none (startup, re-authentication), so a 403 on the app's own path can be transient.
    """


class UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that connects to a UNIX socket instead of TCP."""

    def __init__(self, socket_path: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


def _quote_path(path: str) -> str:
    path = path.strip().lstrip("/")
    if not path or any(part in ("", ".", "..") for part in path.split("/")):
        raise ValueError(f"invalid vault path: {path!r}")
    return urllib.parse.quote(path, safe="/-_.~")


class VaultClient:
    def __init__(
        self,
        socket_path: str,
        timeout: float = DEFAULT_TIMEOUT,
        connection_factory: Callable[[str, float], http.client.HTTPConnection] | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.timeout = timeout
        self._connection_factory = connection_factory or UnixHTTPConnection

    def _request(
        self, method: str, url: str, body: dict[str, Any] | None = None
    ) -> tuple[int, http.client.HTTPMessage, Any]:
        conn = self._connection_factory(self.socket_path, self.timeout)
        headers = {VAULT_REQUEST_HEADER: "true", "Accept": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, url, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            data: Any = None
            if raw:
                try:
                    data = json.loads(raw)
                except ValueError:
                    data = None
            return resp.status, resp.headers, data
        except http.client.HTTPException as exc:
            # IncompleteRead, BadStatusLine, RemoteDisconnected...: not OSError subclasses. Surface
            # them as the transport error callers already handle.
            raise ConnectionError(f"vault proxy transport error: {type(exc).__name__}") from exc
        finally:
            conn.close()

    def read(self, path: str) -> tuple[int, http.client.HTTPMessage, Any]:
        """``GET /v1/<path>`` -> ``(status, headers, json or None)``.

        Raises OSError on I/O or a malformed response (``ConnectionError``).
        """
        status, headers, body = self._request("GET", f"/v1/{_quote_path(path)}")
        log_event(
            log,
            logging.INFO,
            "vault_read",
            path=path,
            status=status,
            x_cache=headers.get("X-Cache"),
            age=headers.get("Age"),
        )
        return status, headers, body

    def cache_clear(self, path: str) -> int:
        """Evict the Proxy cache entry for ``/v1/<path>``. Returns the HTTP status."""
        value = f"/v1/{_quote_path(path)}"
        status, _, _ = self._request(
            "POST", "/proxy/v1/cache-clear", {"type": "request_path", "value": value}
        )
        log_event(log, logging.INFO, "cache_cleared", path=path, status=status)
        return status

    def revoke_self(self) -> int:
        """``POST /v1/auth/token/revoke-self`` -> HTTP status (204 on success).

        Proxy adds its own auto-auth token, so this revokes the pod's Proxy token and, with it,
        every lease created with it: the dynamic database user is dropped straight away instead of
        living until ``max_ttl``. Allowed by Vault's built-in ``default`` policy. Call it only on
        shutdown, after the pool is closed. Raises OSError if the socket cannot be reached.
        """
        status, _, _ = self._request("POST", "/v1/auth/token/revoke-self")
        level = logging.INFO if 200 <= status < 300 else logging.WARNING
        log_event(log, level, "token_revoked_self", status=status)
        return status

    def wait_for_socket(self, max_wait: float = 30.0, interval: float = 0.5) -> bool:
        """Retry connecting to the socket for up to ``max_wait`` seconds."""
        start = time.monotonic()
        deadline = start + max_wait
        attempt = 0
        while True:
            attempt += 1
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.connect(self.socket_path)
                log_event(
                    log,
                    logging.INFO,
                    "vault_socket_ready",
                    socket=self.socket_path,
                    attempts=attempt,
                    waited_seconds=round(time.monotonic() - start, 1),
                )
                return True
            except OSError as exc:
                log_event(
                    log,
                    logging.INFO,
                    "vault_socket_wait",
                    socket=self.socket_path,
                    attempt=attempt,
                    error=type(exc).__name__,
                    retry_in=interval,
                )
                if time.monotonic() >= deadline:
                    log_event(
                        log,
                        logging.ERROR,
                        "vault_socket_unavailable",
                        socket=self.socket_path,
                        error=type(exc).__name__,
                    )
                    return False
            finally:
                sock.close()
            time.sleep(interval)
