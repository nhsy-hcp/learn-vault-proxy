from __future__ import annotations

import json
import logging
import time

import psycopg
import pytest

from conftest import Connector, PoolFactory, creds_response, truncated_response
from db_app.creds import CredentialManager, RetriesExhausted, classify_db_error
from db_app.vault_client import VaultDenied

PATH = "db/creds/preview-payments"
ROUTE = f"GET /v1/{PATH}"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_manager(vault, **kwargs):
    pools, connector = PoolFactory(), Connector()
    sleeps: list[float] = []
    mgr = CredentialManager(
        vault,
        PATH,
        host="db",
        port=5432,
        dbname="preview",
        pool_factory=pools,
        connector=connector,
        sleep=sleeps.append,
        backoff=lambda attempt: 0.01 * attempt,
        **{"denied_backoff": lambda attempt: 0.02 * attempt, **kwargs},
    )
    return mgr, pools, connector, sleeps


def events(caplog, name):
    return [r for r in caplog.records if r.getMessage() == name]


def test_load_opens_pool_and_whoami_hides_password(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = creds_response("v-user-1", lease_id="db/creds/x/LEASE-abcdef")
    clock = Clock()
    mgr, pools, _, _ = make_manager(vault, clock=clock)

    mgr.load()
    clock.now += 100

    assert mgr.is_healthy()
    info = mgr.whoami()
    assert info["current_user"] == "v-user-1"
    assert info["lease_id_suffix"] == "abcdef"
    assert info["lease_seconds_remaining"] == 500
    assert "pw-v-user-1-secret" not in json.dumps(info)
    assert "pw-v-user-1-secret" not in repr(mgr._creds)
    assert "sslmode=disable" in pools.pools[0].conninfo


def test_vault_403_logs_vault_denied_without_retry(fake_proxy, caplog):
    state, vault = fake_proxy
    state.routes[ROUTE] = (403, {}, {"errors": ["permission denied"]})
    mgr, pools, _, sleeps = make_manager(vault)

    with caplog.at_level(logging.INFO), pytest.raises(VaultDenied):
        mgr.load()

    assert len(state.calls("GET", f"/v1/{PATH}")) == 1
    assert sleeps == []
    assert pools.pools == []
    (rec,) = events(caplog, "vault_denied")
    assert rec.fields == {"path": PATH, "status": 403}
    assert mgr.denied and not mgr.is_healthy()


def stop_after(n: int, waits: list[float]):
    def waiter(delay: float) -> bool:
        waits.append(delay)
        return len(waits) >= n

    return waiter


def test_startup_403_is_retried_with_backoff(fake_proxy, caplog):
    state, vault = fake_proxy
    denied = (403, {}, {"errors": []})
    state.routes[ROUTE] = [denied, denied, creds_response("v-user-1")]
    waits: list[float] = []
    mgr, pools, _, _ = make_manager(vault, waiter=stop_after(3, waits))

    with caplog.at_level(logging.INFO):
        mgr._run()

    # two denied-backoff waits, then the refresh schedule after a successful load
    assert waits == [0.02, 0.04, 60.0]
    assert len(state.calls("GET", f"/v1/{PATH}")) == 3
    assert [p.user for p in pools.pools] == ["v-user-1"]
    assert mgr.is_healthy() and not mgr.denied
    assert len(events(caplog, "vault_denied")) == 2
    assert all(r.levelno == logging.WARNING for r in events(caplog, "vault_denied"))
    assert events(caplog, "db_creds_unavailable") == []


def test_startup_403_then_200_opens_pool_and_thread_stays_alive(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = [(403, {}, {"errors": []}), creds_response("v-user-1")]
    mgr, pools, _, _ = make_manager(vault, denied_backoff=lambda attempt: 0.05)

    mgr.start()
    try:
        deadline = time.monotonic() + 5
        while not mgr.is_healthy() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert mgr.is_healthy()
        assert [p.user for p in pools.pools] == ["v-user-1"]
        assert mgr._thread is not None and mgr._thread.is_alive()
    finally:
        mgr.stop()
    assert not mgr._thread.is_alive()


def test_refresh_403_with_creds_keeps_pool_and_retries_soon(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = [
        creds_response("v-user-1"),
        (403, {}, {"errors": []}),
        creds_response("v-user-1"),
    ]
    waits: list[float] = []
    mgr, pools, _, _ = make_manager(vault, waiter=stop_after(3, waits))

    mgr._run()

    assert waits == [60.0, 0.02, 60.0]
    assert len(pools.pools) == 1 and not pools.pools[0].closed


def test_incomplete_read_does_not_kill_refresh_loop(fake_proxy, caplog):
    state, vault = fake_proxy
    state.routes[ROUTE] = [truncated_response(), creds_response("v-user-1")]
    waits: list[float] = []
    mgr, _, _, _ = make_manager(vault, waiter=stop_after(2, waits))

    with caplog.at_level(logging.INFO):
        mgr._run()

    assert waits == [0.01, 60.0]
    assert mgr.is_healthy()
    (rec,) = events(caplog, "db_creds_refresh_failed")
    assert rec.fields["error"] == "ConnectionError"


def test_unexpected_exception_does_not_kill_refresh_loop(fake_proxy, caplog, monkeypatch):
    state, vault = fake_proxy
    state.routes[ROUTE] = creds_response("v-user-1")
    waits: list[float] = []
    mgr, _, _, _ = make_manager(vault, waiter=stop_after(2, waits))
    real_load = mgr.load
    calls = iter([RuntimeError("boom"), None])

    def flaky_load() -> None:
        exc = next(calls)
        if exc is not None:
            raise exc
        real_load()

    monkeypatch.setattr(mgr, "load", flaky_load)
    with caplog.at_level(logging.INFO):
        mgr._run()

    assert waits == [0.01, 60.0]
    assert mgr.is_healthy()
    (rec,) = events(caplog, "creds_loop_error")
    assert rec.levelno == logging.ERROR and rec.fields["error"] == "RuntimeError"


def test_refresh_delay_capped_at_60_seconds(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = creds_response("v-user-1", lease_duration=600)
    waits: list[float] = []
    mgr, _, _, _ = make_manager(vault, waiter=stop_after(3, waits))
    mgr._run()

    assert waits == [60.0, 60.0, 60.0]
    # initial load + 2 refreshes
    assert len(state.calls("GET", f"/v1/{PATH}")) == 3


def test_refresh_delay_two_thirds_below_cap(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = creds_response("v-user-1", lease_duration=600)
    mgr, _, _, _ = make_manager(vault, max_refresh_seconds=1000)
    mgr.load()
    assert mgr.refresh_delay() == 400.0
    state.routes[ROUTE] = creds_response("v-user-1", lease_duration=30)
    mgr.refresh()
    assert mgr.refresh_delay() == 20.0


def test_refresh_same_lease_keeps_fetched_at(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = creds_response("v-user-1", lease_id="db/creds/x/LEASE-aaaaaa")
    clock = Clock()
    mgr, _, _, _ = make_manager(vault, clock=clock)
    mgr.load()
    first = mgr._creds.fetched_at

    clock.now += 400
    assert mgr.refresh() is False
    assert mgr._creds.fetched_at == first
    assert mgr.lease_info()["lease_seconds_remaining"] == 200

    state.routes[ROUTE] = creds_response("v-user-1", lease_id="db/creds/x/LEASE-bbbbbb")
    clock.now += 100
    mgr.refresh()
    assert mgr._creds.fetched_at == clock.now
    assert mgr.lease_info()["lease_seconds_remaining"] == 600


def test_refresh_delay_has_floor(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = creds_response("v-user-1", lease_duration=3)
    mgr, _, _, _ = make_manager(vault)
    assert mgr.refresh_delay() == 5.0
    mgr.load()
    assert mgr.refresh_delay() == 5.0


def test_refresh_same_user_keeps_pool(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = creds_response("v-user-1")
    mgr, pools, _, _ = make_manager(vault)
    mgr.load()

    assert mgr.refresh() is False
    assert len(pools.pools) == 1
    assert not pools.pools[0].closed


def test_refresh_username_change_swaps_and_closes_old_pool(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = [creds_response("v-user-1"), creds_response("v-user-2")]
    mgr, pools, _, _ = make_manager(vault)
    mgr.load()
    old = mgr._pool

    assert mgr.refresh() is True

    assert len(pools.pools) == 2
    assert old.closed
    assert mgr._pool is pools.pools[1] and not mgr._pool.closed
    assert mgr.whoami()["current_user"] == "v-user-2"


def test_login_failure_on_probe_clears_cache_and_rereads(fake_proxy, caplog):
    state, vault = fake_proxy
    state.routes[ROUTE] = [creds_response("v-revoked"), creds_response("v-fresh")]
    mgr, pools, connector, _ = make_manager(vault)
    connector.failures.append(psycopg.errors.InvalidPassword("password authentication failed"))

    with caplog.at_level(logging.INFO):
        mgr.load()

    (clear,) = state.calls("POST", "/proxy/v1/cache-clear")
    assert clear.body == {"type": "request_path", "value": f"/v1/{PATH}"}
    assert len(state.calls("GET", f"/v1/{PATH}")) == 2
    assert [p.user for p in pools.pools] == ["v-fresh"]
    for rec in caplog.records:
        assert "pw-" not in json.dumps(getattr(rec, "fields", {}))


def test_run_query_28p01_clears_cache_rereads_and_rebuilds(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = [creds_response("v-user-1"), creds_response("v-user-2")]
    mgr, pools, _, sleeps = make_manager(vault)
    mgr.load()
    pools.pools[0].failures.append(psycopg.errors.InvalidPassword("bad"))

    assert mgr.whoami()["current_user"] == "v-user-2"

    assert len(state.calls("POST", "/proxy/v1/cache-clear")) == 1
    assert len(state.calls("GET", f"/v1/{PATH}")) == 2
    assert pools.pools[0].closed
    assert len(sleeps) == 1


def test_run_query_connection_loss_reconnects_then_login_failure_clears_cache(fake_proxy):
    """Revoke: sessions terminated (57P01), then the old user can no longer log in."""
    state, vault = fake_proxy
    state.routes[ROUTE] = [creds_response("v-user-1"), creds_response("v-user-2")]
    mgr, pools, connector, _ = make_manager(vault)
    mgr.load()
    pools.pools[0].failures.append(psycopg.errors.AdminShutdown("terminating connection"))
    connector.failures.append(psycopg.OperationalError('password authentication failed for "x"'))

    assert mgr.whoami()["current_user"] == "v-user-2"
    assert len(state.calls("POST", "/proxy/v1/cache-clear")) == 1


def test_run_query_connection_loss_only_reconnects(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = creds_response("v-user-1")
    mgr, pools, _, _ = make_manager(vault)
    mgr.load()
    pools.pools[0].failures.append(psycopg.errors.AdminShutdown("terminating connection"))

    assert mgr.whoami()["current_user"] == "v-user-1"
    assert state.calls("POST", "/proxy/v1/cache-clear") == []
    assert len(pools.pools) == 2 and pools.pools[0].closed


def test_run_query_gives_up_after_five_attempts(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = creds_response("v-user-1")
    mgr, pools, connector, sleeps = make_manager(vault)
    mgr.load()
    connector.failures.extend(psycopg.OperationalError("connection refused") for _ in range(10))
    pools.pools[0].failures.extend(
        psycopg.OperationalError("server closed the connection") for _ in range(10)
    )

    with pytest.raises(RetriesExhausted):
        mgr.whoami()
    assert len(sleeps) == 4  # 5 attempts, 4 backoff sleeps


def test_run_query_vault_403_during_recovery_is_not_retried(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = [creds_response("v-user-1"), (403, {}, {"errors": []})]
    mgr, pools, _, sleeps = make_manager(vault)
    mgr.load()
    pools.pools[0].failures.append(psycopg.errors.InvalidPassword("bad"))

    with pytest.raises(VaultDenied):
        mgr.whoami()
    assert len(sleeps) == 1
    assert len(state.calls("GET", f"/v1/{PATH}")) == 2


def test_non_recoverable_sql_error_is_raised(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = creds_response("v-user-1")
    mgr, pools, _, sleeps = make_manager(vault)
    mgr.load()
    pools.pools[0].failures.append(psycopg.errors.InsufficientPrivilege("denied"))

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        mgr.whoami()
    assert sleeps == []


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (psycopg.errors.InvalidPassword("x"), "login"),
        (psycopg.errors.InvalidAuthorizationSpecification("x"), "login"),
        (psycopg.OperationalError('FATAL: password authentication failed for user "u"'), "login"),
        (psycopg.OperationalError('FATAL: role "v-x" does not exist'), "login"),
        (psycopg.errors.AdminShutdown("x"), "connection_lost"),
        (psycopg.errors.CrashShutdown("x"), "connection_lost"),
        (psycopg.errors.CannotConnectNow("x"), "connection_lost"),
        (psycopg.OperationalError("server closed the connection unexpectedly"), "connection_lost"),
        (psycopg.errors.UndefinedTable("x"), None),
        (ValueError("x"), None),
    ],
)
def test_classify_db_error(exc, kind):
    assert classify_db_error(exc) == kind
