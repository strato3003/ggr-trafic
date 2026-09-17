#!/usr/bin/env bash
# Supervision k3s dans le NS monitoring (Prometheus, Grafana dédié).
# Ne touche PAS au Grafana applicatif https://dashboard.k3s.lpb.ovh (NS grafana).
# À lancer sur le VPS, hors 12:00 / 18:00 TU.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

kc() {
  if command -v k3s >/dev/null 2>&1; then
    as_root k3s kubectl "$@"
  else
    kubectl "$@"
  fi
}

kc apply -f "$ROOT/k8s/monitoring/namespace.yaml"

if ! kc -n monitoring get secret k3s-grafana-admin >/dev/null 2>&1; then
  pass="$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)"
  kc -n monitoring create secret generic k3s-grafana-admin \
    --from-literal=user=admin \
    --from-literal=password="$pass"
  echo "Grafana k3s — identifiant admin, mot de passe (une fois) : ${pass}"
else
  echo "Secret k3s-grafana-admin déjà présent."
fi

echo "Manifests monitoring (sans NS grafana)…"
kc apply -k "$ROOT/k8s/monitoring"
kc apply -f "$ROOT/k8s/monitoring/traefik-clientip.yaml"
kc -n monitoring rollout restart deploy/prometheus
kc -n monitoring rollout restart deploy/k3s-grafana

# Plus d’Alertmanager (pas d’alertes mail).
kc -n monitoring delete deploy alertmanager --ignore-not-found
kc -n monitoring delete svc alertmanager --ignore-not-found
kc -n monitoring delete secret alertmanager-config --ignore-not-found

kc -n monitoring rollout status deploy/prometheus --timeout=180s
kc -n monitoring rollout status deploy/kube-state-metrics --timeout=120s
kc -n monitoring rollout status deploy/k3s-grafana --timeout=180s

echo "Grafana k3s : https://monitoring.k3s.lpb.ovh  (indépendant de dashboard.k3s.lpb.ovh)"
