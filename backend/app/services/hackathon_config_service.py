from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


try:
    _YamlLoader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover - minimal PyYAML builds
    _YamlLoader = yaml.SafeLoader


@dataclass(frozen=True)
class HackathonConfig:
    leaderboard_secret: str = ""


def load_hackathon_config(path: Path) -> HackathonConfig:
    if not path.is_file():
        return HackathonConfig()
    raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_YamlLoader) or {}
    values = raw.get("hackathon", {}) if isinstance(raw, dict) else {}
    if not isinstance(values, dict):
        return HackathonConfig()
    return HackathonConfig(
        leaderboard_secret=str(values.get("leaderboard_secret") or "").strip(),
    )
