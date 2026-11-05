"""Dynamic PostgreSQL credentials from Vault Proxy, with refresh, pool swap and recovery.

* Reads ``DB_CREDS_PATH`` through the socket and opens a small connection pool.
* Refreshes at 2/3 of ``lease_duration``, capped at ``max_refresh_seconds`` (default 60 s,
  ``CREDS_REFRESH_MAX_SECONDS``) so a rotation at the role's ``max_ttl`` is noticed quickly. Proxy
  serves cached reads with the original body, so an unchanged ``lease_id`` keeps its original
  ``fetched_at``. If the username changed, a new pool is built, swapped in atomically, and the old
  one is closed.
* :meth:`CredentialManager.run_query` recovers from:

  - connection loss (``OperationalError``, SQLSTATE 57P01/57P02/57P03, e.g. when Vault terminates
    sessions on revoke): rebuild the pool with the current credentials;
  - login failure (SQLSTATE 28P01/28000): ``cache-clear`` the path in Proxy, re-read, rebuild.

  Retries use jittered exponential backoff, at most ``max_attempts`` (5) attempts.
* A Vault ``403`` logs ``vault_denied``. ``run_query`` recovery never retries it. The background
  refresh loop does, with a bounded backoff (5 s growing to 30 s), because Proxy forwards requests
  without a token while it has none (startup, re-authentication). The loop never exits on error.

The password is held only in memory, is excluded from ``repr`` and is never logged or returned.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from db_app.backoff import backoff_delay
from db_app.logging_setup import log_event
from db_app.vault_client import VaultClient, VaultDenied, VaultError

log = logging.getLogger(__name__)

T = TypeVar("T")

SQLSTATE_CONNECTION_LOST = frozenset({"57P01", "57P02", "57P03"})
SQLSTATE_LOGIN_FAILED = frozenset({"28P01", "28000"})

DEFAULT_MAX_REFRESH_SECONDS = 60.0  # refresh cap; env CREDS_REFRESH_MAX_SECONDS
DENIED_BACKOFF_BASE_SECONDS = 5.0
DENIED_BACKOFF_CAP_SECONDS = 30.0

LOGIN = "login"
CONNECTION_LOST = "connection_lost"

PoolFactory = Callable[[str], Any]
Connector = Callable[[str], Any]


@dataclass(frozen=True)
class DbCreds:
    username: str
    password: str = field(repr=False)
    lease_id: str
    lease_duration: int
    fetched_at: float

    @property
    def lease_id_suffix(self) -> str | None:
        return self.lease_id[-6:] if self.lease_id else None


class LoginFailed(Exception):
    """PostgreSQL rejected the credentials (28P01 / 28000)."""


class NoPool(Exception):
    """No connection pool is open yet."""


class RetriesExhausted(Exception):
    """``run_query`` gave up after ``max_attempts``."""


def classify_db_error(exc: BaseException) -> str | None:
    """Return ``LOGIN``, ``CONNECTION_LOST`` or ``None`` (not recoverable by reconnecting)."""
    if isinstance(exc, LoginFailed):
        return LOGIN
    if isinstance(exc, NoPool):
        return CONNECTION_LOST
    if not isinstance(exc, psycopg.Error):
        return None
    state = getattr(exc, "sqlstate", None)
    if state in SQLSTATE_LOGIN_FAILED:
        return LOGIN
    if state in SQLSTATE_CONNECTION_LOST:
        return CONNECTION_LOST
    if isinstance(exc, psycopg.OperationalError):
        # libpq connection failures carry no SQLSTATE: fall back to the server message.
        message = str(exc).lower()
        if "authentication failed" in message or (
            "role" in message and "does not exist" in message
        ):
            return LOGIN
        return CONNECTION_LOST
    if isinstance(exc, psycopg.InterfaceError):
        return CONNECTION_LOST
    return None


def default_pool_factory(conninfo: str) -> ConnectionPool:
    return ConnectionPool(
        conninfo,
        min_size=1,
        max_size=4,
        timeout=5.0,
        open=True,
        check=ConnectionPool.check_connection,
        kwargs={"autocommit": True},
        name="db-app",
    )


def default_connector(conninfo: str) -> psycopg.Connection:
    return psycopg.connect(conninfo, connect_timeout=5)


def denied_backoff_delay(attempt: int) -> float:
    return backoff_delay(attempt, base=DENIED_BACKOFF_BASE_SECONDS, cap=DENIED_BACKOFF_CAP_SECONDS)


class CredentialManager:
    def __init__(
        self,
        vault: VaultClient,
        creds_path: str,
        *,
        host: str,
        port: int,
        dbname: str,
        pool_factory: PoolFactory = default_pool_factory,
        connector: Connector = default_connector,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        backoff: Callable[[int], float] = backoff_delay,
        denied_backoff: Callable[[int], float] = denied_backoff_delay,
        waiter: Callable[[float], bool] | None = None,
        max_attempts: int = 5,
        min_refresh_seconds: float = 5.0,
        max_refresh_seconds: float = DEFAULT_MAX_REFRESH_SECONDS,
    ) -> None:
        self.vault = vault
        self.creds_path = creds_path
        self._host, self._port, self._dbname = host, port, dbname
        self._pool_factory = pool_factory
        self._connector = connector
        self._clock = clock
        self._sleep = sleep
        self._backoff = backoff
        self._denied_backoff = denied_backoff
        self.max_attempts = max_attempts
        self.min_refresh_seconds = min_refresh_seconds
        self.max_refresh_seconds = max_refresh_seconds

        self._lock = threading.Lock()
        self._recover_lock = threading.Lock()
        self._creds: DbCreds | None = None
        self._pool: Any = None
        self._stop = threading.Event()
        self._waiter = waiter or self._stop.wait
        self._thread: threading.Thread | None = None
        self.denied = False

    # ------------------------------------------------------------------ Vault
    def fetch_creds(self) -> DbCreds:
        status, _, body = self.vault.read(self.creds_path)
        if status == 403:
            self.denied = True
            log_event(log, logging.WARNING, "vault_denied", path=self.creds_path, status=status)
            raise VaultDenied(self.creds_path, status)
        if status != 200 or not isinstance(body, dict):
            raise VaultError(self.creds_path, status)
        data = body.get("data") or {}
        username, password = data.get("username"), data.get("password")
        if not username or not password:
            raise VaultError(self.creds_path, status, "response has no username/password")
        self.denied = False
        creds = DbCreds(
            username=username,
            password=password,
            lease_id=body.get("lease_id") or "",
            lease_duration=int(body.get("lease_duration") or 0),
            fetched_at=self._clock(),
        )
        log_event(
            log,
            logging.INFO,
            "creds_acquired",
            path=self.creds_path,
            username=creds.username,
            lease_id_suffix=creds.lease_id_suffix,
            lease_duration=creds.lease_duration,
        )
        return creds

    # ------------------------------------------------------------------- pool
    def _conninfo(self, creds: DbCreds) -> str:
        return make_conninfo(
            host=self._host,
            port=self._port,
            dbname=self._dbname,
            user=creds.username,
            password=creds.password,
            sslmode="disable",
            application_name="db-app",
        )

    def _open_pool(self, creds: DbCreds) -> Any:
        """Probe a single login (so 28P01 surfaces as LoginFailed), then build the pool."""
        conninfo = self._conninfo(creds)
        try:
            self._connector(conninfo).close()
        except psycopg.Error as exc:
            if classify_db_error(exc) == LOGIN:
                log_event(
                    log,
                    logging.WARNING,
                    "db_login_failed",
                    username=creds.username,
                    sqlstate=getattr(exc, "sqlstate", None),
                )
                raise LoginFailed(creds.username) from exc
            raise
        return self._pool_factory(conninfo)

    def _install(self, creds: DbCreds, pool: Any) -> None:
        with self._lock:
            old_pool, old_creds = self._pool, self._creds
            self._pool, self._creds = pool, creds
        if old_pool is not None and old_pool is not pool:
            self._close_pool(old_pool)
            log_event(
                log,
                logging.INFO,
                "pool_swapped",
                old_user=old_creds.username if old_creds else None,
                new_user=creds.username,
                lease_id_suffix=creds.lease_id_suffix,
            )
        else:
            log_event(
                log,
                logging.INFO,
                "pool_opened",
                username=creds.username,
                lease_id_suffix=creds.lease_id_suffix,
            )

    @staticmethod
    def _close_pool(pool: Any) -> None:
        try:
            pool.close()
        except Exception as exc:  # closing an old pool must never break the swap
            log_event(log, logging.WARNING, "db_pool_close_failed", error=type(exc).__name__)

    def _establish(self, creds: DbCreds) -> None:
        try:
            pool = self._open_pool(creds)
        except LoginFailed:
            self._recover_login()
            return
        self._install(creds, pool)

    def _recover_login(self) -> None:
        """Proxy may hold a lease Vault already revoked: evict it, re-read, rebuild."""
        self.vault.cache_clear(self.creds_path)
        creds = self.fetch_creds()
        self._install(creds, self._open_pool(creds))

    def _reconnect(self) -> None:
        creds = self._creds
        if creds is None:
            self.load()
        else:
            self._establish(creds)

    # ----------------------------------------------------------------- public
    def load(self) -> None:
        """Read credentials and open the pool."""
        self._establish(self.fetch_creds())

    def refresh(self) -> bool:
        """Re-read credentials; rebuild and swap the pool if the username changed."""
        new = self.fetch_creds()
        with self._lock:
            current, pool = self._creds, self._pool
        if current is not None and pool is not None and new.username == current.username:
            if new.lease_id and new.lease_id == current.lease_id:
                # Same lease (Proxy cache): keep the original fetch time so remaining TTL is real.
                new = replace(new, fetched_at=current.fetched_at)
            with self._lock:
                self._creds = new
            log_event(
                log,
                logging.INFO,
                "creds_unchanged",
                username=new.username,
                lease_id_suffix=new.lease_id_suffix,
                lease_duration=new.lease_duration,
            )
            return False
        log_event(
            log,
            logging.INFO,
            "creds_rotated",
            old_user=current.username if current else None,
            new_user=new.username,
            lease_id_suffix=new.lease_id_suffix,
        )
        self._establish(new)
        return True

    def refresh_delay(self) -> float:
        creds = self._creds
        if creds is None or creds.lease_duration <= 0:
            return self.min_refresh_seconds
        return max(
            self.min_refresh_seconds, min(creds.lease_duration * 2 / 3, self.max_refresh_seconds)
        )

    def is_healthy(self) -> bool:
        pool = self._pool
        return pool is not None and not getattr(pool, "closed", False)

    def lease_info(self) -> dict[str, Any]:
        creds = self._creds
        if creds is None:
            return {"lease_id_suffix": None, "lease_seconds_remaining": None}
        remaining = creds.fetched_at + creds.lease_duration - self._clock()
        return {
            "lease_id_suffix": creds.lease_id_suffix,
            "lease_seconds_remaining": max(0, int(remaining)),
        }

    def run_query(self, fn: Callable[[Any], T]) -> T:
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            pool = self._pool
            try:
                if pool is None:
                    raise NoPool
                with pool.connection() as conn:
                    return fn(conn)
            except (psycopg.Error, NoPool) as exc:
                kind = classify_db_error(exc)
                if kind is None:
                    raise
                last_exc = exc
                event = "db_login_failed" if kind == LOGIN else "db_connection_lost"
                log_event(
                    log,
                    logging.WARNING,
                    event,
                    sqlstate=getattr(exc, "sqlstate", None),
                    error=type(exc).__name__,
                    attempt=attempt,
                )
            if attempt == self.max_attempts:
                log_event(log, logging.ERROR, "retries_exhausted", attempts=attempt)
                break
            delay = self._backoff(attempt)
            log_event(
                log,
                logging.INFO,
                "retry",
                component="db",
                attempt=attempt + 1,
                max_attempts=self.max_attempts,
                delay=round(delay, 2),
                reason=kind,
            )
            self._sleep(delay)
            try:
                self._recover(kind, pool)
            except VaultDenied:
                raise
            except (LoginFailed, psycopg.Error, VaultError, OSError) as exc:
                last_exc = exc
                log_event(log, logging.WARNING, "db_recover_failed", error=type(exc).__name__)
        raise RetriesExhausted(f"gave up after {self.max_attempts} attempts") from last_exc

    def _recover(self, kind: str, failed_pool: Any) -> None:
        with self._recover_lock:
            if self._pool is not failed_pool:
                return  # another thread already replaced it
            if kind == LOGIN:
                self._recover_login()
            else:
                self._reconnect()

    def whoami(self) -> dict[str, Any]:
        user = self.run_query(lambda conn: conn.execute("SELECT current_user").fetchone()[0])
        return {"current_user": user, "creds_path": self.creds_path, **self.lease_info()}

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="creds-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            self._close_pool(pool)

    def _run(self) -> None:
        """Load, then refresh on schedule. Never exits except on ``stop()``."""
        failures = 0
        while not self._stop.is_set():
            if self._creds is not None and failures == 0:
                delay = self.refresh_delay()
                log_event(log, logging.INFO, "refresh_scheduled", seconds=round(delay, 1))
                if self._waiter(delay):
                    return
            component = "creds_load" if self._creds is None else "creds_refresh"
            try:
                if self._creds is None:
                    self.load()
                else:
                    self.refresh()
                failures = 0
                continue
            except VaultDenied:
                # vault_denied is already logged by fetch_creds. Proxy may have no token yet:
                # retry with a bounded backoff, keeping any current pool meanwhile.
                failures += 1
                delay = self._denied_backoff(failures)
                reason = "vault_denied"
            except (LoginFailed, VaultError, psycopg.Error, OSError) as exc:
                failures += 1
                log_event(
                    log,
                    logging.WARNING,
                    "db_creds_refresh_failed",
                    path=self.creds_path,
                    attempt=failures,
                    error=type(exc).__name__,
                )
                delay = self._backoff(failures)
                reason = "error"
            except Exception as exc:  # the refresh thread must never die
                failures += 1
                log_event(
                    log,
                    logging.ERROR,
                    "creds_loop_error",
                    path=self.creds_path,
                    attempt=failures,
                    error=type(exc).__name__,
                )
                delay = self._backoff(failures)
                reason = "unexpected_error"
            log_event(
                log,
                logging.INFO,
                "retry",
                component=component,
                attempt=failures + 1,
                delay=round(delay, 2),
                reason=reason,
            )
            if self._waiter(delay):
                return
