apiVersion: v1
kind: Namespace
metadata:
  name: ${APP_NAME}
  labels:
    app.kubernetes.io/part-of: db-app
    db-app/application: ${APPLICATION}
    db-app/environment: ${ENVIRONMENT}
