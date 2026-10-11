#!/usr/bin/env bash
# Run application database migrations as one bounded Kubernetes Job.
# Usage: scripts/run-k8s-migrations.sh <namespace> <backend-image> [release-id]

set -euo pipefail

NAMESPACE="${1:-}"
BACKEND_IMAGE="${2:-}"
RELEASE_ID="${3:-}"
KCLI="${KCLI:-kubectl}"
TIMEOUT_SECONDS="${MIGRATION_TIMEOUT_SECONDS:-600}"
POLL_SECONDS="${MIGRATION_POLL_SECONDS:-2}"

if [[ -z "$NAMESPACE" || -z "$BACKEND_IMAGE" ]]; then
  echo "Usage: $0 <namespace> <backend-image> [release-id]" >&2
  exit 2
fi
[[ "$NAMESPACE" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || {
  echo "ERROR: invalid Kubernetes namespace: $NAMESPACE" >&2; exit 2;
}
[[ "$BACKEND_IMAGE" =~ ^[A-Za-z0-9._/:@+-]+$ ]] || {
  echo "ERROR: invalid backend image reference: $BACKEND_IMAGE" >&2; exit 2;
}
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: MIGRATION_TIMEOUT_SECONDS must be a positive integer" >&2; exit 2;
}
[[ "$POLL_SECONDS" =~ ^[0-9]+$ ]] || {
  echo "ERROR: MIGRATION_POLL_SECONDS must be a non-negative integer" >&2; exit 2;
}
command -v "$KCLI" >/dev/null || { echo "ERROR: $KCLI not found in PATH" >&2; exit 1; }

if [[ -n "$RELEASE_ID" ]]; then
  [[ "$RELEASE_ID" =~ ^[A-Fa-f0-9]{7,64}$ ]] || {
    echo "ERROR: release-id must be a 7-64 character hexadecimal revision" >&2; exit 2;
  }
  JOB_SUFFIX="${RELEASE_ID:0:12}"
else
  JOB_SUFFIX="$(printf '%s' "$BACKEND_IMAGE" | cksum | awk '{print $1}')"
fi
ATTEMPT_ID="${MIGRATION_ATTEMPT_ID:-$(date -u +%Y%m%d%H%M%S)-$$-$RANDOM}"
[[ "$ATTEMPT_ID" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || {
  echo "ERROR: invalid migration attempt id: $ATTEMPT_ID" >&2; exit 2;
}
JOB_NAME="testlookup-migrate-$JOB_SUFFIX-$ATTEMPT_ID"
(( ${#JOB_NAME} <= 63 )) || {
  echo "ERROR: generated migration Job name exceeds 63 characters: $JOB_NAME" >&2; exit 2;
}

show_failure() {
  echo "ERROR: database migration Job failed: $NAMESPACE/$JOB_NAME" >&2
  "$KCLI" -n "$NAMESPACE" describe job "$JOB_NAME" >&2 || true
  "$KCLI" -n "$NAMESPACE" logs "job/$JOB_NAME" --all-containers=true >&2 || true
}

echo "==> Creating migration Job $JOB_NAME for $BACKEND_IMAGE"
cat <<EOF | "$KCLI" create -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: testlookup-backend
    app.kubernetes.io/name: testlookup
    app.kubernetes.io/component: migration
spec:
  backoffLimit: 0
  activeDeadlineSeconds: ${TIMEOUT_SECONDS}
  ttlSecondsAfterFinished: 604800
  template:
    metadata:
      labels:
        app: testlookup-backend
        app.kubernetes.io/name: testlookup
        app.kubernetes.io/component: migration
    spec:
      automountServiceAccountToken: false
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        # runAsUser/runAsGroup are REQUIRED alongside runAsNonRoot here, not
        # decoration. The backend image ends with "USER testlookup" — a NAME —
        # and the kubelet cannot prove a non-numeric user is non-root, so it
        # refuses the container outright:
        #
        #   Error: container has runAsNonRoot and image has non-numeric user
        #   (testlookup), cannot verify user is non-root
        #
        # The pod then sits in Init:CreateContainerConfigError until the
        # migration times out, which is what it did on the homelab deploy.
        # backend-deployment.yaml already sets both, and the Dockerfile creates
        # the user at UID 1000 with the comment "to match the k8s
        # securityContext (runAsUser: 1000)" — this Job was the one place that
        # had the intent written down and never applied it.
        runAsUser: 1000
        runAsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      initContainers:
        - name: wait-for-postgres
          image: ${BACKEND_IMAGE}
          imagePullPolicy: IfNotPresent
          command: ["python", "-c"]
          args:
            - |
              import os, socket, time
              from sqlalchemy.engine import make_url
              url = make_url(os.environ["DATABASE_URL"])
              while True:
                  try:
                      with socket.create_connection((url.host, url.port or 5432), timeout=5):
                          break
                  except OSError:
                      time.sleep(2)
          envFrom:
            - secretRef:
                name: testlookup-secrets
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
      containers:
        - name: migrate
          image: ${BACKEND_IMAGE}
          imagePullPolicy: IfNotPresent
          command: ["alembic", "upgrade", "head"]
          envFrom:
            - secretRef:
                name: testlookup-secrets
          env:
            - name: PG_PROCESS_ROLE
              value: operation
            - name: PG_PROCESSES_PER_POD
              value: "1"
            - name: PG_POOL_SIZE
              value: "1"
            - name: PG_MAX_OVERFLOW
              value: "0"
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
EOF
DEADLINE=$((SECONDS + TIMEOUT_SECONDS))
while (( SECONDS < DEADLINE )); do
  CONDITIONS="$("$KCLI" -n "$NAMESPACE" get job "$JOB_NAME" \
    -o jsonpath='{range .status.conditions[*]}{.type}={.status}{"\n"}{end}' 2>/dev/null || true)"
  if grep -qx 'Complete=True' <<<"$CONDITIONS"; then
    "$KCLI" -n "$NAMESPACE" logs "job/$JOB_NAME" --all-containers=true
    echo "==> Database migrations completed."
    exit 0
  fi
  if grep -qx 'Failed=True' <<<"$CONDITIONS"; then
    show_failure
    exit 1
  fi
  sleep "$POLL_SECONDS"
done
echo "ERROR: database migration Job timed out after ${TIMEOUT_SECONDS}s" >&2
show_failure
exit 1
