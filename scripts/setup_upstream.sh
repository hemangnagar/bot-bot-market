#!/usr/bin/env bash
# Fetch the two upstream repos that are run as sidecars (not pip-installable into the bench venv),
# pinned to commit SHAs, under .upstream/ (gitignored). Nothing is copied into this repo.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UP="$ROOT/.upstream"
mkdir -p "$UP"

GROCERY_SHA=ef8fa67ae9c6e3f21c7977800b674f3a7c69edb0
EVIDENCE_SHA=a0f9025fe54b391ad42f5c908812ee848668a4ae

fetch() {  # fetch <dir> <url> <sha>
  local dir="$1" url="$2" sha="$3"
  if [ ! -d "$dir/.git" ]; then
    git init -q "$dir"
    git -C "$dir" remote add origin "$url"
  fi
  if ! git -C "$dir" cat-file -e "$sha^{commit}" 2>/dev/null; then
    GIT_LFS_SKIP_SMUDGE=1 git -C "$dir" fetch -q --depth 1 origin "$sha"
  fi
  git -C "$dir" checkout -q --detach "$sha"
  echo "$(basename "$dir") @ $(git -C "$dir" rev-parse --short HEAD)"
}

fetch "$UP/grocery_optimizer" https://github.com/hemangnagar/grocery_optimizer "$GROCERY_SHA"
fetch "$UP/model-evidence" https://github.com/hemangnagar/model-evidence "$EVIDENCE_SHA"

# grocery_optimizer needs Python >= 3.12 and heavy deps (duckdb, pandas, playwright): isolate it in its own venv,
# installed editable so its DuckDB file lands in the clone's data/ directory.
GV="$UP/grocery_optimizer/.venv"
if [ ! -x "$GV/bin/python" ]; then
  uv venv -q --python 3.12 "$GV"
fi
uv pip install -q -p "$GV/bin/python" -e "$UP/grocery_optimizer"
# Seed the credential-free demo dataset (4 stores x 26 items). Idempotent.
(cd "$UP/grocery_optimizer" && "$GV/bin/grocery-init-db" >/dev/null && "$GV/bin/grocery-seed-demo" | tail -1)

command -v node >/dev/null || { echo "node (>=22) is required for the model_evidence provider" >&2; exit 1; }
echo "upstream ready: $UP"
