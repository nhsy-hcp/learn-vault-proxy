# The app team can see its workload but cannot change ServiceAccounts, so it cannot edit the
# alias-metadata-* annotations that decide which credentials Vault hands out (scenario 8).
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: app-team
  namespace: ${APP_NAME}
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "services", "configmaps", "events"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["serviceaccounts"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: app-team
  namespace: ${APP_NAME}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: app-team
subjects:
  - apiGroup: rbac.authorization.k8s.io
    kind: User
    name: app-team
