import shutil
import json
import os
import subprocess
import uuid
import zipfile
from pathlib import Path

from app.models.requirement import Requirement
from app.models.run import Run
from app.models.submission import Submission
from app.models.user import User
from app.core.config import get_settings
from app.services.runtime_path_service import RuntimePathService
from app.services.traceability_seed_builder import TraceabilitySeedBuilder
from app.core.enums import AgentSourceType
from app.services.agent_archive_layout import resolve_agent_root
from app.services.competition_template_service import CompetitionTemplateService


class WorkspaceAssembler:
    def __init__(self) -> None:
        self.runtime_paths = RuntimePathService()
        self.traceability_seed_builder = TraceabilitySeedBuilder()

    def assemble(
        self,
        run: Run,
        submission: Submission,
        requirement: Requirement,
        user: User,
        *,
        generation: int,
        workspace_root: Path | None = None,
    ) -> Path:
        target_root = workspace_root or self.runtime_paths.get_workspace_root(
            run,
            username=user.username,
            generation=generation,
        )
        target_root.parent.mkdir(parents=True, exist_ok=True)
        workspace_root = target_root.with_name(f".{target_root.name}.build-{uuid.uuid4().hex}")
        try:
            self._assemble_at(
                workspace_root,
                run=run,
                submission=submission,
                requirement=requirement,
                user=user,
            )
            backup_root = target_root.with_name(f".{target_root.name}.previous-{uuid.uuid4().hex}")
            if target_root.exists():
                os.replace(target_root, backup_root)
            try:
                os.replace(workspace_root, target_root)
            except Exception:
                if backup_root.exists() and not target_root.exists():
                    os.replace(backup_root, target_root)
                raise
            shutil.rmtree(backup_root, ignore_errors=True)
            return target_root
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)

    def _assemble_at(
        self,
        workspace_root: Path,
        *,
        run: Run,
        submission: Submission,
        requirement: Requirement,
        user: User,
    ) -> None:
        """Build a complete workspace outside the reader-visible path."""
        if workspace_root.exists():
            shutil.rmtree(workspace_root)
        submission_dir = workspace_root / "submission"
        template_dir = workspace_root / "template"
        tests_dir = workspace_root / "tests"
        requirements_dir = template_dir / "requirements"
        arc_dir = template_dir / ".arc"

        submission_dir.mkdir(parents=True, exist_ok=True)
        template_dir.mkdir(parents=True, exist_ok=True)
        tests_dir.mkdir(parents=True, exist_ok=True)
        arc_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(run.agent_archive_path, "r") as archive:
            archive.extractall(submission_dir)
        self._flatten_single_root(submission_dir, submission.runtime)
        if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
            self._materialize_demo_source_template(submission_dir)
        if run.template_archive_path and run.template_archive_sha256:
            CompetitionTemplateService.extract_verified(
                Path(run.template_archive_path),
                run.template_archive_sha256,
                template_dir,
            )
        requirement_root = Path(requirement.requirements_path).resolve().parent
        # Most output workspaces start empty. Template-gated competitions are
        # seeded above from the exact immutable source selected at submission.
        self._copy_requirement_workspace(requirement, requirement_root, requirements_dir)
        # Evaluator tests are deliberately injected only after the uploaded
        # agent has finished. Keeping this directory empty during agent
        # execution prevents the agent from reading the scoring suite.
        self.traceability_seed_builder.write_seed_file(
            arc_dir / "traceability-seed.json",
            requirement,
            requirement_yaml_path=requirements_dir / "requirements.yaml",
        )

        (workspace_root / "runner-spec.json").write_text(
            json.dumps(
                {
                    "agent_source": submission.agent_source,
                    "runtime": submission.runtime,
                    "submission_dir": "/workspace/submission",
                    "template_dir": "/workspace/template",
                    "project_dir": "/workspace/template",
                    "tests_dir": "/workspace/tests",
                    "arc_dir": ".arc",
                    "requirement_dir": "requirements",
                    "output_dir": "/workspace/template",
                    "runner_events_path": ".arc/runner-events.jsonl",
                    "traceability_dir": ".arc/traceability",
                    "task": {
                        "category": requirement.category,
                        "requirement_id": requirement.id,
                        "test_runner": requirement.test_runner,
                    },
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

        debug_log_path = workspace_root / "execution.debug.log"
        debug_log_path.write_text(
            "Workspace assembled successfully.\n",
            encoding="utf-8",
        )

    @staticmethod
    def _materialize_demo_source_template(submission_dir: Path) -> None:
        """Expand the bundled Git repository used by the deterministic demo replay."""
        bundle_path = submission_dir / "template.git.bundle"
        if not bundle_path.is_file():
            raise FileNotFoundError("Quick Start demo bundle is missing template.git.bundle")
        target_dir = submission_dir / "template"
        if target_dir.exists():
            shutil.rmtree(target_dir)
        result = subprocess.run(
            ["git", "clone", "--quiet", str(bundle_path), str(target_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"Could not expand Quick Start demo template: {detail}")
        bundle_path.unlink()
        # Keep the canonical snapshot store on the server. A demo archive is
        # intentionally small; the runner workspace receives a private copy
        # (or a future copy-on-write view) of the immutable demo snapshots.
        snapshot_source = get_settings().demo_agent_zip.with_suffix("") / "snapshots"
        if snapshot_source.is_dir():
            snapshot_target = submission_dir / "snapshots"
            if snapshot_target.exists():
                if snapshot_target.is_dir() and not snapshot_target.is_symlink():
                    shutil.rmtree(snapshot_target)
                else:
                    snapshot_target.unlink()
            # Snapshots are immutable server assets. A symlink avoids walking
            # and materializing several gigabytes during every demo upload.
            snapshot_target.symlink_to(snapshot_source, target_is_directory=True)

    @staticmethod
    def _link_or_copy(source: str, destination: str) -> str:
        """Share immutable demo snapshot files when the filesystem permits it."""
        try:
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).unlink(missing_ok=True)
            Path(destination).hardlink_to(source)
            return destination
        except OSError:
            shutil.copy2(source, destination)
            return destination

    @staticmethod
    def _copy_optional_tree(source: Path, destination: Path) -> None:
        if not source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            return
        shutil.copytree(source, destination, dirs_exist_ok=True)

    def _copy_requirement_workspace(
        self,
        requirement: Requirement,
        requirement_root: Path,
        requirements_dir: Path,
    ) -> None:
        if requirements_dir.exists():
            shutil.rmtree(requirements_dir)
        requirements_dir.mkdir(parents=True, exist_ok=True)

        excluded_names = {"template", "tests"}
        for source_path in requirement_root.iterdir():
            if source_path.name in excluded_names:
                continue
            destination_path = requirements_dir / source_path.name
            if source_path.is_dir():
                shutil.copytree(source_path, destination_path, dirs_exist_ok=True)
            elif source_path.is_file():
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination_path)

        requirement_markdown_path = Path(requirement.requirements_path)
        requirement_yaml_path = requirement_markdown_path.with_name("requirements.yaml")
        prerequisites_path = Path(requirement.prerequisites_path)
        if requirement_markdown_path.is_file():
            shutil.copy2(requirement_markdown_path, requirements_dir / "requirements.md")
        if requirement_yaml_path.is_file():
            shutil.copy2(requirement_yaml_path, requirements_dir / "requirements.yaml")
        if prerequisites_path.is_file():
            shutil.copy2(prerequisites_path, requirements_dir / "prerequisites.md")
        elif not (requirements_dir / "prerequisites.md").exists():
            (requirements_dir / "prerequisites.md").write_text("", encoding="utf-8")

        self._copy_optional_tree(Path(requirement.assets_path), requirements_dir / "assets")
        self._copy_optional_tree(Path(requirement.references_path), requirements_dir / "reference")

        if not (requirements_dir / "requirements.yaml").is_file():
            raise FileNotFoundError(
                f"Requirement source is missing requirements.yaml: {requirement_yaml_path}"
            )

    @staticmethod
    def _flatten_single_root(agent_dir: Path, runtime: str = "python") -> None:
        members = []
        for child in agent_dir.iterdir():
            if child.is_file():
                members.append(child.relative_to(agent_dir))
            elif child.is_dir():
                members.extend(path.relative_to(agent_dir) for path in child.iterdir() if path.is_file())
        relative_root = resolve_agent_root(members, runtime)
        if relative_root == Path("."):
            return
        # Select the complete directory, including hidden files. Wrapper siblings
        # are not part of the agent and must not overwrite its files.
        selected_root = agent_dir.with_name(f".agent-root-{uuid.uuid4().hex}")
        os.replace(agent_dir / relative_root, selected_root)
        shutil.rmtree(agent_dir)
        os.replace(selected_root, agent_dir)
