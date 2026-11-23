"""Load the site's public CNY token prices without a Meter dependency."""
from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache(maxsize=8)
def _parse_prices(content: bytes):
    # Preserve scalar spelling, including decimal prices, rather than passing
    # monetary values through YAML's binary float conversion.
    payload = yaml.load(content, Loader=yaml.BaseLoader)
    if not isinstance(payload, dict) or payload.get("unit") != "cny_per_1m_tokens":
        raise ValueError("Model pricing unit must be cny_per_1m_tokens")
    version = payload.get("version")
    if not isinstance(version, str) or not 1 <= len(version.strip()) <= 96:
        raise ValueError("Model pricing must contain a version of 1..96 characters")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Model pricing must contain a non-empty entries list")
    catalog = {}
    allowed = {"input", "output", "cache_read", "cache_write", "cache_write_5m", "cache_write_1h"}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("provider") != "*":
            raise ValueError("Site-wide pricing supports only provider: '*'")
        model = entry.get("model")
        if not isinstance(model, str) or not model.strip() or len(model) > 120 or model in catalog:
            raise ValueError("Model pricing IDs must be non-empty, unique and at most 120 characters")
        cost = entry.get("cost")
        if not isinstance(cost, dict) or not {"input", "output"} <= cost.keys() or cost.keys() - allowed:
            raise ValueError("Model cost must define input/output and only supported cache price fields")
        for value in cost.values():
            try:
                amount = Decimal(value)
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("Invalid model price") from exc
            if not amount.is_finite() or not Decimal(0) <= amount <= Decimal("100000000"):
                raise ValueError("Model prices must be finite and non-negative")
        # No discount or cache-write premium is invented when the public file
        # specifies no separate price: those input tokens use its input rate.
        catalog[model] = {
            "input": cost["input"],
            "output": cost["output"],
            "cache_read": cost.get("cache_read", cost["input"]),
            "cache_write_5m": cost.get("cache_write_5m", cost.get("cache_write", cost["input"])),
            "cache_write_1h": cost.get("cache_write_1h", cost.get("cache_write", cost["input"])),
        }
    # Snapshot identity changes even if an operator forgets to bump `version`.
    identity = f"{version.strip()}:{hashlib.sha256(content).hexdigest()[:16]}"
    return identity, catalog


class ModelPricingService:
    def __init__(self, path: Path):
        self.path = Path(path)

    def models(self) -> list[str]:
        _, catalog = _parse_prices(self.path.read_bytes())
        return sorted(catalog)

    def prices(self, model: str):
        version, catalog = _parse_prices(self.path.read_bytes())
        prices = catalog.get(model)
        return version, dict(prices) if prices is not None else None
