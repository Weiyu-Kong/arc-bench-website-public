from functools import lru_cache
import math
import os
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
import yaml


ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "ArcBench API"
    api_prefix: str = "/api"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 1
    # Deliberately empty: production must explicitly configure PostgreSQL.
    database_url: str = ""
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    frontend_dist: Path = ROOT_DIR / "frontend" / "dist"
    site_assets_root: Path = ROOT_DIR / "assets"
    host_preview_public_url: str | None = None
    host_preview_host: str = "127.0.0.1"
    host_preview_port: int = 3000
    demo_preview_public_url_template: str = "https://steps-{step:04d}.arc-bench.com"
    demo_preview_health_host: str = "127.0.0.1"
    demo_preview_port_start: int = 3001
    preview_gateway_base_url: str | None = None
    previewer_public_base_url: str | None = None
    previewer_public_host: str = "127.0.0.1"
    previewer_bind_host: str = "0.0.0.0"
    previewer_port_start: int = 31000
    previewer_port_end: int = 31031
    previewer_concurrency: int = 32
    previewer_workspace_root: Path = ROOT_DIR / "runtime" / "previewer-workspaces"
    previewer_identity_path: Path = ROOT_DIR / "runtime" / "previewer-id"
    previewer_id: str = ""
    previewer_lease_seconds: int = 90
    previewer_heartbeat_seconds: int = 20
    preview_startup_timeout_seconds: int = 180
    preview_idle_timeout_seconds: int = 1800
    preview_absolute_expiry_seconds: int = 14400
    data_root: Path = ROOT_DIR / "data"
    arc_bench_root: Path = data_root / "arc-bench"
    playground_root: Path = data_root / "playground"
    competition_root: Path = data_root / "competition"
    arc_template_root: Path = ROOT_DIR / "arc-template" / "templates"
    web_template_files_root: Path = arc_template_root / "web-react-express"
    mobile_template_files_root: Path = arc_template_root / "mobile-android-java"
    cli_template_files_root: Path = arc_template_root / "cli-python"
    requirements_root: Path = arc_bench_root / "web"
    tests_root: Path = arc_bench_root / "web"
    playground_requirements_root: Path = playground_root / "web"
    playground_tests_root: Path = playground_root / "web"
    demo_agent_zip: Path = ROOT_DIR / "runtime" / "demo-agent.zip"
    reference_implementations_root: Path = ROOT_DIR / "reference-implementations"
    # ARC uses the unpacked sibling arc/ source when present, plus arc_template_root.
    agent_reference_arc_archive: Path = reference_implementations_root / "arc.zip"
    agent_reference_octos_archive: Path = reference_implementations_root / "octos.zip"
    agent_reference_blank_archive: Path = reference_implementations_root / "blank.zip"
    agent_reference_codex_archive: Path = reference_implementations_root / "codex.zip"
    # Kept for local source development and backwards compatibility. Production
    # template generation uses the immutable codex.zip archive above.
    agent_reference_codex_root: Path = ROOT_DIR / "reference-implementations" / "codex"
    agent_starter_template_root: Path = ROOT_DIR / "packages"
    agent_runtime_package_root: Path = ROOT_DIR / "packages" / "shared" / "arcbench-agent-runtime"
    agent_skills_package_root: Path = ROOT_DIR / "packages" / "shared" / "skills"
    user_submissions_root: Path = ROOT_DIR / "runtime" / "user-submissions"
    user_tasks_root: Path = ROOT_DIR / "runtime" / "user-tasks"
    # Shared storage is partitioned by stable user id. Executors use the
    # local workspace root for high-frequency Docker I/O.
    shared_storage_root: Path = ROOT_DIR / "runtime" / "shared"
    executor_workspace_root: Path = ROOT_DIR / "runtime" / "executor-workspaces"
    executor_workspace_host_root: Path | None = None
    storage_bucket_count: int = 32
    log_sync_interval_seconds: int = 5
    preview_source_sync_interval_seconds: int = 10
    log_sync_max_bytes: int = 512 * 1024
    log_max_bytes_per_run: int = 100 * 1024 * 1024
    runner_context_dir: Path = ROOT_DIR
    runner_dockerfile: str = "backend/runner/Dockerfile"
    runner_image: str = "arcbench-runner:latest"
    runner_build_on_demand: bool = False
    runtime_config_path: Path = ROOT_DIR / "config.yaml"
    session_secret: str = "arcbench-dev-session-secret"
    secure_cookies: bool = False
    email_verification_enabled: bool = False
    resend_api_key: str = ""
    email_from: str = "ARC-Bench <noreply@auth.arc-bench.com>"
    email_code_ttl_seconds: int = 600
    email_resend_interval_seconds: int = 60
    email_daily_send_limit: int = 90
    admin_api_token: str = ""
    admin_emails: list[str] = []
    executor_identity_path: Path = ROOT_DIR / "runtime" / "executor-id"
    executor_id: str = ""
    executor_health_interval_seconds: int = 5
    executor_registry_ttl_seconds: int = 20
    executor_completed_task_limit: int = 100
    executor_completed_task_ttl_seconds: int = 86400
    executor_shutdown_timeout_seconds: int = 21600
    redis_url: str = "redis://127.0.0.1:6379/0"
    worker_soft_time_limit_seconds: int = 1900
    worker_hard_time_limit_seconds: int = 2000
    max_concurrent_runs: int = 32
    recovery_interval_seconds: int = 30
    recovery_max_attempts: int = 3
    db_pool_size: int = 2
    db_max_overflow: int = 2
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800

    # Limit each evaluation Runner to one CPU by default. Operators can raise
    # this per Executor with ARCBENCH_RUNNER_CPU_LIMIT when capacity allows.
    runner_cpu_limit: int = 1
    runner_memory_limit: str = "4g"
    runner_timeout_seconds: int = 0
    agent_health_timeout_seconds: int = 90
    runner_network_mode: str | None = None
    runner_extra_hosts: str | None = None
    # UID:GID used inside the bind-mounted runner container. Empty means the
    # UID:GID of the API/Celery process, keeping workspace files writable.
    runner_user: str = ""
    meter_base_url: str = ""
    meter_admin_base_url: str = ""
    meter_admin_token: str = ""
    meter_bridge_secret: str = ""
    meter_bridge_token_ttl_seconds: int = 120
    meter_usage_enabled: bool = False
    meter_usage_timeout_seconds: float = 10.0
    meter_usage_settle_timeout_seconds: float = 15.0
    # Keep one day of headroom under the meter API's 31-day maximum so the
    # same fixed window can be reused by both snapshots of a long run.
    meter_usage_window_seconds: int = 30 * 24 * 60 * 60
    model_proxy_enabled: bool = False
    # Full /api/model-proxy/v1 URL reachable from runner containers.
    model_proxy_base_url: str = ""
    model_proxy_pricing_path: Path = Path(__file__).resolve().parents[1] / "resources" / "model_pricing.public.yaml"
    # Zero disables proxy-imposed idle/total request deadlines.
    model_proxy_read_timeout_seconds: float = 0
    model_proxy_request_timeout_seconds: float = 0
    model_proxy_max_body_bytes: int = 16 * 1024 * 1024
    model_proxy_max_response_bytes: int = 32 * 1024 * 1024
    model_proxy_max_event_bytes: int = 4 * 1024 * 1024
    model_proxy_max_concurrent: int = 64
    model_proxy_max_concurrent_per_run: int = 8
    # Some OpenAI-compatible upstreams reject stream_options. Preserve explicit
    # caller settings; operators may disable automatic usage-option injection.
    model_proxy_request_stream_usage: bool = True
    leaderboard_efficiency_threshold: float = 80.0
    pip_index_url: str = "https://pypi.tuna.tsinghua.edu.cn/simple"
    pip_trusted_host: str = "pypi.tuna.tsinghua.edu.cn"
    # pip treats index URLs as a combined candidate pool. Keep the regional mirror
    # as the primary source and include PyPI for packages not mirrored there.
    # Multiple extra indexes may be supplied as a comma- or whitespace-separated list.
    pip_extra_index_url: str | None = "https://pypi.org/simple"

    model_config = SettingsConfigDict(env_prefix="ARCBENCH_", case_sensitive=False, extra="ignore")

    @model_validator(mode="after")
    def validate_previewer_settings(self) -> "Settings":
        if "user_tasks_root" not in self.model_fields_set:
            self.user_tasks_root = self.shared_storage_root / "user-tasks"
        if self.api_workers <= 0:
            raise ValueError("API workers must be positive")
        if not 60 <= self.email_code_ttl_seconds <= 3600:
            raise ValueError("email_code_ttl_seconds must be between 60 and 3600")
        if not 30 <= self.email_resend_interval_seconds <= self.email_code_ttl_seconds:
            raise ValueError("email_resend_interval_seconds must be between 30 and the code TTL")
        if self.email_daily_send_limit < 1:
            raise ValueError("email_daily_send_limit must be positive")
        for name in ("read_timeout_seconds", "request_timeout_seconds"):
            value = getattr(self, f"model_proxy_{name}")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"model_proxy_{name} must be non-negative and finite; zero disables the timeout")
        for name in ("max_body_bytes",
                     "max_response_bytes", "max_event_bytes", "max_concurrent", "max_concurrent_per_run"):
            value = getattr(self, f"model_proxy_{name}")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"model_proxy_{name} must be positive and finite")
        if not 1 <= self.previewer_port_start <= 65535 or not 1 <= self.previewer_port_end <= 65535:
            raise ValueError("Previewer ports must be between 1 and 65535")
        if self.previewer_port_start > self.previewer_port_end:
            raise ValueError("Previewer port start must not exceed port end")
        port_capacity = self.previewer_port_end - self.previewer_port_start + 1
        if not 1 <= self.previewer_concurrency <= port_capacity:
            raise ValueError("Previewer concurrency must be positive and no greater than the configured port count")
        if self.previewer_heartbeat_seconds <= 0 or self.previewer_lease_seconds <= self.previewer_heartbeat_seconds * 2:
            raise ValueError("Previewer lease must exceed twice the positive heartbeat interval")
        if self.preview_startup_timeout_seconds <= 0:
            raise ValueError("Preview startup timeout must be positive")
        if self.preview_idle_timeout_seconds <= 0 or self.preview_absolute_expiry_seconds <= 0:
            raise ValueError("Preview idle and absolute expiry values must be positive")
        if (
            self.executor_completed_task_limit <= 0
            or self.executor_completed_task_ttl_seconds <= 0
            or self.executor_shutdown_timeout_seconds <= 0
        ):
            raise ValueError("Executor retention and shutdown settings must be positive")
        return self

    @staticmethod
    def _split_csv(value: str | None) -> list[str]:
        if value is None:
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    def get_runner_extra_hosts(self) -> dict[str, str]:
        entries: dict[str, str] = {}
        for item in self._split_csv(self.runner_extra_hosts):
            host, separator, target = item.partition(":")
            normalized_host = host.strip()
            normalized_target = target.strip()
            if separator and normalized_host and normalized_target:
                entries[normalized_host] = normalized_target
        return entries

    def get_pip_extra_index_urls(self) -> list[str]:
        value = (self.pip_extra_index_url or "").replace(",", " ")
        return [item.strip() for item in value.split() if item.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    config_payload: dict[str, object] = {}
    config_path = settings.runtime_config_path
    if config_path.is_file():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(f"Application configuration is invalid: {config_path}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Application configuration must be a YAML mapping: {config_path}")
        config_payload = payload

    admin = config_payload.get("admin") or {}
    if admin and not isinstance(admin, dict):
        raise RuntimeError(f"Application admin configuration must be a YAML mapping: {config_path}")
    configured_admin_token = str(admin.get("api_token") or "").strip() if isinstance(admin, dict) else ""
    configured_admin_emails = admin.get("emails", []) if isinstance(admin, dict) else []
    if configured_admin_emails is None:
        configured_admin_emails = []
    if not isinstance(configured_admin_emails, list) or not all(isinstance(item, str) for item in configured_admin_emails):
        raise RuntimeError(f"Application admin emails must be a YAML list: {config_path}")
    meter = config_payload.get("meter") or {}
    if meter and not isinstance(meter, dict):
        raise RuntimeError(f"Application meter configuration must be a YAML mapping: {config_path}")

    def configured(name: str, current):
        env_name = f"ARCBENCH_{name.upper()}"
        if os.environ.get(env_name) is not None:
            return current
        return meter.get(name, current) if isinstance(meter, dict) else current

    shared_updates = {
        "meter_base_url": str(configured("meter_base_url", settings.meter_base_url) or "").strip(),
        "meter_admin_base_url": str(configured("meter_admin_base_url", settings.meter_admin_base_url) or "").strip(),
        "meter_admin_token": str(configured("meter_admin_token", settings.meter_admin_token) or "").strip(),
    }
    proxy = config_payload.get("model_proxy") or {}
    if not isinstance(proxy, dict):
        raise RuntimeError("model_proxy must be a YAML mapping")
    for name in Settings.model_fields:
        if name.startswith("model_proxy_") and f"ARCBENCH_{name.upper()}" not in os.environ:
            key = name.removeprefix("model_proxy_")
            if key in proxy:
                shared_updates[name] = proxy[key]
    email = config_payload.get("email_verification") or {}
    if not isinstance(email, dict):
        raise RuntimeError("email_verification must be a YAML mapping")
    email_fields = {
        "enabled": "email_verification_enabled", "resend_api_key": "resend_api_key",
        "from": "email_from", "code_ttl_seconds": "email_code_ttl_seconds",
        "resend_interval_seconds": "email_resend_interval_seconds",
        "daily_send_limit": "email_daily_send_limit",
    }
    for key, name in email_fields.items():
        if key in email and f"ARCBENCH_{name.upper()}" not in os.environ:
            shared_updates[name] = email[key]
    # model_copy does not validate updates. Validate YAML booleans/numbers for
    # both proxy and email settings without resolving another DB configuration.
    validated_settings = Settings.model_validate({**settings.model_dump(), **shared_updates})
    shared_updates.update({name: getattr(validated_settings, name) for name in Settings.model_fields
                           if name.startswith("model_proxy_") or name in email_fields.values()})

    # Environment variables remain the highest-priority production override.
    environment_url = os.environ.get("ARCBENCH_DATABASE_URL", "").strip()
    if environment_url:
        return settings.model_copy(
            update={
                **shared_updates,
                "database_url": _require_postgresql_url(environment_url),
                "admin_api_token": os.environ.get("ARCBENCH_ADMIN_API_TOKEN", "").strip() or configured_admin_token,
                "admin_emails": [item.strip().casefold() for item in configured_admin_emails if item.strip()],
            }
        )

    if not config_path.is_file():
        raise RuntimeError(
            "PostgreSQL configuration is missing. Set ARCBENCH_DATABASE_URL "
            "or add database_url to config.yaml. SQLite is disabled."
        )
    database_url = str(config_payload.get("database_url") or "").strip()
    if not database_url:
        raise RuntimeError(
            f"PostgreSQL database_url is missing from {config_path}. "
            "Set ARCBENCH_DATABASE_URL or add database_url to config.yaml. "
            "SQLite is disabled."
        )
    return settings.model_copy(
        update={
            **shared_updates,
            "database_url": _require_postgresql_url(database_url),
            "admin_api_token": os.environ.get("ARCBENCH_ADMIN_API_TOKEN", "").strip() or configured_admin_token,
            "admin_emails": [item.strip().casefold() for item in configured_admin_emails if item.strip()],
        }
    )


def _require_postgresql_url(value: str) -> str:
    """Validate that the configured URL targets PostgreSQL, never SQLite."""
    try:
        url = make_url(value)
    except Exception as exc:  # SQLAlchemy raises multiple URL-specific exceptions.
        raise RuntimeError("Invalid database URL. Expected a PostgreSQL URL.") from exc
    if url.get_backend_name() != "postgresql":
        raise RuntimeError(
            "ARC-Bench requires PostgreSQL. Configure a postgresql+psycopg:// "
            "URL via ARCBENCH_DATABASE_URL or config.yaml; SQLite is disabled."
        )
    if not url.database:
        raise RuntimeError("PostgreSQL database URL must include a database name.")
    return value
