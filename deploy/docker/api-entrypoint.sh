#!/usr/bin/env bash
set -euo pipefail

if [[ -f /app/source.sh ]]; then
    # Cluster-wide settings are baked into the private deployment image.
    # shellcheck disable=SC1091
    source /app/source.sh
fi

exec "$@"
