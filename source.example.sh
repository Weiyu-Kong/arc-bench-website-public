#!/usr/bin/env bash
# Copy to source.sh, replace cluster-wide values, then run: source ./source.sh
# Per-node Executor and Previewer settings live in deploy/docker/compose*.yaml.

export ARCBENCH_DATABASE_URL='postgresql+psycopg://arcbench:URL_ENCODED_PASSWORD@127.0.0.1:5432/arcbench?sslmode=prefer'
export ARCBENCH_REDIS_URL='redis://:URL_ENCODED_REDIS_PASSWORD@127.0.0.1:6379/0'
export ARCBENCH_SESSION_SECRET='CHANGE_ME_LONG_RANDOM_SESSION_SECRET'
export ARCBENCH_SECURE_COOKIES='false'
# Email verification can also be configured in config.yaml. Keep the real key private.
# export ARCBENCH_EMAIL_VERIFICATION_ENABLED='true'
# export ARCBENCH_RESEND_API_KEY='re_REPLACE_WITH_YOUR_KEY'
# export ARCBENCH_EMAIL_FROM='ARC-Bench <noreply@auth.arc-bench.com>'
export ARCBENCH_CORS_ORIGINS='["http://localhost:5173","http://127.0.0.1:5173"]'
# Private email-to-key JSON used for Hackathon registration and profiles.
export ARCBENCH_EXECUTOR_IDENTITY_PATH='/var/lib/arcbench/executor-id'
export ARCBENCH_EXECUTOR_HEALTH_INTERVAL_SECONDS='5'
export ARCBENCH_EXECUTOR_REGISTRY_TTL_SECONDS='20'
export ARCBENCH_EXECUTOR_COMPLETED_TASK_LIMIT='100'
export ARCBENCH_EXECUTOR_COMPLETED_TASK_TTL_SECONDS='86400'
export ARCBENCH_EXECUTOR_SHUTDOWN_TIMEOUT_SECONDS='21600'
export ARCBENCH_API_HOST='0.0.0.0'
export ARCBENCH_API_PORT='8000'

# Immutable demo previews: step N is public at steps-NNNN.arc-bench.com and
# served internally by 127.0.0.1 on port 3000 + N.
export ARCBENCH_DEMO_PREVIEW_PUBLIC_URL_TEMPLATE='http://localhost:31000'
export ARCBENCH_DEMO_PREVIEW_HEALTH_HOST='127.0.0.1'
export ARCBENCH_DEMO_PREVIEW_PORT_START='3001'

# Optional only when a real routing gateway maps /<preview-id> to its node/port.
export ARCBENCH_PREVIEW_GATEWAY_BASE_URL=''

# Mount the NFS bucket namespace at the same path on API and executor hosts.
# Individual bucket directories may be separate NFS mounts, for example:
# /mnt/arcbench/bucket-00 ... /mnt/arcbench/bucket-31.
export ARCBENCH_SHARED_STORAGE_ROOT='/mnt/arcbench'
# Compose bind-mount source on the host. Set this to the NFS mount on every
# deployment host; all hosts must expose the same files through this path.
export ARCBENCH_SHARED_STORAGE_HOST_ROOT="${ARCBENCH_SHARED_STORAGE_HOST_ROOT:-/mnt/arcbench}"
export ARCBENCH_STORAGE_BUCKET_COUNT='32'

# Executor writes here; the host root identifies the corresponding path used
# by the host Docker daemon when it creates Runner containers.
export ARCBENCH_EXECUTOR_WORKSPACE_ROOT='/var/lib/arcbench/executor-workspaces'
export ARCBENCH_EXECUTOR_WORKSPACE_HOST_ROOT="${ARCBENCH_EXECUTOR_WORKSPACE_HOST_ROOT:-/var/lib/arcbench/executor-workspaces}"
export ARCBENCH_PREVIEWER_IDENTITY_PATH='/var/lib/arcbench/previewer-state/previewer-id'
export ARCBENCH_PREVIEWER_WORKSPACE_ROOT='/var/lib/arcbench/previewer-workspaces'
export ARCBENCH_LOG_SYNC_INTERVAL_SECONDS='5'
export ARCBENCH_PREVIEW_SOURCE_SYNC_INTERVAL_SECONDS='10'
export ARCBENCH_LOG_SYNC_MAX_BYTES='524288'
export ARCBENCH_LOG_MAX_BYTES_PER_RUN='104857600'

# Optional ARC Bench Meter integration. Model OPENAI_BASE_URL values in
# This is the usage-portal origin. Model relay URLs in config.yaml may use a
# different origin (for example, a dedicated api subdomain).
export ARCBENCH_METER_BASE_URL='https://meter.example.com'
export ARCBENCH_METER_USAGE_ENABLED='false'
export ARCBENCH_METER_USAGE_TIMEOUT_SECONDS='10'
export ARCBENCH_METER_USAGE_SETTLE_TIMEOUT_SECONDS='15'
export ARCBENCH_LEADERBOARD_EFFICIENCY_THRESHOLD='60'

export ARCBENCH_WORKER_SOFT_TIME_LIMIT_SECONDS='1900'
export ARCBENCH_WORKER_HARD_TIME_LIMIT_SECONDS='2000'
export ARCBENCH_RECOVERY_INTERVAL_SECONDS='30'
export ARCBENCH_RECOVERY_MAX_ATTEMPTS='3'
export ARCBENCH_DB_POOL_SIZE='2'
export ARCBENCH_DB_MAX_OVERFLOW='2'
export ARCBENCH_DB_POOL_TIMEOUT_SECONDS='30'
export ARCBENCH_DB_POOL_RECYCLE_SECONDS='1800'
export ARCBENCH_RUNNER_IMAGE="${ARCBENCH_RUNNER_IMAGE:-${ARCBENCH_IMAGE_REGISTRY:-ghcr.io/your-org}/arcbench-runner:latest}"
export ARCBENCH_RUNNER_CPU_LIMIT='1'
export ARCBENCH_RUNNER_BUILD_ON_DEMAND='true'
export ARCBENCH_RUNNER_TIMEOUT_SECONDS='0'
export ARCBENCH_AGENT_HEALTH_TIMEOUT_SECONDS='90'

export ARCBENCH_PIP_INDEX_URL='https://pypi.tuna.tsinghua.edu.cn/simple'
export ARCBENCH_PIP_TRUSTED_HOST='pypi.tuna.tsinghua.edu.cn'
export ARCBENCH_PIP_EXTRA_INDEX_URL='https://pypi.org/simple'

echo 'ARC-Bench environment loaded.'
