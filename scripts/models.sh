#!/usr/bin/env bash
# Model weights from infra/models.txt (pinned revisions) into MODELS_DIR from .env.
#   scripts/models.sh pull [NAME]    download every model, or just NAME
#   scripts/models.sh lock           record sha256 of every downloaded file in infra/models.sha256
#   scripts/models.sh verify [NAME]  check downloaded files against infra/models.sha256
set -euo pipefail
cd "$(dirname "$0")/.."

MODELS_DIR=$(sed -n 's/^MODELS_DIR=//p' .env)
[ -d "$MODELS_DIR" ] || { echo "MODELS_DIR '$MODELS_DIR' missing: run make init" >&2; exit 1; }
HF="uvx --from huggingface_hub==1.32.0 hf"
LIST="$PWD/infra/models.txt"
SUMS="$PWD/infra/models.sha256"

entries() { grep -vE '^\s*(#|$)' "$LIST" | { if [ -n "${1:-}" ]; then awk -v n="$1" '$1 == n'; else cat; fi; }; }

pull() {
  entries "${1:-}" | while read -r name repo rev excludes; do
    echo "== $name ($repo@${rev:0:12})"
    args=()
    for glob in $excludes; do args+=(--exclude "$glob"); done
    $HF download "$repo" --revision "$rev" --local-dir "$MODELS_DIR/$name" "${args[@]}" >/dev/null
  done
}

lock() {
  (cd "$MODELS_DIR" && entries | awk '{print $1}' | xargs -I{} find {} -type f -not -path '*/.cache/*' \
    | sort | xargs sha256sum) > "$SUMS"
  echo "wrote $(wc -l < "$SUMS") checksums to infra/models.sha256"
}

verify() {
  local filter="${1:-}"
  (cd "$MODELS_DIR" && grep -E "  ${filter:-[^/]+}/" "$SUMS" | sha256sum -c --quiet) && echo "models ok"
}

case "${1:-}" in
  pull) pull "${2:-}" ;;
  lock) lock ;;
  verify) verify "${2:-}" ;;
  *) sed -n '2,5p' "$0" >&2; exit 2 ;;
esac
