#!/usr/bin/env bash
# One-time H01 protocol cutover. Old API binaries consume source events but
# cannot publish Redis fan-out, so they must not overlap v1 during this deploy.
# Once v1 is present, later releases retain the normal zero-downtime rollout.
set -euo pipefail

namespace="${1:?namespace is required}"
deployment="testlookup-backend"
hpa="testlookup-backend-hpa"
KCLI="${KCLI:-kubectl}"
label_path='{.spec.template.metadata.labels.testlookup\.io/live-fanout-protocol}'

if ! "$KCLI" -n "$namespace" get deployment "$deployment" >/dev/null 2>&1; then
  exit 0
fi

# Scoped to SERVING pods only. The migration Job's pod template also carries
# `app: testlookup-backend` (scripts/run-k8s-migrations.sh), and a Job retains
# its pod after completion. Every deploy runs that Job immediately before this
# script, so the pod is always present on the normal path. It has no
# live-fanout-protocol label, so a bare `app=testlookup-backend` probe saw a
# "legacy" pod on every deploy and repeated this one-time cutover each time:
# the HPA deleted and the API scaled to zero. A bare selector in the wait below
# blocks 180s on a Completed pod that is never deleted, then fails under set -e.
SERVING_PODS='app=testlookup-backend,app.kubernetes.io/component!=migration'
current="$("$KCLI" -n "$namespace" get deployment "$deployment" -o "jsonpath=${label_path}")"
pod_protocols="$("$KCLI" -n "$namespace" get pods -l "$SERVING_PODS" \
  -o 'jsonpath={range .items[*]}{.metadata.name}={.metadata.labels.testlookup\.io/live-fanout-protocol}{"\n"}{end}')"
if [[ "$current" == "v1" ]] && ! grep -qv '=v1$' <<<"$pod_protocols"; then
  exit 0
fi

echo "==> One-time live fan-out v1 cutover: stopping legacy API consumers"
# The HPA's minReplicas would otherwise race this scale-down and recreate a
# legacy pod. The immediately following manifest apply recreates the HPA.
"$KCLI" -n "$namespace" delete hpa "$hpa" --ignore-not-found=true
"$KCLI" -n "$namespace" scale deployment "$deployment" --replicas=0
if "$KCLI" -n "$namespace" get pods -l "$SERVING_PODS" -o name | grep -q .; then
  "$KCLI" -n "$namespace" wait --for=delete pod -l "$SERVING_PODS" --timeout=180s
fi
