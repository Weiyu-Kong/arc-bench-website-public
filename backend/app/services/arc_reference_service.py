"""Package the local ARC source for the common platform runner contract."""
from __future__ import annotations

import copy
from pathlib import Path, PurePosixPath
import zipfile


ARC_ENTRYPOINT = '''from __future__ import annotations
import os
import sys
from arc_main import main as arc_main

def main() -> None:
    arguments = sys.argv[1:]
    if arguments and arguments[0] == "compile":
        arguments = arguments[1:]
    if not any(arg == "--type" or arg.startswith("--type=") or arg == "-t" for arg in arguments):
        arguments.extend(["--type", os.environ.get("ARCBENCH_TASK_TYPE", "web")])
    sys.argv = [sys.argv[0], "compile", *arguments]
    arc_main()

if __name__ == "__main__":
    main()
'''

EXCLUDED_PARTS = {
    ".git", ".arc", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "node_modules", "build", "dist", "coverage",
}


def add_arc_reference(archive: zipfile.ZipFile, configured_source: Path, *, templates_root: Path) -> None:
    """Prefer unpacked sibling source; retain ZIP-only deployments."""
    configured_source = Path(configured_source).resolve()
    source_root = configured_source if configured_source.is_dir() else configured_source.with_suffix("")
    templates_root = Path(templates_root).resolve()
    if not templates_root.is_dir():
        raise FileNotFoundError(f"ARC application templates not found: {templates_root}")
    root_files: set[str] = set()
    if source_root.is_dir():
        for name in ("main.py", "requirements.txt"):
            if not (source_root / name).is_file():
                raise ValueError(f"ARC source is missing {name}: {source_root}")
        for path in sorted(source_root.rglob("*")):
            relative = path.relative_to(source_root)
            if (
                not path.is_file() or path.is_symlink()
                or not path.resolve().is_relative_to(source_root)
                or set(relative.parts) & EXCLUDED_PARTS
                or relative.parts[0] in {"template", "arc-template"}
                or relative.name == ".env" or relative.name.startswith(".env.")
                or relative.suffix in {".pyc", ".pyo"}
            ):
                continue
            name = "arc_main.py" if relative.as_posix() == "main.py" else relative.as_posix()
            archive.write(path, arcname=name)
    elif configured_source.is_file():
        with zipfile.ZipFile(configured_source) as source:
            for member in source.infolist():
                relative = PurePosixPath(member.filename)
                if (member.is_dir() or relative.is_absolute() or ".." in relative.parts
                        or not relative.parts or relative.parts[0] in {"template", "arc-template"}):
                    continue
                if len(relative.parts) == 1:
                    root_files.add(relative.name)
                output_info = copy.copy(member)
                output_info.filename = "arc_main.py" if relative.as_posix() == "main.py" else relative.as_posix()
                archive.writestr(output_info, source.read(member))
        missing = {"main.py", "requirements.txt"} - root_files
        if missing:
            raise ValueError(f"ARC archive is missing {', '.join(sorted(missing))}")
    else:
        raise FileNotFoundError(f"ARC source directory or archive not found: {source_root}, {configured_source}")
    # ARC resolves scaffolds relative to its source, not the output workspace.
    # Bundle the site's current application templates for both runtime modes.
    for path in sorted(templates_root.rglob("*")):
        relative = path.relative_to(templates_root)
        if (not path.is_file() or path.is_symlink()
                or not path.resolve().is_relative_to(templates_root)
                or set(relative.parts) & EXCLUDED_PARTS
                or relative.name == ".env" or relative.name.startswith(".env.")
                or relative.suffix in {".pyc", ".pyo"}):
            continue
        archive.write(path, arcname=f"arc-template/templates/{relative.as_posix()}")
    archive.writestr("main.py", ARC_ENTRYPOINT)
