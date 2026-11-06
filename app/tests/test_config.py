from __future__ import annotations

import pytest

from db_app.config import Config, ConfigError

BASE = {
    "DB_CREDS_PATH": "db/creds/preview-payments",
    "DB_HOST": "postgres",
    "DB_NAME": "preview",
    "DB_SCHEMA": "payments",
}


def test_defaults():
    cfg = Config.from_env(BASE)
    assert cfg.vault_socket == "/run/vault/proxy.sock"
    assert cfg.db_port == 5432
    assert cfg.kv_refresh_seconds == 30
    assert cfg.demo_mode is False
    assert cfg.port == 8080
    assert cfg.creds_refresh_max_seconds == 60


def test_creds_refresh_max_seconds_override():
    cfg = Config.from_env({**BASE, "CREDS_REFRESH_MAX_SECONDS": "15"})
    assert cfg.creds_refresh_max_seconds == 15


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("TRUE", True), ("no", False)])
def test_demo_mode(value, expected):
    assert Config.from_env({**BASE, "DEMO_MODE": value}).demo_mode is expected


def test_missing_required():
    with pytest.raises(ConfigError):
        Config.from_env({k: v for k, v in BASE.items() if k != "DB_SCHEMA"})


def test_bad_number():
    with pytest.raises(ConfigError):
        Config.from_env({**BASE, "DB_PORT": "x"})


@pytest.mark.parametrize("name", ["KV_REFRESH_SECONDS", "CREDS_REFRESH_MAX_SECONDS"])
@pytest.mark.parametrize("value", ["0", "-5", "nan", "inf"])
def test_refresh_interval_must_be_positive(name, value):
    with pytest.raises(ConfigError, match=name):
        Config.from_env({**BASE, name: value})
