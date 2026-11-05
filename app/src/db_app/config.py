"""Configuration from environment variables. Every setting is non-secret."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_TRUE = {"1", "true", "yes", "on"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    vault_socket: str
    db_creds_path: str
    db_host: str
    db_name: str
    db_port: int
    db_schema: str
    api_key_path: str
    kv_refresh_seconds: float
    demo_mode: bool
    log_level: str
    port: int
    creds_refresh_max_seconds: float = 60.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env

        def required(name: str) -> str:
            value = env.get(name, "").strip()
            if not value:
                raise ConfigError(f"{name} is required")
            return value

        try:
            return cls(
                vault_socket=env.get("VAULT_SOCKET", "/run/vault/proxy.sock"),
                db_creds_path=required("DB_CREDS_PATH").strip("/"),
                db_host=required("DB_HOST"),
                db_name=required("DB_NAME"),
                db_port=int(env.get("DB_PORT", "5432")),
                db_schema=required("DB_SCHEMA"),
                api_key_path=env.get("API_KEY_PATH", "").strip().strip("/"),
                kv_refresh_seconds=float(env.get("KV_REFRESH_SECONDS", "30")),
                creds_refresh_max_seconds=float(env.get("CREDS_REFRESH_MAX_SECONDS", "60")),
                demo_mode=env.get("DEMO_MODE", "false").strip().lower() in _TRUE,
                log_level=env.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
                port=int(env.get("PORT", "8080")),
            )
        except ValueError as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(str(exc)) from exc
