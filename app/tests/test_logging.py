from __future__ import annotations

import json
import logging
from contextlib import contextmanager

import psycopg

from conftest import Connector, PoolFactory, creds_response, kv_response
from db_app.creds import CredentialManager
from db_app.kv import KVWatcher
from db_app.logging_setup import configure_logging

CREDS = "db/creds/preview-payments"
KVPATH = "kvv2/data/preview/payments/api-key"
API_KEY = "deadbeefcafe0123456789abcdef0123456789abcdef0123"


@contextmanager
def json_stdout_logging():
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    configure_logging("INFO")  # binds to the sys.stdout capsys installed for this phase
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_logs_go_to_stdout_as_json_without_secrets(fake_proxy, capsys):
    state, vault = fake_proxy
    state.routes[f"GET /v1/{CREDS}"] = [creds_response("v-user-1"), creds_response("v-user-2")]
    state.routes[f"GET /v1/{KVPATH}"] = [
        kv_response(API_KEY, 1, {"X-Cache": "HIT", "Age": "3"}),
        (503, {}, None),
    ]
    connector = Connector()
    mgr = CredentialManager(
        vault,
        CREDS,
        host="db",
        port=5432,
        dbname="preview",
        pool_factory=PoolFactory(),
        connector=connector,
        sleep=lambda _: None,
        backoff=lambda _: 0.0,
    )
    kv = KVWatcher(vault, KVPATH, 30)
    with json_stdout_logging():
        mgr.load()
        mgr._pool.failures.append(psycopg.errors.InvalidPassword("password authentication failed"))
        mgr.whoami()
        kv.poll_once()
        kv.poll_once()
        out, err = capsys.readouterr()

    assert err == ""
    lines = [json.loads(line) for line in out.splitlines()]
    assert all({"ts", "level", "event"} <= line.keys() for line in lines)
    events = [line["event"] for line in lines]
    for expected in (
        "vault_read",
        "creds_acquired",
        "pool_opened",
        "db_login_failed",
        "retry",
        "cache_cleared",
        "pool_swapped",
        "kv_refreshed",
        "kv_refresh_failed",
        "kv_stale",
    ):
        assert expected in events, expected
    kv_line = next(line for line in lines if line["event"] == "kv_refreshed")
    assert kv_line["source"] == "proxy-cache" and kv_line["x_cache"] == "HIT"
    assert kv_line["age"] == "3" and kv_line["version"] == 1
    read = next(line for line in lines if line["event"] == "vault_read")
    assert {"path", "status", "x_cache"} <= read.keys()
    for secret in ("pw-v-user-1-secret", "pw-v-user-2-secret", API_KEY, "password="):
        assert secret not in out
