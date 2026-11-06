"""HTTP server (stdlib ``http.server``) exposing JSON endpoints.

| Path       | Returns                                                                   |
| ---------- | ------------------------------------------------------------------------- |
| /healthz   | 200 when a pool is open, else 503                                         |
| /whoami    | current_user, lease ID suffix, seconds to expiry (never the password)     |
| /data      | rows from ``<DB_SCHEMA>.items``                                           |
| /apikey    | KV version, fingerprint, source, cache headers, staleness (never the key) |
| /try?path= | DEMO_MODE only: HTTP status of a GET to another Vault path, never the body |
"""

from __future__ import annotations

import json
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from db_app import __version__
from db_app.config import Config, ConfigError
from db_app.creds import CredentialManager, NoPool, RetriesExhausted
from db_app.kv import KVWatcher
from db_app.logging_setup import configure_logging, log_event
from db_app.vault_client import VaultClient, VaultDenied

log = logging.getLogger(__name__)


class App:
    def __init__(
        self,
        config: Config,
        vault: VaultClient,
        creds: CredentialManager,
        kv: KVWatcher | None,
    ) -> None:
        self.config = config
        self.vault = vault
        self.creds = creds
        self.kv = kv

    # Each handler returns (http_status, json_body).
    def healthz(self, _query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        if self.creds.is_healthy():
            return 200, {"status": "ok"}
        return 503, {"status": "unavailable", "vault_denied": self.creds.denied}

    def whoami(self, _query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        return 200, self.creds.whoami()

    def data(self, _query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        query = sql.SQL("SELECT * FROM {}.items").format(sql.Identifier(self.config.db_schema))

        def fetch(conn: Any) -> list[dict[str, Any]]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query)
                return cur.fetchall()

        rows = self.creds.run_query(fetch)
        return 200, {"schema": self.config.db_schema, "count": len(rows), "rows": rows}

    def apikey(self, _query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        if self.kv is None:
            return 404, {"error": "api_key_path_not_configured"}
        status = self.kv.status()
        return (200 if self.kv.has_value else 503), status

    def try_path(self, query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        if not self.config.demo_mode:
            return 404, {"error": "not_found"}
        path = (query.get("path") or [""])[0].strip().lstrip("/")
        if not path:
            return 400, {"error": "missing_path"}
        try:
            status, _, _ = self.vault.read(path)  # body deliberately discarded
        except ValueError:
            return 400, {"error": "invalid_path"}
        except OSError as exc:
            log_event(log, logging.WARNING, "try_request", path=path, error=type(exc).__name__)
            return 502, {"path": path, "status": None}
        if status == 403:
            log_event(log, logging.WARNING, "vault_denied", path=path, status=status, via="try")
        log_event(log, logging.INFO, "try_request", path=path, status=status)
        return 200, {"path": path, "status": status}

    def routes(self) -> dict[str, Any]:
        return {
            "/healthz": self.healthz,
            "/whoami": self.whoami,
            "/data": self.data,
            "/apikey": self.apikey,
            "/try": self.try_path,
        }


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    routes = app.routes()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"db-app/{__version__}"
        sys_version = ""

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            handler = routes.get(parts.path)
            if handler is None:
                self._send(404, {"error": "not_found"})
                return
            try:
                status, body = handler(parse_qs(parts.query))
            except VaultDenied as exc:
                status, body = 403, {"error": "vault_denied", "path": exc.path}
            except (RetriesExhausted, NoPool):
                status, body = 503, {"error": "database_unavailable"}
            except psycopg.Error as exc:
                status = 500
                body = {"error": "database_error", "sqlstate": getattr(exc, "sqlstate", None)}
                log_event(log, logging.ERROR, "db_query_failed", sqlstate=body["sqlstate"])
            except Exception:
                log.exception("request_failed")
                status, body = 500, {"error": "internal_error"}
            self._send(status, body)

        def _send(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            if urlsplit(self.path).path == "/healthz":
                return  # probes every few seconds: keep kubectl logs readable
            log_event(
                log,
                logging.INFO,
                "http_request",
                method=self.command,
                path=urlsplit(self.path).path,
                status=int(code) if str(code).isdigit() else code,
            )

        def log_message(self, format: str, *args: Any) -> None:
            log_event(log, logging.WARNING, "http_server", message=format % args)

    return Handler


def make_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(app))


def build_app(config: Config) -> App:
    vault = VaultClient(config.vault_socket)
    creds = CredentialManager(
        vault,
        config.db_creds_path,
        host=config.db_host,
        port=config.db_port,
        dbname=config.db_name,
        max_refresh_seconds=config.creds_refresh_max_seconds,
    )
    kv = (
        KVWatcher(vault, config.api_key_path, config.kv_refresh_seconds)
        if config.api_key_path
        else None
    )
    return App(config, vault, creds, kv)


def revoke_on_shutdown(vault: VaultClient) -> None:
    """Best effort: revoke Proxy's token (and the DB lease) on the way out; never raise."""
    try:
        status = vault.revoke_self()
    except OSError as exc:
        log_event(log, logging.WARNING, "revoke_self_failed", error=type(exc).__name__)
        return
    if status != 204:
        log_event(log, logging.WARNING, "revoke_self_failed", status=status)


def main() -> int:
    configure_logging("INFO")
    try:
        config = Config.from_env()
    except ConfigError as exc:
        log_event(log, logging.ERROR, "config_invalid", error=str(exc))
        return 2
    configure_logging(config.log_level)
    log_event(
        log,
        logging.INFO,
        "startup_config",
        version=__version__,
        socket=config.vault_socket,
        creds_path=config.db_creds_path,
        api_key_path=config.api_key_path or None,
        db_host=config.db_host,
        db_name=config.db_name,
        db_schema=config.db_schema,
        demo_mode=config.demo_mode,
        port=config.port,
    )

    app = build_app(config)
    if not app.vault.wait_for_socket(max_wait=30.0):
        return 1
    app.creds.start()
    if app.kv is not None:
        app.kv.start()

    server = make_server(app, "0.0.0.0", config.port)  # noqa: S104 - container port
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    thread = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    thread.start()
    log_event(log, logging.INFO, "listening", port=config.port)

    stop.wait()
    log_event(log, logging.INFO, "stopping")
    server.shutdown()
    server.server_close()
    if app.kv is not None:
        app.kv.stop()
    app.creds.stop()
    # Pool closed first, so revocation never terminates a session still in use. Proxy is a
    # native sidecar and stops after this container, so the socket is still served here.
    revoke_on_shutdown(app.vault)
    return 0
