#!/usr/bin/env bash
# Mise à jour du déploiement k3s depuis ce dépôt.
# Usage :
#   ./scripts/update.sh          # GHCR si origin GitHub, sinon build local
#   ./scripts/update.sh local    # build Docker + import k3s
#   ./scripts/update.sh pull     # image ghcr.io/<owner>/ggr-trafic:latest (ou ancien dépôt)
#   ./scripts/update.sh preview [ref]  # UI test, sans enregistreur → https://ggr-trafic-test.k3s.lpb.ovh
#   ./scripts/update.sh copy-pvc # copie les archives PVC ggr-vacations → ggr-trafic
#
# Recreate : un rollout tue l’enregistreur. Refus si .recording.lock
# (buddy 12:00 TU / bulletin 18:00 TU). Urgence : GGR_FORCE_UPDATE=1.
#
# Kubernetes ne peut pas renommer un namespace : ce script crée / met à jour
# ggr-trafic. Les enregistrements live restent dans ggr-vacations jusqu'à
# copy-pvc. Ne PAS supprimer l'ancien namespace automatiquement.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

KNS="ggr-trafic"
PREVIEW_NS="ggr-trafic-preview"
OLD_KNS="ggr-vacations"
MODE="${1:-auto}"
PREVIEW_REF="${2:-}"

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

if [[ "$MODE" != "preview" && -d .git ]]; then
  git pull --ff-only || true
fi

# Kustomize n'autorise que les fichiers sous k8s/ (restriction de sécurité).
if [[ "$MODE" != "preview" ]]; then
  cp "$ROOT/config/default.yaml" "$ROOT/k8s/config.yaml"
fi

image_from_origin() {
  local remote owner repo
  remote="$(git remote get-url origin 2>/dev/null || true)"
  if [[ "$remote" =~ github.com[:/]([^/]+)/([^/.]+) ]]; then
    owner="$(printf '%s' "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')"
    repo="$(printf '%s' "${BASH_REMATCH[2]}" | tr '[:upper:]' '[:lower:]')"
    printf 'ghcr.io/%s/%s:latest\n' "$owner" "$repo"
  fi
}

build_local() {
  local img="ggr-trafic:local"
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker est requis pour le build local (ou utilisez : $0 pull)" >&2
    exit 1
  fi
  # stdout du build / import ne doit jamais alimenter IMAGE (sinon InvalidImageName).
  as_root docker build -t "$img" "$ROOT"
  as_root docker save "$img" | as_root k3s ctr images import -
}

# Copie ggr-vacations-secrets → ggr-trafic-secrets si le nouveau secret n'existe pas encore.
copy_legacy_secret_if_needed() {
  if kc -n "$KNS" get secret ggr-trafic-secrets >/dev/null 2>&1; then
    return 0
  fi
  if ! kc get ns "$OLD_KNS" >/dev/null 2>&1; then
    return 0
  fi
  if ! kc -n "$OLD_KNS" get secret ggr-vacations-secrets >/dev/null 2>&1; then
    return 0
  fi
  echo "Copie du secret ${OLD_KNS}/ggr-vacations-secrets → ${KNS}/ggr-trafic-secrets…"
  kc -n "$OLD_KNS" get secret ggr-vacations-secrets -o json | python3 -c '
import json, sys
doc = json.load(sys.stdin)
md = dict(doc.get("metadata") or {})
for key in ("uid", "resourceVersion", "creationTimestamp", "generation",
            "managedFields", "selfLink", "ownerReferences"):
    md.pop(key, None)
ann = dict(md.get("annotations") or {})
ann.pop("kubectl.kubernetes.io/last-applied-configuration", None)
if ann:
    md["annotations"] = ann
else:
    md.pop("annotations", None)
md["name"] = "ggr-trafic-secrets"
md["namespace"] = "ggr-trafic"
doc["metadata"] = md
doc.pop("status", None)
json.dump(doc, sys.stdout)
' | kc apply -f -
}

pv_host_path() {
  local pv="$1"
  local path
  path="$(kc get pv "$pv" -o jsonpath='{.spec.hostPath.path}' 2>/dev/null || true)"
  if [[ -z "$path" ]]; then
    path="$(kc get pv "$pv" -o jsonpath='{.spec.local.path}' 2>/dev/null || true)"
  fi
  printf '%s' "$path"
}

