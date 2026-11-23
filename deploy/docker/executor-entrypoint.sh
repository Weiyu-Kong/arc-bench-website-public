#!/usr/bin/env bash
set -euo pipefail

if [[ -f /app/source.sh ]]; then
    # Node-local settings are injected by Compose and are absent from this file.
    # shellcheck disable=SC1091
    source /app/source.sh
fi

exec arcbench-executor
