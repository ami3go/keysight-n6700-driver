#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
pytest tests/conformance -v "$@"
