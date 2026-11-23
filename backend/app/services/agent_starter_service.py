from __future__ import annotations

import copy
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import zipfile

from app.core.config import get_settings
from app.services.arc_reference_service import ARC_ENTRYPOINT, add_arc_reference


class AgentStarterService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.template_root = self.settings.agent_starter_template_root

    def build_bundle(
        self,
        *,
        task_type: str,
        language: str = "python",
        template_kind: str = "blank",
        evolution_templates: dict[str, tuple[Path | None, dict[str, object]]] | None = None,
    ) -> tuple[bytes, str]:
        normalized_kind = self._normalize_template_kind(template_kind)
        language_root, normalized_language = self._resolve_language_template_root(language)
        if normalized_kind != "blank" and normalized_language != "python":
            raise ValueError(f"The {normalized_kind} agent template is only available for Python.")

        memory_file = BytesIO()
        with zipfile.ZipFile(memory_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            include_default_application = not evolution_templates
            if normalized_kind == "blank":
                if normalized_language == "python" and self.settings.agent_reference_blank_archive.is_file():
                    self._add_source_archive(archive, self.settings.agent_reference_blank_archive)
                else:
                    if not language_root.is_dir():
                        raise FileNotFoundError(f"Agent starter template root not found: {language_root}")
                    self._add_starter_template_sources(archive, language_root)
                if include_default_application:
                    self._add_task_template(archive, self._resolve_template_root(task_type))
            else:
                self._add_reference_agent_template(
                    archive,
                    normalized_kind,
                    task_type,
                    include_task_template=include_default_application,
                )
            if evolution_templates:
                self._add_evolution_templates(archive, task_type, evolution_templates)
        return memory_file.getvalue(), f"agent-{normalized_kind.replace('_', '-')}-based.zip"

    def _add_evolution_templates(
        self,
        archive: zipfile.ZipFile,
        task_type: str,
        templates: dict[str, tuple[Path | None, dict[str, object]]],
    ) -> None:
        manifest: dict[str, object] = {"format_version": 1, "tasks": {}}
        single_task = len(templates) == 1
        for task_slug, (source_archive, metadata) in sorted(templates.items()):
            safe_slug = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in task_slug)
            archive_name = f"evolution-templates/{safe_slug}.zip"
            if source_archive is None:
                payload = self._build_project_archive(self._resolve_template_root(task_type))
            else:
                payload = source_archive.read_bytes()
            if single_task:
                self._add_project_archive(archive, payload, "template")
                packaged_path = "template/"
            else:
                archive.writestr(archive_name, payload)
                packaged_path = archive_name
            manifest["tasks"][task_slug] = {**metadata, "archive": packaged_path}
        archive.writestr(
            "evolution-templates/manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        archive.writestr(
            "evolution-templates/README.md",
            "# Evolution application baselines\n\n"
            "A single-task competition places its application directly in `template/`. "
            "A multi-task competition stores one application ZIP per task in this directory. "
            "A blank scaffold is included when no eligible previous-stage run exists. "
            "ARC-Bench verifies and injects the authoritative baseline again when the uploaded agent runs.\n",
        )

    def _build_project_archive(self, source_root: Path) -> bytes:
        if not source_root.is_dir():
            raise FileNotFoundError(f"Application template root not found: {source_root}")
        memory_file = BytesIO()
        with zipfile.ZipFile(memory_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source_root.rglob("*")):
                if path.is_dir() or self._should_exclude_starter_path(path, source_root):
                    continue
                archive.write(path, arcname=path.relative_to(source_root).as_posix())
        return memory_file.getvalue()

    @staticmethod
    def _add_project_archive(destination: zipfile.ZipFile, payload: bytes, archive_root: str) -> None:
        with zipfile.ZipFile(BytesIO(payload), "r") as source:
            for member in source.infolist():
                relative = PurePosixPath(member.filename)
                if member.is_dir() or relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    continue
                output_info = copy.copy(member)
                output_info.filename = f"{archive_root.rstrip('/')}/{relative.as_posix()}"
                destination.writestr(output_info, source.read(member))

    @staticmethod
    def _normalize_template_kind(template_kind: str) -> str:
        normalized = str(template_kind or "blank").strip().lower()
        if normalized not in {"blank", "arc", "octos", "codex"}:
            raise ValueError(f"Unsupported agent template: {template_kind}")
        return normalized

    def _add_reference_agent_template(
        self,
        archive: zipfile.ZipFile,
        template_kind: str,
        task_type: str,
        *,
        include_task_template: bool = True,
    ) -> None:
        """Add a runnable reference agent at the archive root.

        The runner always invokes ``/workspace/submission/main.py``.  Reference
        agents therefore cannot be shipped as files below ``template/<kind>``:
        that layout leaves the blank starter as the active entry point.
        """
        if template_kind == "arc":
            self._add_arc_agent_template(archive, task_type, include_task_template=include_task_template)
            return
        if template_kind == "octos":
            self._add_source_archive(archive, self.settings.agent_reference_octos_archive)
            if include_task_template:
                self._add_task_template(archive, self._resolve_template_root(task_type))
            return

        if template_kind == "codex":
            self._add_source_archive(archive, self.settings.agent_reference_codex_archive)
            if include_task_template:
                self._add_task_template(archive, self._resolve_template_root(task_type))
            return

        raise ValueError(f"Unsupported agent template: {template_kind}")

    def _add_arc_agent_template(
        self,
        archive: zipfile.ZipFile,
        task_type: str,
        *,
        include_task_template: bool = True,
    ) -> None:
        add_arc_reference(
            archive, self.settings.agent_reference_arc_archive,
            templates_root=self.settings.arc_template_root,
        )
        if include_task_template:
            self._add_task_template(archive, self._resolve_template_root(task_type))

    def _add_source_archive(
        self,
        destination: zipfile.ZipFile,
        source_path: Path,
        *,
        rename_root_main: str | None = None,
    ) -> None:
        if not source_path.is_file():
            raise FileNotFoundError(
                f"Reference implementation archive not found: {source_path}. "
                "Run scripts/update_reference_implementations.py first."
            )
        root_files: set[str] = set()
        try:
            with zipfile.ZipFile(source_path, "r") as source:
                for member in source.infolist():
                    normalized = PurePosixPath(member.filename)
                    if (
                        member.is_dir()
                        or normalized.is_absolute()
                        or ".." in normalized.parts
                        or not normalized.parts
                        or normalized.parts[0] == "template"
                    ):
                        continue
                    archive_name = normalized.as_posix()
                    if len(normalized.parts) == 1:
                        root_files.add(normalized.name)
                    if rename_root_main and normalized == PurePosixPath("main.py"):
                        archive_name = rename_root_main
                    output_info = copy.copy(member)
                    output_info.filename = archive_name
                    destination.writestr(output_info, source.read(member))
        except zipfile.BadZipFile as exc:
            raise RuntimeError(f"Reference implementation archive is invalid: {source_path}") from exc
        missing = {"main.py", "requirements.txt"} - root_files
        if missing:
            raise FileNotFoundError(
                f"Reference implementation archive {source_path} is missing {', '.join(sorted(missing))}"
            )

    @staticmethod
    def _build_arc_entrypoint() -> str:
        return ARC_ENTRYPOINT

    def _resolve_language_template_root(self, language: str) -> tuple[Path, str]:
        normalized = str(language or "").strip().lower()
        aliases = {
            "js": "javascript",
            "node": "javascript",
            "nodejs": "javascript",
            "ts": "typescript",
            "py": "python",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"python", "javascript", "typescript"}:
            raise ValueError(f"Unsupported agent template language: {language}")
        return self.template_root / normalized, normalized

    def _add_starter_template_sources(self, archive: zipfile.ZipFile, source_root: Path) -> None:
        for path in sorted(source_root.rglob("*")):
            if path.is_dir() or self._should_exclude_starter_path(path, source_root):
                continue
            relative_path = path.relative_to(source_root)
            archive.write(path, arcname=relative_path.as_posix())

    def _should_exclude_starter_path(self, path: Path, source_root: Path) -> bool:
        relative_path = path.relative_to(source_root)
        excluded_parts = {
            ".git",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "node_modules",
            "target",
            "venv",
        }
        excluded_files = {".DS_Store", ".env", ".env.local", ".env.development", ".env.production"}
        return (
            bool(set(relative_path.parts) & excluded_parts)
            or relative_path.name in excluded_files
            or relative_path.suffix == ".pyc"
        )

    def _add_task_template(self, archive: zipfile.ZipFile, template_root: Path) -> None:
        if not template_root.is_dir():
            raise FileNotFoundError(f"Task template directory not found: {template_root}")
        for path in sorted(template_root.rglob("*")):
            if path.is_dir():
                continue
            relative_path = path.relative_to(template_root)
            archive.write(path, arcname=f"template/{relative_path.as_posix()}")

    def _resolve_template_root(self, task_type: str) -> Path:
        normalized = str(task_type or "").strip().lower()
        if normalized == "cli":
            return self.settings.cli_template_files_root
        if normalized in {"mobile", "android"}:
            return self.settings.mobile_template_files_root
        return self.settings.web_template_files_root
