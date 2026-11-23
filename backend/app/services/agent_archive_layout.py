"""Resolve an agent package root without searching beyond immediate children."""

from pathlib import Path

from app.core.enums import RuntimeType


def required_agent_root_files(runtime: RuntimeType | str) -> tuple[str, ...]:
    if runtime == RuntimeType.PYTHON:
        return ("main.py", "requirements.txt")
    if runtime in {RuntimeType.JAVASCRIPT, RuntimeType.NODEJS}:
        return ("index.js", "package.json")
    if runtime == RuntimeType.TYPESCRIPT:
        return ("index.ts", "package.json")
    raise ValueError(f"Unsupported runtime: {runtime}")


def resolve_agent_root(members: list[Path], runtime: RuntimeType | str) -> Path:
    required = set(required_agent_root_files(runtime))
    root_files = {member.name for member in members if len(member.parts) == 1}
    if required <= root_files:
        return Path(".")

    children: dict[str, set[str]] = {}
    for member in members:
        if member.is_absolute() or ".." in member.parts:
            raise ValueError("Uploaded zip contains an invalid agent path")
        if len(member.parts) == 2:
            children.setdefault(member.parts[0], set()).add(member.name)
    candidates = sorted(name for name, files in children.items() if required <= files)
    if len(candidates) > 1:
        raise ValueError("Uploaded zip contains multiple agent directories")
    if candidates:
        return Path(candidates[0])

    missing = sorted(required - root_files)
    raise ValueError(f"Uploaded zip must include {', '.join(missing)} at the archive root")
