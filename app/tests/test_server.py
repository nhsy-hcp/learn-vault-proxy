from __future__ import annotations

import http.client
import json
import logging
import threading

import pytest

from conftest import Connector, PoolFactory, creds_response, kv_response
from db_app.config import Config
from db_app.creds import CredentialManager
from db_app.kv import KVWatcher, fingerprint
from db_app.server import App, make_server, revoke_on_shutdown
from db_app.vault_client import VaultClient

CREDS = "db/creds/preview-payments"
KVPATH = "kvv2/data/preview/payments/api-key"
SECRET_BODY = {"data": {"username": "v-prod", "password": "CANARY_PROD_PASSWORD"}}


def config(vault, demo_mode: bool) -> Config:
    return Config(
        vault_socket=vault.socket_path,
        db_creds_path=CREDS,
        db_host="db",
        db_name="preview",
        db_port=5432,
        db_schema="payments",
        api_key_path=KVPATH,
        kv_refresh_seconds=30,
        demo_mode=demo_mode,
        log_level="INFO",
        port=0,
    )


@pytest.fixture
def serve(fake_proxy):
    servers = []

    def start(demo_mode: bool = False, load: bool = True):
        state, vault = fake_proxy
        state.routes[f"GET /v1/{CREDS}"] = creds_response("v-payments")
        state.routes[f"GET /v1/{KVPATH}"] = kv_response("the-api-key-value", 4)
        creds = CredentialManager(
            vault,
            CREDS,
            host="db",
            port=5432,
            dbname="preview",
            pool_factory=PoolFactory(),
            connector=Connector(),
            sleep=lambda _: None,
        )
        kv = KVWatcher(vault, KVPATH, 30)
        if load:
            creds.load()
            kv.poll_once()
        server = make_server(App(config(vault, demo_mode), vault, creds, kv), "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        servers.append(server)

        def get(path: str):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", path)
            resp = conn.getresponse()
            raw = resp.read().decode()
            conn.close()
            return resp.status, json.loads(raw), raw

        get.creds = creds  # type: ignore[attr-defined]
        return state, get

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_healthz_ok_when_pool_open(serve):
    _, get = serve()
    assert get("/healthz")[0] == 200


def test_healthz_503_without_pool(serve):
    _, get = serve(load=False)
    assert get("/healthz")[0] == 503


def test_requests_during_shutdown_get_503_without_new_lease(serve):
    state, get = serve()
    get.creds.stop()
    reads = len(state.calls("GET", f"/v1/{CREDS}"))

    status, body, _ = get("/whoami")

    assert (status, body) == (503, {"error": "shutting_down"})
    assert len(state.calls("GET", f"/v1/{CREDS}")) == reads


def test_whoami_and_data(serve):
    _, get = serve()
    status, body, raw = get("/whoami")
    assert status == 200
    assert body["current_user"] == "v-payments"
    assert "pw-" not in raw

    status, body, _ = get("/data")
    assert status == 200
    assert body["schema"] == "payments"
    assert body["rows"] == [{"id": 1, "name": "widget"}]


def test_apikey_never_returns_value(serve):
    _, get = serve()
    status, body, raw = get("/apikey")
    assert status == 200
    assert body["version"] == 4
    assert body["fingerprint"] == fingerprint("the-api-key-value")
    assert "the-api-key-value" not in raw


@pytest.mark.parametrize("path", ["/try?path=db/creds/production-payments", "/try"])
def test_try_disabled_unless_demo_mode(serve, path):
    state, get = serve(demo_mode=False)
    assert get(path)[0] == 404
    assert state.calls("GET", "/v1/db/creds/production-payments") == []


def test_try_returns_status_only_never_body(serve):
    state, get = serve(demo_mode=True)
    state.routes["GET /v1/db/creds/production-payments"] = (200, {}, SECRET_BODY)

    status, body, raw = get("/try?path=db/creds/production-payments")

    assert status == 200
    assert body == {"path": "db/creds/production-payments", "status": 200}
    assert "CANARY" not in raw and "v-prod" not in raw
    (req,) = state.calls("GET", "/v1/db/creds/production-payments")
    assert req.headers["X-Vault-Request"] == "true"


def test_try_reports_denial(serve):
    state, get = serve(demo_mode=True)
    state.routes["GET /v1/kvv2/data/production/payments/api-key"] = (
        403,
        {},
        {"errors": ["permission denied"]},
    )
    status, body, _ = get("/try?path=kvv2/data/production/payments/api-key")
    assert status == 200
    assert body == {"path": "kvv2/data/production/payments/api-key", "status": 403}


@pytest.mark.parametrize("query", ["", "?path=", "?path=../sys/seal"])
def test_try_rejects_bad_paths(serve, query):
    _, get = serve(demo_mode=True)
    assert get(f"/try{query}")[0] == 400


def test_unknown_route_404(serve):
    _, get = serve()
    assert get("/nope")[0] == 404


def test_access_log_skips_healthz(serve, caplog):
    _, get = serve()
    with caplog.at_level("INFO"):
        get("/healthz")
        get("/whoami")
    paths = [r.fields["path"] for r in caplog.records if r.getMessage() == "http_request"]
    assert paths == ["/whoami"]


def _revoke_events(caplog):
    return [r for r in caplog.records if r.getMessage() == "revoke_self_failed"]


def test_revoke_on_shutdown_success_logs_no_warning(fake_proxy, caplog):
    state, vault = fake_proxy
    state.routes["POST /v1/auth/token/revoke-self"] = (204, {}, None)

    with caplog.at_level(logging.INFO):
        revoke_on_shutdown(vault)

    assert len(state.calls("POST", "/v1/auth/token/revoke-self")) == 1
    assert _revoke_events(caplog) == []


def test_revoke_on_shutdown_denied_logs_warning(fake_proxy, caplog):
    state, vault = fake_proxy
    state.routes["POST /v1/auth/token/revoke-self"] = (403, {}, {"errors": ["permission denied"]})

    with caplog.at_level(logging.INFO):
        revoke_on_shutdown(vault)

    (rec,) = _revoke_events(caplog)
    assert rec.levelno == logging.WARNING


def test_revoke_on_shutdown_never_raises_without_proxy(socket_dir, caplog):
    vault = VaultClient(str(socket_dir / "missing.sock"), timeout=0.2)

    with caplog.at_level(logging.INFO):
        revoke_on_shutdown(vault)

    (rec,) = _revoke_events(caplog)
    assert rec.levelno == logging.WARNING
