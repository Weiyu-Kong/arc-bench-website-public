#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

cd "$ROOT_DIR"

docker compose -f "$SCRIPT_DIR/compose.yaml" build frontend migrate api executor previewer runner-check
docker compose -f "$SCRIPT_DIR/compose.yaml" run --rm --no-deps runner-check
