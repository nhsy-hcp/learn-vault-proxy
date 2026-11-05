apiVersion: v1
kind: ConfigMap
metadata:
  name: vault-proxy-config
  namespace: ${APP_NAME}
data:
  proxy.hcl: |
    vault {
      address = "http://vault.vault.svc.cluster.local:8200"
    }

    auto_auth {
      method "kubernetes" {
        mount_path = "auth/kubernetes-auth-mount"
        config = {
          role       = "db-app"
          token_path = "/var/run/secrets/vault/token"
        }
      }
    }

    # No sink: the Vault token stays in Proxy memory and never touches disk.

    api_proxy {
      use_auto_auth_token = "force"
    }

    cache {}

    listener "unix" {
      address                = "/run/vault/proxy.sock"
      socket_mode            = "0660"
      socket_user            = "10001"
      socket_group           = "10001"
      tls_disable            = true
      require_request_header = true
    }
