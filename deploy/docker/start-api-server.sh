#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f source.sh ]]; then
    printf 'Missing source.sh in %s\n' "$ROOT_DIR" >&2
    printf 'Create it from source.example.sh and fill in the deployment settings.\n' >&2
    exit 1
fi

source "$ROOT_DIR/source.sh"
exec uv run arcbench-api
