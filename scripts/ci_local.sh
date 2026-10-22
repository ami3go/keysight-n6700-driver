#!/usr/bin/env bash
# Run the GitHub Actions "ci" job locally.
#
# Usage:
#   scripts/ci_local.sh [pytest args...]
#
# Set PYTHON to select the interpreter, e.g.:
#   PYTHON=.venv/bin/python scripts/ci_local.sh
#
# This mirrors .github/workflows/ci.yml: Ruff, both mypy targets, pytest with
# coverage, structure validation, and generated-artifact freshness.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"

run() {
  printf '\n== %s\n' "$*"
  "$@"
}

run "$PYTHON" -m ruff check src adapters tests scripts
run "$PYTHON" -m mypy src/keysight_n6700
run "$PYTHON" -m mypy --ignore-missing-imports \
  adapters/robotframework/keysight_n6700_robotframework_adapter
run "$PYTHON" -m pytest --timeout=30 --timeout-method=thread \
  --cov=keysight_n6700 --cov-report=term-missing "$@"
run "$PYTHON" scripts/validate_structure.py

printf '\n== generated artifact freshness\n'
"$PYTHON" scripts/generate_conformance_inventory.py
"$PYTHON" scripts/generate_capability_model.py
"$PYTHON" scripts/generate_ai_contract.py

artifacts=(
  tests/conformance/data/method_inventory.yaml
  capability/capability_model.yaml
  capability/examples/capability_snapshot.json
  ai/ai_contract.yaml
)
if ! git diff --exit-code -- "${artifacts[@]}"; then
  echo "Generated LPDS artifacts are stale; commit the regenerated files." >&2
  exit 1
fi

# ai_contract.lock's generated_at timestamp changes on every run. When the
# content artifacts above are fresh the contract hash is unchanged, so restore
# the committed lock instead of leaving a timestamp-only diff behind.
git checkout -- ai/ai_contract.lock 2>/dev/null || true

printf '\nAll local CI checks passed.\n'
