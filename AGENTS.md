# AGENTS.md

Rules and orientation for AI agents working in this repository.

## Purpose

A local lab showing Vault Kubernetes-auth workload identity, a Vault Proxy native sidecar on a UNIX socket, and
dynamic PostgreSQL credentials on k3d with Vault Community Edition. The design is `docs/solution-design.md`; the
implementation plan is kept locally in `.plans/implementation-plan.md` (git-ignored).

## Rules

- Write files only inside this project. `~/Projects/training/learn-vault-secrets-operator` is a read-only reference.
- Temporary files go in `.tmp/`, plans go in `.plans/`.
- All automation runs through `task`. Logic longer than about 20 lines goes in `scripts/*.sh`, which must start with
  `#!/bin/bash` and `set -euo pipefail` and pass shellcheck.
- Never hardcode secrets. `vault-init.json`, `.env` and `*.lic` are git-ignored. Add new environment variables to
  `.env.template`.
- Stage files by explicit path only, and never use `--no-verify`. Run `task lint` before every commit.
- Commit messages follow Conventional Commits (`type: description`, lowercase).
- The sample app is Python 3.12 managed with uv (`app/pyproject.toml`). It must not use `hvac`: it calls Vault Proxy
  over the UNIX socket with the standard library only.
- Vault configuration runs as `kubectl exec vault-0 -n vault -- vault ...`, with the root token read from
  `vault-init.json`. Every config step must be safe to re-run.

## Layout

| Path | Contents |
| --- | --- |
| `Taskfile.yml` | All tasks; version pins and TTL variables at the top |
| `scripts/` | Bash helpers invoked from the Taskfile |
| `vault-ce/` | Vault Helm values, RBAC, policies, roles, PostgreSQL manifests and bootstrap SQL, `apps.json` |
| `templates/` | Per-app manifest templates rendered with `envsubst` |
| `app/` | `db-app` Python source, tests and Dockerfile |
| `docs/` | Design, walkthrough and troubleshooting |
| `.github/workflows/ci.yml` | Lint and unit tests; e2e on k3d for non-main branches. Tools come from `scripts/ci-install-tools.sh` |

## Common tasks

| Task | Does |
| --- | --- |
| `task init` | Installs pre-commit hooks, adds the Helm repo, creates `.env`, syncs the app venv |
| `task deps` | Checks the required CLI tools |
| `task all` (`up`) | Builds the whole lab: cluster, Vault, auth, PostgreSQL, engines, apps, verify |
| `task down` | Stops the cluster, keeping its data; afterwards run `task cluster:up` and `task unseal:vault` |
| `task build` | Builds the app image and imports it into k3d |
| `task lint` | Runs every pre-commit hook |
| `task test` | Runs the app unit tests (no cluster) |
| `task test:all` | Runs the unit tests, `verify:db-app` and `demo:isolation` on the running lab |
| `task test:ci` | Runs lint and the unit tests; with `E2E=true`, also builds the lab and runs `test:all` |
| `task demo:*` | Runs the demo scenarios (see `docs/db-proxy-secrets.md`) |
| `task logs APP=… C=app\|vault-proxy` | Follows an app's JSON logs (k9s works too) |
| `task clean` | Deletes the cluster, `vault-init.json` and temporary files |

For the full list, run `task --list`.

## Workflow for agents

1. Run `task deps` first and stop if anything is missing. Scripts must run on macOS `/bin/bash` 3.2, so no
   associative arrays and no `mapfile`.
2. Run tasks in order. If a step fails, stop and report rather than improvising changes.
3. Use `localhost:8200` for Vault from the laptop, and `kubectl exec vault-0` inside the cluster.
