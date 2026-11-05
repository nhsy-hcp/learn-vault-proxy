# learn-vault-proxy

A sample app on k3d gets short-lived PostgreSQL credentials from Vault Community Edition without holding any Vault
secret. It authenticates with its Kubernetes ServiceAccount through a Vault Proxy sidecar listening on a UNIX socket.
One templated policy, driven by ServiceAccount annotations, isolates each app and environment.

![Architecture](docs/images/architecture.png)

## Prerequisites

kubectl, helm, k3d, docker or podman, jq, task, uv, pre-commit, shellcheck, gitleaks, yamllint, hadolint, ruff,
envsubst and openssl. Check them with `task deps`.

## Quick start

```bash
task init     # pre-commit hooks, Helm repo, .env
task deps     # verify tools
task --list   # available tasks
```

## Documentation

- [Solution design](docs/solution-design.md)
- [Implementation plan](.plans/implementation-plan.md)
