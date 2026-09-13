#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -z "${1:-}" ]; then
  echo "usage: $0 <examples/python/*.py>" >&2
  exit 1
fi
python "$1"
