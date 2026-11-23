from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


try:
    _YamlLoader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover - minimal PyYAML builds
    _YamlLoader = yaml.SafeLoader


DEFAULT_OPENAI_BASE_URL = "https://api.arc-bench.com/v1"


@dataclass(frozen=True)
class ModelProvider:
    openai_api_key: str
    openai_base_url: str
    model: str


class ModelProviderService:
    """Resolve user-owned API credentials without selecting model names."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = Path(config_path)
        self._payload = self._load_payload()

    @property
    def default_openai_base_url(self) -> str:
        defaults = self._optional_mapping(self._payload.get("model_defaults"))
        return str(defaults.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL).strip()

    def resolve_model(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
        model: str | None,
        require_api_key: bool = True,
    ) -> ModelProvider:
        normalized_api_key = str(api_key or "").strip()
        normalized_base_url = str(base_url or "").strip() or self.default_openai_base_url
        normalized_model = str(model or "").strip()
        if require_api_key and not normalized_api_key:
            raise ValueError("API key is required")
        parsed_url = urlparse(normalized_base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Base URL must be an absolute HTTP(S) URL")
        if len(normalized_base_url) > 1024:
            raise ValueError("Base URL must be 1024 characters or fewer")
        if len(normalized_model) > 120:
            raise ValueError("Model must be 120 characters or fewer")
        if len(normalized_api_key) > 4096:
            raise ValueError("API key must be 4096 characters or fewer")
        return ModelProvider(
            openai_api_key=normalized_api_key,
            openai_base_url=normalized_base_url,
            model=normalized_model,
        )

    def build_container_environment(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        visual_model: str | None = None,
    ) -> dict[str, str]:
        provider = self.resolve_model(api_key=api_key, base_url=base_url, model=model)
        return {
            "OPENAI_API_KEY": provider.openai_api_key,
            "OPENAI_BASE_URL": provider.openai_base_url,
            "VISUAL_API_KEY": provider.openai_api_key,
            "VISUAL_BASE_URL": provider.openai_base_url,
            "ARC_DEBUG": "1",
        }

    def get_runner_dns_servers(self) -> list[str]:
        environment = self._optional_mapping(self._payload.get("environment"))
        raw_value = environment.get("ARCBENCH_RUNNER_DNS_SERVERS", [])
        if isinstance(raw_value, list):
            return [str(item).strip() for item in raw_value if str(item).strip()]
        return [item.strip() for item in str(raw_value or "").replace(",", " ").split() if item.strip()]

    def _load_payload(self) -> dict[str, Any]:
        if not self.config_path.is_file():
            raise RuntimeError(
                f"Model provider configuration is missing: {self.config_path}. "
                "Copy config.example.yaml to config.yaml and configure the API connection."
            )
        try:
            payload = yaml.load(self.config_path.read_text(encoding="utf-8"), Loader=_YamlLoader)
        except yaml.YAMLError as exc:
            raise RuntimeError(f"Model provider configuration is invalid YAML: {self.config_path}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Model provider configuration must be a YAML mapping: {self.config_path}")
        return payload

    @staticmethod
    def _optional_mapping(value: object) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}