# True si un bulletin / buddy / record manuel tient le cadenas PVC.
recording_active() {
  local pod pv path
  pv="$(kc -n "$KNS" get pvc ggr-trafic-data -o jsonpath='{.spec.volumeName}' 2>/dev/null || true)"
  if [[ -n "$pv" ]]; then
    path="$(pv_host_path "$pv")"
    if [[ -n "$path" ]] && as_root test -f "${path}/.recording.lock"; then
      return 0
    fi
  fi
  pod="$(kc -n "$KNS" get pod -l app.kubernetes.io/component=recorder -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [[ -n "$pod" ]] && kc -n "$KNS" exec --request-timeout=8s "$pod" -- test -f /data/.recording.lock >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

refuse_if_recording() {
  if [[ "${GGR_FORCE_UPDATE:-}" == "1" ]]; then
    echo "GGR_FORCE_UPDATE=1 : déploiement malgré un possible enregistrement." >&2
    return 0
  fi
  if recording_active; then
    echo "Enregistrement en cours (.recording.lock) : pas de Recreate." >&2
    echo "Réessayer après 12:00 / 18:00 TU (fin du créneau). Urgence : GGR_FORCE_UPDATE=1 $0 ${MODE}" >&2
    exit 1
  fi
}

# Copie les archives du PVC local-path ancien → nouveau (rsync du hostPath k3s).
# À lancer APRÈS le premier apply (PVC ggr-trafic-data Bound) et AVANT
# de supprimer le namespace ggr-vacations. Hors créneau d'enregistrement.
copy_pvc_data() {
  local old_pvc="ggr-vacations-data"
  local new_pvc="ggr-trafic-data"
  local old_pv new_pv old_path new_path

  if ! kc get ns "$OLD_KNS" >/dev/null 2>&1; then
    echo "Namespace ${OLD_KNS} absent : rien à copier." >&2
    exit 1
  fi
  if ! kc -n "$KNS" get pvc "$new_pvc" >/dev/null 2>&1; then
    echo "PVC ${KNS}/${new_pvc} absent : déployer d'abord (./scripts/update.sh)." >&2
    exit 1
  fi

  echo "Attente du Bound de ${KNS}/${new_pvc}…"
  kc -n "$KNS" wait --for=jsonpath='{.status.phase}'=Bound "pvc/${new_pvc}" --timeout=120s

  old_pv="$(kc -n "$OLD_KNS" get pvc "$old_pvc" -o jsonpath='{.spec.volumeName}')"
  new_pv="$(kc -n "$KNS" get pvc "$new_pvc" -o jsonpath='{.spec.volumeName}')"
  old_path="$(pv_host_path "$old_pv")"
  new_path="$(pv_host_path "$new_pv")"

  if [[ -z "$old_path" || -z "$new_path" ]]; then
    echo "Impossible de lire le hostPath local-path (PV ${old_pv} / ${new_pv})." >&2
    exit 1
  fi
  echo "Source : ${old_path}"
  echo "Cible  : ${new_path}"
  echo "rsync des enregistrements (ne pas supprimer ${OLD_KNS} avant vérif UI)…"
  as_root rsync -aH "${old_path}/" "${new_path}/"
  echo "Copie PVC terminée. Vérifier https://ggr-trafic.k3s.lpb.ovh puis supprimer ${OLD_KNS} à la main."
}

ensure_cert_manager() {
  if kc get crd certificates.cert-manager.io >/dev/null 2>&1; then
    return 0
  fi
  echo "Installation cert-manager v1.13.2 (certificats Let's Encrypt)…"
  kc apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.2/cert-manager.yaml
  kc -n cert-manager rollout status deploy/cert-manager-webhook --timeout=180s
  kc -n cert-manager rollout status deploy/cert-manager-cainjector --timeout=180s
  kc -n cert-manager rollout status deploy/cert-manager --timeout=180s
}

apply_clusterissuer() {
  local i
  for i in 1 2 3 4 5 6; do
    if kc apply -f "$ROOT/k8s/clusterissuer.yaml"; then
      return 0
    fi
    echo "ClusterIssuer : webhook cert-manager pas prêt, nouvel essai (${i}/6)…"
    sleep 5
  done
  return 1
}

# Révision Git pour la preview (branche, tag ou SHA). Sans argument : arbre courant.
preview_tree() {
  if [[ -z "$PREVIEW_REF" ]]; then
    printf '%s' "$ROOT"
    return
  fi
  [[ -d .git ]] || { echo "Dépôt git requis pour preview <ref>" >&2; exit 1; }
  git fetch origin --prune || true
  local rev
  if ! rev="$(git rev-parse --verify "${PREVIEW_REF}^{commit}" 2>/dev/null)"; then
    if ! rev="$(git rev-parse --verify "origin/${PREVIEW_REF}^{commit}" 2>/dev/null)"; then
      echo "Révision inconnue : ${PREVIEW_REF}" >&2
      exit 1
    fi
  fi
  local work
  work="$(mktemp -d /tmp/ggr-preview.XXXXXX)"
  git archive --format=tar "$rev" | tar -x -C "$work"
  printf '%s' "$work"
}

build_preview_image() {
  local src="$1"
  local img="ggr-trafic:preview"
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker est requis pour le build preview" >&2
    exit 1
  fi
  as_root docker build -t "$img" "$src"
  as_root docker save "$img" | as_root k3s ctr images import -
}

deploy_preview() {
  local src work=""
  src="$(preview_tree)"
  if [[ "$src" != "$ROOT" ]]; then
    work="$src"
  fi
  if [[ ! -f "$src/overlays/preview/kustomization.yaml" ]]; then
    echo "Pas d’overlay overlays/preview dans cette révision." >&2
    [[ -n "$work" ]] && rm -rf "$work"
    exit 1
  fi
  cp "$src/config/default.yaml" "$src/k8s/config.yaml"
  build_preview_image "$src"
  ensure_cert_manager
  apply_clusterissuer
  kc apply -f "$src/overlays/preview/namespace.yaml"
  kc apply -k "$src/overlays/preview"
  kc -n "$PREVIEW_NS" set image "deploy/ggr-trafic" "web=ggr-trafic:preview"
  kc -n "$PREVIEW_NS" rollout restart "deploy/ggr-trafic"
  kc -n "$PREVIEW_NS" rollout status "deploy/ggr-trafic" --timeout=180s
  [[ -n "$work" ]] && rm -rf "$work"
  echo "Preview : ggr-trafic:preview"
  echo "UI test : https://ggr-trafic-test.k3s.lpb.ovh"
  echo "Pas d’enregistreur (GGR_SCHEDULER=0). Prod inchangée."
}

if [[ "$MODE" == "copy-pvc" ]]; then
  refuse_if_recording
  copy_pvc_data
  exit 0
fi

if [[ "$MODE" == "preview" ]]; then
  deploy_preview
  exit 0
fi

refuse_if_recording

IMAGE=""
case "$MODE" in
  local)
    IMAGE="ggr-trafic:local"
    build_local
    ;;
  pull)
    IMAGE="$(image_from_origin)"
    if [[ -z "$IMAGE" ]]; then
      echo "Impossible de déduire ghcr.io depuis git remote origin" >&2
      exit 1
    fi
    ;;
  auto)
    IMAGE="$(image_from_origin)"
    if [[ -z "$IMAGE" ]]; then
      IMAGE="ggr-trafic:local"
      build_local
    fi
    ;;
  *)
    echo "usage: $0 [auto|local|pull|preview [ref]|copy-pvc]" >&2
    exit 1
    ;;
