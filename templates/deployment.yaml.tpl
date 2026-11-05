apiVersion: apps/v1
kind: Deployment
metadata:
  name: db-app
  namespace: ${APP_NAME}
  labels:
    app.kubernetes.io/name: db-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: db-app
  template:
    metadata:
      labels:
        app.kubernetes.io/name: db-app
    spec:
      serviceAccountName: ${APP_NAME}-sa
      automountServiceAccountToken: false
      securityContext:
        runAsUser: 10001
        runAsGroup: 10001
        runAsNonRoot: true
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      initContainers:
        - name: vault-proxy
          image: ${PROXY_IMAGE}
          imagePullPolicy: IfNotPresent
          restartPolicy: Always # native sidecar: starts before the app, stops after it
          # Bypass the image entrypoint (it does not recognise the proxy subcommand)
          command: ["vault"]
          args: ["proxy", "-config=/etc/vault-proxy/proxy.hcl"]
          # No VAULT_ADDR env here: it would override vault.address in proxy.hcl and loop Proxy onto itself
          # Passes only once Proxy holds a valid auto-auth token (force mode answers lookup-self with it),
          # so the app never starts before Proxy has logged in. `vault status` alone is not enough.
          startupProbe:
            exec:
              command: ["vault", "token", "lookup", "-address=unix:///run/vault/proxy.sock", "-format=json"]
            timeoutSeconds: 3
            periodSeconds: 2
            failureThreshold: 30
          resources:
            requests: {cpu: 20m, memory: 32Mi}
            limits: {memory: 128Mi}
          securityContext:
            readOnlyRootFilesystem: true
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - {name: proxy-config, mountPath: /etc/vault-proxy, readOnly: true}
            - {name: vault-token, mountPath: /var/run/secrets/vault, readOnly: true}
            - {name: proxy-socket, mountPath: /run/vault}
            - {name: proxy-tmp, mountPath: /tmp}
      containers:
        - name: app
          image: ${APP_IMAGE}
          imagePullPolicy: Never # imported with k3d image import
          ports:
            - {name: http, containerPort: 8080}
          env:
            - {name: VAULT_SOCKET, value: /run/vault/proxy.sock}
            - {name: DB_CREDS_PATH, value: "db/creds/${ENVIRONMENT}-${APPLICATION}"}
            - {name: DB_HOST, value: postgres.postgres.svc.cluster.local}
            - {name: DB_NAME, value: "${ENVIRONMENT}"}
            - {name: DB_SCHEMA, value: "${APPLICATION}"}
            - {name: API_KEY_PATH, value: "kvv2/data/${ENVIRONMENT}/${APPLICATION}/api-key"}
            - {name: KV_REFRESH_SECONDS, value: "${KV_REFRESH_SECONDS}"}
            - {name: DEMO_MODE, value: "true"}
          readinessProbe:
            httpGet: {path: /healthz, port: http}
            periodSeconds: 5
          resources:
            requests: {cpu: 20m, memory: 64Mi}
            limits: {memory: 256Mi}
          securityContext:
            readOnlyRootFilesystem: true
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - {name: proxy-socket, mountPath: /run/vault}
            - {name: app-tmp, mountPath: /tmp}
      volumes:
        - name: proxy-socket
          emptyDir: {medium: Memory}
        - name: proxy-tmp
          emptyDir: {medium: Memory}
        - name: app-tmp
          emptyDir: {medium: Memory}
        - name: proxy-config
          configMap:
            name: vault-proxy-config
        - name: vault-token
          projected:
            sources:
              - serviceAccountToken:
                  path: token
                  audience: vault
                  expirationSeconds: 600
---
apiVersion: v1
kind: Service
metadata:
  name: db-app
  namespace: ${APP_NAME}
  labels:
    app.kubernetes.io/name: db-app
spec:
  selector:
    app.kubernetes.io/name: db-app
  ports:
    - {name: http, port: 80, targetPort: http}
