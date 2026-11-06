from __future__ import annotations

import http.client

import pytest

from conftest import truncated_response
from db_app.vault_client import VaultClient


def test_read_sends_x_vault_request_header_and_no_token(fake_proxy):
    state, vault = fake_proxy
    state.routes["GET /v1/db/creds/preview-payments"] = (200, {}, {"data": {"x": 1}})

    status, _, body = vault.read("db/creds/preview-payments")

    assert status == 200
    assert body == {"data": {"x": 1}}
    (req,) = state.calls("GET", "/v1/db/creds/preview-payments")
    assert req.headers["X-Vault-Request"] == "true"
    assert "X-Vault-Token" not in req.headers
    assert "Authorization" not in req.headers


def test_cache_clear_request_shape(fake_proxy):
    state, vault = fake_proxy

    assert vault.cache_clear("db/creds/preview-payments") == 200

    (req,) = state.calls("POST", "/proxy/v1/cache-clear")
    assert req.headers["X-Vault-Request"] == "true"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body == {"type": "request_path", "value": "/v1/db/creds/preview-payments"}


def test_read_returns_headers(fake_proxy):
    state, vault = fake_proxy
    state.routes["GET /v1/kvv2/data/a"] = (200, {"X-Cache": "HIT", "Age": "7"}, {})

    _, headers, _ = vault.read("kvv2/data/a")

    assert headers.get("x-cache") == "HIT"
    assert headers.get("Age") == "7"


@pytest.mark.parametrize("path", ["", "../sys/seal", "db//creds", "a/./b"])
def test_rejects_invalid_paths(fake_proxy, path):
    _, vault = fake_proxy
    with pytest.raises(ValueError):
        vault.read(path)


def test_wait_for_socket(fake_proxy, socket_dir):
    _, vault = fake_proxy
    assert vault.wait_for_socket(max_wait=1.0)
    missing = VaultClient(str(socket_dir / "missing.sock"))
    assert not missing.wait_for_socket(max_wait=0.2, interval=0.05)


def test_truncated_response_raises_transport_error(fake_proxy):
    state, vault = fake_proxy
    state.routes["GET /v1/kvv2/data/a"] = truncated_response()

    with pytest.raises(ConnectionError) as info:
        vault.read("kvv2/data/a")

    assert isinstance(info.value, OSError)
    assert isinstance(info.value.__cause__, http.client.IncompleteRead)


def test_revoke_self_request_shape(fake_proxy):
    state, vault = fake_proxy
    state.routes["POST /v1/auth/token/revoke-self"] = (204, {}, None)

    assert vault.revoke_self() == 204

    (req,) = state.calls("POST", "/v1/auth/token/revoke-self")
    assert req.headers["X-Vault-Request"] == "true"
    assert "X-Vault-Token" not in req.headers
    assert req.body is None
