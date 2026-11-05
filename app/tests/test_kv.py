from __future__ import annotations

import hashlib
import json
import logging

from conftest import kv_response, truncated_response
from db_app.kv import KVWatcher, fingerprint

PATH = "kvv2/data/preview/payments/api-key"
ROUTE = f"GET /v1/{PATH}"
KEY = "test-only-fake-api-key"


class Clock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


def make_watcher(vault, clock=None):
    return KVWatcher(vault, PATH, 30, clock=clock or Clock(), backoff=lambda n: float(n))


def test_fingerprint_is_first_12_hex_of_sha256():
    assert fingerprint(KEY) == hashlib.sha256(KEY.encode()).hexdigest()[:12]
    assert len(fingerprint(KEY)) == 12


def test_first_read_reports_vault_source(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = kv_response(KEY, 3, {"X-Cache": "MISS"})
    clock = Clock()
    kv = make_watcher(vault, clock)

    assert kv.poll_once()
    clock.now += 12

    status = kv.status()
    assert status["version"] == 3
    assert status["fingerprint"] == fingerprint(KEY)
    assert status["source"] == "vault"
    assert status["x_cache"] == "MISS"
    assert status["stale"] is False
    assert status["seconds_since_refresh"] == 12.0
    assert KEY not in json.dumps(status)
    assert state.calls("GET", f"/v1/{PATH}")[0].headers["X-Vault-Request"] == "true"


def test_x_cache_hit_reports_proxy_cache(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = kv_response(KEY, 1, {"X-Cache": "HIT", "Age": "42"})
    kv = make_watcher(vault)

    assert kv.poll_once()

    status = kv.status()
    assert status["source"] == "proxy-cache"
    assert status["age"] == "42"
    assert status["stale"] is False


def test_failure_after_success_serves_app_memory_and_is_stale(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = [kv_response(KEY, 2), (503, {}, {"errors": ["Vault is sealed"]})]
    clock = Clock()
    kv = make_watcher(vault, clock)
    assert kv.poll_once()
    clock.now += 90

    assert not kv.poll_once()

    status = kv.status()
    assert status["stale"] is True
    assert status["source"] == "app-memory"
    assert status["version"] == 2
    assert status["fingerprint"] == fingerprint(KEY)
    assert status["last_status"] == 503
    assert status["seconds_since_refresh"] == 90.0
    assert kv.has_value


def test_failure_backs_off_then_recovers(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = [
        kv_response(KEY, 1),
        (503, {}, None),
        (503, {}, None),
        kv_response("new-key", 2),
    ]
    kv = make_watcher(vault)
    delays = []
    for _ in range(4):
        kv.poll_once()
        delays.append(kv.next_delay())

    assert delays == [30, 1.0, 2.0, 30]
    status = kv.status()
    assert status["stale"] is False
    assert status["version"] == 2
    assert status["fingerprint"] == fingerprint("new-key")


def test_failure_without_value_has_no_source(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = (503, {}, None)
    kv = make_watcher(vault)

    assert not kv.poll_once()

    status = kv.status()
    assert status["stale"] is True
    assert status["source"] is None
    assert status["fingerprint"] is None
    assert not kv.has_value


def test_kv_403_logs_vault_denied(fake_proxy, caplog):
    state, vault = fake_proxy
    state.routes[ROUTE] = (403, {}, {"errors": ["permission denied"]})
    kv = make_watcher(vault)

    assert not kv.poll_once()

    denied = [r for r in caplog.records if r.getMessage() == "vault_denied"]
    assert denied and denied[0].fields["path"] == PATH


def test_socket_unreachable_is_a_failure(socket_dir):
    from db_app.vault_client import VaultClient

    kv = make_watcher(VaultClient(str(socket_dir / "missing.sock"), timeout=0.2))
    assert not kv.poll_once()
    assert kv.status()["stale"] is True


def run_watcher(vault, n: int):
    waits: list[float] = []

    def waiter(delay: float) -> bool:
        waits.append(delay)
        return len(waits) >= n

    kv = KVWatcher(vault, PATH, 30, clock=Clock(), backoff=lambda n: float(n), waiter=waiter)
    return kv, waits


def test_incomplete_read_does_not_kill_watcher(fake_proxy):
    state, vault = fake_proxy
    state.routes[ROUTE] = [truncated_response(), kv_response(KEY, 4)]
    kv, waits = run_watcher(vault, 2)

    kv._run()

    assert waits == [1.0, 30]
    assert kv.has_value and kv.status()["version"] == 4
    assert len(state.calls("GET", f"/v1/{PATH}")) == 2


def test_403_keeps_polling_on_backoff(fake_proxy):
    state, vault = fake_proxy
    denied = (403, {}, {"errors": []})
    state.routes[ROUTE] = [denied, denied, kv_response(KEY, 1)]
    kv, waits = run_watcher(vault, 3)

    kv._run()

    assert waits == [1.0, 2.0, 30]
    assert kv.has_value and kv.status()["stale"] is False


def test_unexpected_exception_does_not_kill_watcher(fake_proxy, caplog, monkeypatch):
    state, vault = fake_proxy
    state.routes[ROUTE] = kv_response(KEY, 1)
    kv, waits = run_watcher(vault, 2)
    real_poll = kv.poll_once
    calls = iter([True, False])

    def flaky_poll() -> bool:
        if next(calls):
            raise RuntimeError("boom")
        return real_poll()

    monkeypatch.setattr(kv, "poll_once", flaky_poll)
    with caplog.at_level(logging.INFO):
        kv._run()

    assert waits == [1.0, 30]
    assert kv.has_value
    errors = [r for r in caplog.records if r.getMessage() == "kv_loop_error"]
    assert len(errors) == 1 and errors[0].fields["error"] == "RuntimeError"
