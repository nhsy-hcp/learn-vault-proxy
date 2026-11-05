# Troubleshooting

Each entry gives the symptom, its cause and the fix. Most were hit while building this lab.

## Vault

| Symptom | Cause | Fix |
| --- | --- | --- |
| `vault-0` Running but not Ready | Vault is sealed (always after a restart) | `task unseal:vault` |
| `init:vault` says the keys are lost | Vault was initialised but `vault-init.json` was deleted | `task clean`, then `task all` |
| `localhost:8200` refuses connections | Cluster stopped, or port 8200 already in use | `task cluster:up`; check with `lsof -nP -iTCP:8200` |

## Vault Proxy sidecar

| Symptom | Cause | Fix |
| --- | --- | --- |
| `exec: proxy: not found` and `Init:CrashLoopBackOff` | The image entrypoint does not know the `proxy` subcommand | Set `command: ["vault"]` (already in `templates/deployment.yaml.tpl`) |
| Startup probe `vault status timed out`; Proxy logs `forwarding request` with no reply | `VAULT_ADDR=unix://…` in the container env overrides `vault.address`, so Proxy forwards to itself | Never set `VAULT_ADDR` in the Proxy container; the probe passes `-address=unix:///run/vault/proxy.sock` |
| Pod never Ready, Proxy logs login errors | Vault sealed, role or policy missing, or the SA does not match `db-app-*-sa` in `db-app-*` | `task unseal:vault`; `task config:k8s-auth`; check the names in `vault-ce/apps.json` |
| App logs `vault_denied` for its own path right after a start or an outage | Proxy had no token yet, so the request reached Vault with no token | Transient: the app retries with backoff (capped at 30 s), and the startup probe (`vault token lookup`) keeps the app from starting before Proxy has logged in |
| Pod takes minutes to recover after an unseal | Proxy auto-auth backoff grows to 5m by default | `max_backoff = "15s"` in `templates/proxy-config.yaml.tpl` |
| `412` from the socket | Request missing `X-Vault-Request: true` | Intended (`require_request_header`); the app always sends the header |

## Database credentials

| Symptom | Cause | Fix |
| --- | --- | --- |
| `config:db-secret` fails with `28P01` for `vault_admin_*` | That connection's root password was rotated and the connection was then deleted or re-created | Leave existing connections in place (the script does this). To start over: `vault secrets disable db`, delete the postgres Deployment, PVC and `postgres-credentials` Secret, then `task install:postgres config:db-secret` |
| App logs `db_login_failed` then `cache_cleared` | The lease was revoked outside Proxy (break-glass); Proxy's cache still held the old credentials | Expected; the app re-reads and recovers |
| `/whoami` returns 503 with `vault_denied` | The SA annotations do not match a `db/roles/<environment>-<application>` role | Fix `vault-ce/apps.json` and redeploy, or add the role in `scripts/config-db-secret.sh` |
| `permission denied for database` | Expected for cross-database access (scenario 5) | None |

## Tooling

| Symptom | Cause | Fix |
| --- | --- | --- |
| A loop over `apps.json` only handles the first app | `kubectl exec -i` reads the loop's stdin | Feed loops on fd 3 (`while read -r row <&3 … done 3< <(…)`), as the scripts do |
| `declare: -A: invalid option` | macOS `/bin/bash` is 3.2 | Scripts avoid bash 4 features; keep it that way |
| Pod `ErrImageNeverPull` | The image was not imported, or its name is not `localhost/db-app:local` (podman's prefix) | `task build` |
| gitleaks flags a test fixture | High-entropy fake values | Use obviously fake, low-entropy strings |
| `vault operator unseal -` returns 400 | The unseal command does not read the key from stdin | Scripts use `vault write sys/unseal key=-`, which keeps the key off the command line |