esac

ensure_cert_manager
kc apply -f "$ROOT/k8s/namespace.yaml"
apply_clusterissuer
kc apply -k "$ROOT/k8s"
copy_legacy_secret_if_needed
kc -n "$KNS" set image "deploy/ggr-trafic" "web=${IMAGE}"
kc -n "$KNS" set image "deploy/ggr-trafic-recorder" "recorder=${IMAGE}"

if [[ "$IMAGE" == ghcr.io/* ]]; then
  kc -n "$KNS" patch deploy ggr-trafic --type json \
    -p '[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Always"}]'
  kc -n "$KNS" patch deploy ggr-trafic-recorder --type json \
    -p '[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Always"}]'
fi

kc -n "$KNS" rollout restart "deploy/ggr-trafic"
kc -n "$KNS" rollout restart "deploy/ggr-trafic-recorder"
kc -n "$KNS" rollout status "deploy/ggr-trafic" --timeout=180s
kc -n "$KNS" rollout status "deploy/ggr-trafic-recorder" --timeout=180s
if kc get ns "$PREVIEW_NS" >/dev/null 2>&1; then
  kc -n "$PREVIEW_NS" scale deploy/ggr-trafic --replicas=0 >/dev/null 2>&1 || true
fi
echo "Déployé : ${IMAGE}"
echo "UI : https://ggr-trafic.k3s.lpb.ovh"
if kc get ns "$OLD_KNS" >/dev/null 2>&1; then
  echo "Ancien namespace ${OLD_KNS} encore présent : copier le PVC avec $0 copy-pvc avant de le supprimer."
fi
