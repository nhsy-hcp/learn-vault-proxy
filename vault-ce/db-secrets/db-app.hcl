# db-app: one templated policy for every app. __ACCESSOR__ is replaced with the
# kubernetes-auth-mount accessor at config time (scripts/config-k8s-auth.sh).
# Missing annotations -> the template cannot resolve -> the path matches nothing (fail closed).

# Dynamic PostgreSQL credentials for <environment>-<application_name> only
path "db/creds/{{identity.entity.aliases.__ACCESSOR__.metadata.environment}}-{{identity.entity.aliases.__ACCESSOR__.metadata.application_name}}" {
  capabilities = ["read"]
}

# KV v2 API key: same two annotations, so the same isolation applies
path "kvv2/data/{{identity.entity.aliases.__ACCESSOR__.metadata.environment}}/{{identity.entity.aliases.__ACCESSOR__.metadata.application_name}}/*" {
  capabilities = ["read"]
}
