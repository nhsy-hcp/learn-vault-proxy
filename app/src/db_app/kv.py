"""KV v2 API key watcher.

Polls ``API_KEY_PATH`` every ``KV_REFRESH_SECONDS`` and keeps the last good value in memory. On
failure (including a ``403`` while Proxy has no token yet) it marks the value ``stale`` and keeps
polling with backoff. It reports metadata only: the value itself is never returned or logged, just
a SHA-256 fingerprint (first 12 hex characters).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from db_app.backoff import backoff_delay
from db_app.logging_setup import log_event
from db_app.vault_client import VaultClient

log = logging.getLogger(__name__)

SOURCE_VAULT = "vault"
SOURCE_PROXY_CACHE = "proxy-cache"
SOURCE_APP_MEMORY = "app-memory"


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


class KVWatcher:
    def __init__(
        self,
        vault: VaultClient,
        path: str,
        refresh_seconds: float = 30.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        backoff: Callable[[int], float] | None = None,
        waiter: Callable[[float], bool] | None = None,
    ) -> None:
        self.vault = vault
        self.path = path
        self.refresh_seconds = refresh_seconds
        self._clock = clock
        self._backoff = backoff or (
            lambda attempt: backoff_delay(attempt, base=1.0, cap=max(1.0, refresh_seconds))
        )
        self._stop = threading.Event()
        self._waiter = waiter or self._stop.wait
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self._api_key: str | None = None  # last good value: memory only, never exposed
        self.version: int | None = None
        self.fingerprint: str | None = None
        self.source: str | None = None
        self.x_cache: str | None = None
        self.age: str | None = None
        self.stale = False
        self.last_status: int | None = None
        self.last_error: str | None = None
        self.failures = 0
        self._last_refresh: float | None = None

    @property
    def has_value(self) -> bool:
        return self._api_key is not None

    def poll_once(self) -> bool:
        """Read the secret once. Returns True on success."""
        try:
            status, headers, body = self.vault.read(self.path)
        except (OSError, ValueError) as exc:
            self._mark_failed(None, type(exc).__name__)
            return False

        with self._lock:
            self.last_status = status
            self.x_cache = headers.get("X-Cache") if headers is not None else None
            self.age = headers.get("Age") if headers is not None else None

        if status == 403:
            log_event(log, logging.WARNING, "vault_denied", path=self.path, status=status)
            self._mark_failed(status, "denied")
            return False

        data = body.get("data") if status == 200 and isinstance(body, dict) else None
        inner = data.get("data") if isinstance(data, dict) else None
        api_key = inner.get("api_key") if isinstance(inner, dict) else None
        if not isinstance(api_key, str) or not api_key:
            self._mark_failed(status, "bad_response")
            return False

        metadata = data.get("metadata") or {}
        with self._lock:
            self._api_key = api_key
            self.version = metadata.get("version")
            self.fingerprint = fingerprint(api_key)
            self.source = (
                SOURCE_PROXY_CACHE if (self.x_cache or "").upper() == "HIT" else SOURCE_VAULT
            )
            self.stale = False
            self.failures = 0
            self.last_error = None
            self._last_refresh = self._clock()
        log_event(
            log,
            logging.INFO,
            "kv_refreshed",
            path=self.path,
            version=self.version,
            fingerprint=self.fingerprint,
            source=self.source,
            x_cache=self.x_cache,
            age=self.age,
        )
        return True

    def _mark_failed(self, status: int | None, reason: str) -> None:
        with self._lock:
            self.stale = True
            self.failures += 1
            self.last_status = status
            self.last_error = reason
            self.source = SOURCE_APP_MEMORY if self._api_key is not None else None
        log_event(
            log,
            logging.WARNING,
            "kv_refresh_failed",
            path=self.path,
            status=status,
            reason=reason,
            attempt=self.failures,
        )
        log_event(
            log,
            logging.WARNING,
            "kv_stale",
            path=self.path,
            serving=self.source,
            version=self.version,
            fingerprint=self.fingerprint,
        )

    def status(self) -> dict[str, Any]:
        with self._lock:
            since = None if self._last_refresh is None else self._clock() - self._last_refresh
            return {
                "path": self.path,
                "version": self.version,
                "fingerprint": self.fingerprint,
                "source": self.source,
                "x_cache": self.x_cache,
                "age": self.age,
                "stale": self.stale,
                "seconds_since_refresh": None if since is None else round(since, 1),
                "last_status": self.last_status,
            }

    def next_delay(self) -> float:
        return self.refresh_seconds if self.failures == 0 else self._backoff(self.failures)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="kv-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # the watcher must never die: log, back off, keep polling
                with self._lock:
                    self.stale = True
                    self.failures += 1
                    self.last_error = "unexpected_error"
                log_event(
                    log,
                    logging.ERROR,
                    "kv_loop_error",
                    path=self.path,
                    error=type(exc).__name__,
                    attempt=self.failures,
                )
            delay = self.next_delay()
            if self.failures:
                log_event(
                    log,
                    logging.INFO,
                    "retry",
                    component="kv",
                    attempt=self.failures + 1,
                    delay=round(delay, 2),
                )
            if self._waiter(delay):
                return
