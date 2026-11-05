# db-app

Sample workload for the lab. It gets dynamic PostgreSQL credentials and a KV v2 API key from the Vault
Proxy sidecar over a UNIX socket, using only the Python standard library for HTTP (no `hvac`, no
Vault token: Proxy runs with `use_auto_auth_token = "force"`). Every request sends
`X-Vault-Request: true`. See `docs/solution-design.md` sections 6 to 8.

## Configuration

All settings are environment variables, and none of them is secret.

| Variable | Default | Example / meaning |
| --- | --- | --- |
| `VAULT_SOCKET` | `/run/vault/proxy.sock` | Proxy UNIX socket |
| `DB_CREDS_PATH` | required | `db/creds/preview-payments` |
| `DB_HOST` / `DB_NAME` | required | `postgres.postgres.svc.cluster.local` / `preview` |
| `DB_PORT` | `5432` | |
| `DB_SCHEMA` | required | `payments`: `/data` reads `<schema>.items` |
| `API_KEY_PATH` | empty (KV watcher off) | `kvv2/data/preview/payments/api-key` |
| `KV_REFRESH_SECONDS` | `30` | KV poll interval |
| `CREDS_REFRESH_MAX_SECONDS` | `60` | Upper bound on the credential refresh interval |
| `DEMO_MODE` | `false` | `true` enables `/try` |
| `LOG_LEVEL` | `INFO` | |
| `PORT` | `8080` | HTTP listen port |

PostgreSQL connections use `sslmode=disable` (demo only).

## Endpoints (JSON)

| Path | Returns |
| --- | --- |
| `/healthz` | `200` when a connection pool is open, else `503` |
| `/whoami` | `current_user`, lease ID suffix (last 6 characters) and seconds to expiry; never the password |
| `/data` | Rows of `<DB_SCHEMA>.items` |
| `/apikey` | KV `version`, `fingerprint` (first 12 hex characters of SHA-256), `source` (`vault`, `proxy-cache` or `app-memory`), `x_cache`, `age`, `stale` and `seconds_since_refresh`; never the key |
| `/try?path=<vault path>` | `DEMO_MODE=true` only, otherwise `404`. `{"path": ..., "status": <int>}`; the body is never returned |

## Behaviour

- Credentials refresh at 2/3 of `lease_duration`, capped at `CREDS_REFRESH_MAX_SECONDS` (60 s), so
  a rotation when Vault drops the user at the role's `max_ttl` is noticed within a minute. If the
  username changes, a new pool is built, swapped in and the old one closed. Proxy serves cached
  reads with the original body, so while the `lease_id` is unchanged the original fetch time is kept
  and `/whoami` counts `lease_seconds_remaining` down from the first read.
- Connection loss (SQLSTATE `57P01`/`57P02`/`57P03` or `OperationalError`) rebuilds the pool. A
  login failure (`28P01`/`28000`) calls `POST /proxy/v1/cache-clear` for the path, re-reads and
  rebuilds. Jittered exponential backoff, at most 5 attempts.
- A Vault `403` logs `vault_denied` (WARNING) every time. `/try` and the `run_query` recovery path
  never retry it. The background credential refresh retries it with a jittered backoff from 5 s up
  to 30 s, because Proxy forwards requests without a token while it has none (startup, restart,
  re-authentication); until it succeeds `/healthz` returns `503` and any existing pool is kept. The
  KV watcher keeps polling on its normal backoff.
- Background loops never die: a truncated Proxy response (`IncompleteRead` and other
  `http.client` errors) is raised as a `ConnectionError`, and any unexpected exception is logged
  (`creds_loop_error` / `kv_loop_error`) and retried with backoff.
- Logs are JSON lines on stdout (`ts`, `level`, `event` plus context), for `kubectl logs`.
  `/healthz` requests are not access-logged. Passwords and the API key are never logged.

## Development

```bash
uv sync
uv run pytest -q
ruff check . && ruff format --check .
hadolint Dockerfile
podman build -t db-app:local .
```

The tests use a fake Vault Proxy on a UNIX socket and fake connection pools, so they need neither
Vault nor PostgreSQL. The image runs as UID/GID 10001 and works with `readOnlyRootFilesystem`.
