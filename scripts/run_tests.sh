#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
pytest --cov=keysight_n6700 --cov-report=term-missing "$@"
