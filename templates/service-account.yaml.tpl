# Vault copies these alias-metadata-* annotations onto the entity alias at every login;
# the db-app policy is templated on environment and application_name.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${APP_NAME}-sa
  namespace: ${APP_NAME}
  annotations:
    vault.hashicorp.com/alias-metadata-application_name: ${APPLICATION}
    vault.hashicorp.com/alias-metadata-environment: ${ENVIRONMENT}
    vault.hashicorp.com/alias-metadata-team: ${TEAM}
automountServiceAccountToken: false
