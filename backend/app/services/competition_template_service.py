from __future__ import annotations

import copy
import hashlib
from io import BytesIO
import os
import shutil
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.core.enums import SubmissionStatus
from app.models.competition_account import CompetitionEntry, TeamMembership
from app.models.competition_template import RunTemplateArtifact, SubmissionTemplateSource
from app.models.run import Run
from app.models.submission import Submission
from app.models.user import User
from app.schemas.requirement import CompetitionTaskTemplateOptions, CompetitionTemplateOption, CompetitionTemplateOptionsResponse
from app.services.requirement_catalog import RequirementCatalogService
from app.services.runtime_path_service import RuntimePathService


class CompetitionTemplateService:
    """Freeze generated projects and validate task-specific template lineage."""

    EXCLUDED_PARTS = {"node_modules"}
    EXCLUDED_FILES = {(".arc", "playwright-report.json")}
    SNAPSHOT_FORMAT = b"arcbench-complete-workspace-v1"

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def task_slug(requirement_id: str) -> str:
        return requirement_id.rsplit("--", 1)[-1].strip().lower()

    @staticmethod
    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def freeze_completed_run(self, run: Run) -> RunTemplateArtifact | None:
        if (
            run.catalog != "competition"
            or not run.competition_id
            or run.status not in {SubmissionStatus.PASSED.value, SubmissionStatus.FAILED.value}
            or run.manual_edit_used
            or not run.workspace_path
        ):
            return None
        existing = self.db.scalar(select(RunTemplateArtifact).where(RunTemplateArtifact.run_id == run.id))
        if existing:
            archive_path = Path(existing.archive_path)
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    complete_snapshot = archive.comment == self.SNAPSHOT_FORMAT
                if complete_snapshot and self.file_sha256(archive_path) == existing.sha256:
                    return existing
            except (OSError, zipfile.BadZipFile):
                pass
        project_dir = Path(run.workspace_path) / "template"
        if not project_dir.is_dir():
            return existing
        if not any(self._include(path, project_dir) for path in project_dir.iterdir()):
            return existing
        artifact_dir = (
            RuntimePathService().get_shared_run_root(run.user_id, run.id) / "artifacts"
            if isinstance(run, Run) and run.user_id
            else Path(run.workspace_path).parent / "artifacts"
        )
        archive_path = Path(existing.archive_path) if existing else artifact_dir / "generated-template.zip"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = archive_path.with_name(f".{archive_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            self._archive_directory(project_dir, temporary_path)
            os.replace(temporary_path, archive_path)
        finally:
            temporary_path.unlink(missing_ok=True)

        digest = self.file_sha256(archive_path)
        if existing:
            # Existing rows may point at archives created before the complete
            # workspace snapshot format was introduced. Refresh the archive
            # when the source workspace is still available so later stage
            # inheritance cannot silently reuse a partial snapshot.
            existing.archive_path = str(archive_path)
            existing.sha256 = digest
            artifact = existing
        else:
            artifact = RunTemplateArtifact(
                id=uuid.uuid4().hex,
                run_id=run.id,
                competition_id=run.competition_id,
                requirement_id=run.requirement_id,
                user_id=run.user_id,
                competition_entry_id=run.competition_entry_id,
                archive_path=str(archive_path),
                sha256=digest,
            )
            self.db.add(artifact)
        self.db.commit()
        self.db.refresh(artifact)
        return artifact

    @classmethod
    def _include(cls, path: Path, root: Path) -> bool:
        relative = path.relative_to(root)
        return not (
            set(relative.parts) & cls.EXCLUDED_PARTS
            or tuple(relative.parts) in cls.EXCLUDED_FILES
        )

    def list_options(self, target_competition_id: str, user: User) -> CompetitionTemplateOptionsResponse:
        catalog = RequirementCatalogService.for_catalog(self.db, "competition")
        target = next((item for item in catalog.list_competitions() if item.id == target_competition_id), None)
        if target is None:
            raise LookupError(f"Competition '{target_competition_id}' not found")
        if not target.template_required or not target.template_source_competition_id:
            raise ValueError("This competition does not require generated templates")
        tasks = [item for item in catalog.scan_entries() if item.competition_id == target_competition_id]
        ownership = self._ownership_filter(user, target.template_source_competition_id)
        rows = self.db.execute(
            select(RunTemplateArtifact, Run)
            .join(Run, Run.id == RunTemplateArtifact.run_id)
            .join(Submission, Submission.id == Run.submission_id)
            .where(Submission.deprecated_at.is_(None))
            .where(RunTemplateArtifact.competition_id == target.template_source_competition_id)
            .where(Run.status.in_([SubmissionStatus.PASSED.value, SubmissionStatus.FAILED.value]))
            .where(Run.manual_edit_used.is_(False))
            .where(ownership)
            .order_by(Run.finished_at.desc(), Run.created_at.desc())
        ).all()
        groups: list[CompetitionTaskTemplateOptions] = []
        for task in tasks:
            slug = self.task_slug(task.id)
            options: list[CompetitionTemplateOption] = []
            for artifact, run in rows:
                path = Path(artifact.archive_path)
                if self.task_slug(artifact.requirement_id) != slug or not path.is_file():
                    continue
                if self.file_sha256(path) != artifact.sha256:
                    continue
                options.append(CompetitionTemplateOption(
                    artifact_id=artifact.id,
                    source_run_id=run.id,
                    source_submission_id=run.submission_id,
                    submission_name=run.submission_display_name,
                    model_name=run.model_name,
                    test_pass_rate=run.test_pass_rate,
                    completed_at=(run.finished_at or run.created_at).isoformat(),
                ))
            groups.append(CompetitionTaskTemplateOptions(target_task_id=task.id, target_task_title=task.title, options=options))
        return CompetitionTemplateOptionsResponse(source_competition_id=target.template_source_competition_id, tasks=groups)

    def resolve_best_templates(
        self,
        target_competition_id: str,
        user: User,
    ) -> list[tuple[str, RunTemplateArtifact | None, Run | None]]:
        """Resolve one immutable highest-scoring baseline for each target task."""
        catalog = RequirementCatalogService.for_catalog(self.db, "competition")
        target = next((item for item in catalog.list_competitions() if item.id == target_competition_id), None)
        if target is None:
            raise LookupError(f"Competition '{target_competition_id}' not found")
        if not target.template_required or not target.template_source_competition_id:
            return []
        tasks = [item for item in catalog.scan_entries() if item.competition_id == target_competition_id]
        if target.template_source_competition_id == "hackathon":
            return self._resolve_hackathon_leaderboard_templates(tasks, user, catalog)
        rows = self.db.execute(
            select(RunTemplateArtifact, Run)
            .join(Run, Run.id == RunTemplateArtifact.run_id)
            .join(Submission, Submission.id == Run.submission_id)
            .where(Submission.deprecated_at.is_(None))
            .where(RunTemplateArtifact.competition_id == target.template_source_competition_id)
            .where(Run.status.in_([SubmissionStatus.PASSED.value, SubmissionStatus.FAILED.value]))
            .where(Run.manual_edit_used.is_(False))
            .where(self._ownership_filter(user, target.template_source_competition_id))
            .order_by(
                desc(func.coalesce(Run.test_pass_rate, Run.score, 0.0)),
                desc(Run.finished_at).nullslast(),
                desc(Run.created_at),
            )
        ).all()
        resolved: list[tuple[str, RunTemplateArtifact | None, Run | None]] = []
        for task in tasks:
            slug = self.task_slug(task.id)
            selected: tuple[RunTemplateArtifact, Run] | None = None
            for artifact, run in rows:
                path = Path(artifact.archive_path)
                if self.task_slug(artifact.requirement_id) != slug or not path.is_file():
                    continue
                if self.file_sha256(path) != artifact.sha256:
                    continue
                selected = (artifact, run)
                break
            resolved.append((task.id, selected[0] if selected else None, selected[1] if selected else None))
        return resolved

    def _resolve_hackathon_leaderboard_templates(
        self,
        target_tasks,
        user: User,
        catalog: RequirementCatalogService,
    ) -> list[tuple[str, RunTemplateArtifact | None, Run | None]]:
        """Use the application represented by the participant's latest official leaderboard snapshot."""
        from app.services.progressive_competition_service import ProgressiveCompetitionService

        source_tasks = [item for item in catalog.scan_entries() if item.competition_id == "hackathon"]
        participant_id = self._participant_id(user, "hackathon")
        progressive = ProgressiveCompetitionService(catalog)
        resolved: list[tuple[str, RunTemplateArtifact | None, Run | None]] = []
        for task in target_tasks:
            slug = self.task_slug(task.id)
            leaderboard = progressive.leaderboard(
                "hackathon",
                source_tasks,
                task_id=slug,
                participant_id=participant_id,
            )
            candidate_runs = self._leaderboard_application_runs(leaderboard[0] if leaderboard else None)
            artifact, run = self._first_valid_template_artifact(candidate_runs)
            resolved.append((task.id, artifact, run))
        return resolved

    def _leaderboard_application_runs(self, leaderboard_entry) -> list[Run]:
        """Order leaderboard runs from the most complete inheritable project to the least."""
        if leaderboard_entry is None or not leaderboard_entry.source_run_ids:
            return []
        runs = self.db.scalars(
            select(Run).where(Run.id.in_(leaderboard_entry.source_run_ids))
        ).all()
        if not runs:
            return []
        if leaderboard_entry.scoring_route != "stages":
            selected = next((run for run in runs if run.id == leaderboard_entry.source_run_ids[0]), None)
            return [selected] if selected else []

        # A later progressive stage contains all earlier stage work. Prefer it,
        # but retain earlier completed stages as fallbacks if its artifact is
        # missing or fails integrity validation.
        def stage_key(run: Run) -> tuple[int, object, object]:
            _base, stage = RequirementCatalogService.stage_info(run.requirement_id)
            return (stage or 0, run.finished_at or run.created_at, run.created_at)

        return sorted(runs, key=stage_key, reverse=True)

    def _first_valid_template_artifact(
        self,
        candidate_runs: list[Run],
    ) -> tuple[RunTemplateArtifact | None, Run | None]:
        for run in candidate_runs:
            artifact = self.db.scalar(
                select(RunTemplateArtifact).where(RunTemplateArtifact.run_id == run.id)
            )
            if artifact is None:
                continue
            archive_path = Path(artifact.archive_path)
            if archive_path.is_file() and self.file_sha256(archive_path) == artifact.sha256:
                return artifact, run
        return None, None

    def _participant_id(self, user: User, competition_id: str) -> str:
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user.id))
        if membership:
            entry = self.db.scalar(select(CompetitionEntry).where(
                CompetitionEntry.competition_id == competition_id,
                CompetitionEntry.team_id == membership.team_id,
            ))
            if entry:
                return entry.id
        return user.id

    def download_templates(
        self,
        target_competition_id: str,
        user: User | None,
    ) -> dict[str, tuple[Path | None, dict[str, object]]]:
        result: dict[str, tuple[Path | None, dict[str, object]]] = {}
        if user is None:
            catalog = RequirementCatalogService.for_catalog(self.db, "competition")
            target = next((item for item in catalog.list_competitions() if item.id == target_competition_id), None)
            if target is None:
                raise LookupError(f"Competition '{target_competition_id}' not found")
            resolved = [
                (task.id, None, None)
                for task in catalog.scan_entries()
                if task.competition_id == target_competition_id
            ]
        else:
            resolved = self.resolve_best_templates(target_competition_id, user)
        for task_id, artifact, run in resolved:
            slug = self.task_slug(task_id)
            result[slug] = (
                Path(artifact.archive_path) if artifact else None,
                {
                    "target_task_id": task_id,
                    "source": (
                        "latest_leaderboard_submission"
                        if artifact and target_competition_id == "hackathon-evolution"
                        else "highest_score"
                        if artifact
                        else "blank_fallback"
                    ),
                    "source_run_id": run.id if run else None,
                    "test_pass_rate": run.test_pass_rate if run else None,
                },
            )
        return result

    def build_initial_projects_bundle(
        self,
        target_competition_id: str,
        user: User,
    ) -> tuple[bytes, str]:
        """Bundle the exact per-task projects used to seed an evolution run."""
        catalog = RequirementCatalogService.for_catalog(self.db, "competition")
        competitions = {item.id: item for item in catalog.list_competitions()}
        target = competitions.get(target_competition_id)
        if target is None:
            raise LookupError(f"Competition '{target_competition_id}' not found")
        if not target.template_required or not target.template_source_competition_id:
            raise ValueError("This competition does not inherit initial projects")

        tasks = {item.id: item for item in catalog.scan_entries()}
        settings = RuntimePathService().settings
        template_roots = {
            "web": settings.web_template_files_root,
            "mobile": settings.mobile_template_files_root,
            "cli": settings.cli_template_files_root,
        }
        resolved = self.resolve_best_templates(target_competition_id, user)
        if not resolved:
            raise ValueError("This competition has no tasks to download")

        memory_file = BytesIO()
        with zipfile.ZipFile(memory_file, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as bundle:
            bundle.comment = self.SNAPSHOT_FORMAT
            for task_id, artifact, run in sorted(resolved, key=lambda item: self.task_slug(item[0])):
                slug = self.task_slug(task_id)
                bundle.writestr(f"{slug}/", b"")
                if artifact is not None and run is not None:
                    source = Path(artifact.archive_path)
                    if not source.is_file() or self.file_sha256(source) != artifact.sha256:
                        raise ValueError(f"The inherited project for '{slug}' failed its integrity check")
                    self._add_archive_under_prefix(bundle, source, slug)
                    continue

                task = tasks.get(task_id)
                category = str(task.category if task else "web").strip().lower()
                source_root = template_roots.get(category, settings.web_template_files_root)
                self._add_directory_under_prefix(bundle, source_root, slug)
        return memory_file.getvalue(), f"{target_competition_id}-initial-projects.zip"

    @classmethod
    def _add_archive_under_prefix(
        cls,
        destination: zipfile.ZipFile,
        source_path: Path,
        prefix: str,
    ) -> None:
        with zipfile.ZipFile(source_path, "r") as source:
            for member in source.infolist():
                relative = PurePosixPath(member.filename)
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    raise ValueError("Inherited project archive contains an unsafe path")
                if (
                    set(relative.parts) & cls.EXCLUDED_PARTS
                    # The evaluator writes exactly this project-root file.
                    # A nested .arc/playwright-report.json belongs to the
                    # generated application (for example, one of its own test
                    # projects) and must remain inheritable.
                    or tuple(relative.parts) in cls.EXCLUDED_FILES
                ):
                    continue
                output_info = copy.copy(member)
                output_info.filename = f"{prefix.rstrip('/')}/{relative.as_posix()}"
                destination.writestr(output_info, source.read(member) if not member.is_dir() else b"")

    @classmethod
    def _add_directory_under_prefix(
        cls,
        destination: zipfile.ZipFile,
        source_root: Path,
        prefix: str,
    ) -> None:
        if not source_root.is_dir():
            raise FileNotFoundError(f"Blank application template not found: {source_root}")
        for path in sorted(source_root.rglob("*")):
            if not cls._include(path, source_root):
                continue
            relative = path.relative_to(source_root).as_posix()
            archive_name = f"{prefix.rstrip('/')}/{relative}"
            if path.is_symlink():
                info = zipfile.ZipInfo(archive_name)
                info.create_system = 3
                info.external_attr = path.lstat().st_mode << 16
                destination.writestr(info, os.readlink(path).encode("utf-8"))
            elif path.is_dir():
                destination.write(path, f"{archive_name}/")
            elif path.is_file():
                destination.write(path, archive_name)

    def validate_selections(self, target_competition_id: str, user: User, selections: dict[str, str]) -> list[tuple[str, RunTemplateArtifact]]:
        available = self.list_options(target_competition_id, user)
        expected = {group.target_task_id for group in available.tasks}
        if set(selections) != expected:
            raise ValueError("Choose one generated template for every competition task")
        allowed = {group.target_task_id: {option.artifact_id for option in group.options} for group in available.tasks}
        resolved: list[tuple[str, RunTemplateArtifact]] = []
        for task_id in sorted(expected):
            artifact_id = str(selections.get(task_id) or "").strip()
            if artifact_id not in allowed[task_id]:
                raise ValueError(f"The selected template is not eligible for task '{task_id}'")
            artifact = self.db.get(RunTemplateArtifact, artifact_id)
            if artifact is None:
                raise ValueError("The selected generated template is no longer available")
            resolved.append((task_id, artifact))
        return resolved

    def copy_selections(self, submission: Submission, submission_root: Path, resolved: list[tuple[str, RunTemplateArtifact]]) -> None:
        template_dir = submission_root / "templates"
        template_dir.mkdir(parents=True, exist_ok=True)
        for task_id, artifact in resolved:
            source = Path(artifact.archive_path)
            if not source.is_file() or self.file_sha256(source) != artifact.sha256:
                raise ValueError("A selected generated template failed its integrity check")
            destination = template_dir / f"{self.task_slug(task_id)}.zip"
            shutil.copy2(source, destination)
            self.db.add(SubmissionTemplateSource(
                id=uuid.uuid4().hex,
                submission_id=submission.id,
                target_requirement_id=task_id,
                source_artifact_id=artifact.id,
                source_run_id=artifact.run_id,
                archive_path=str(destination),
                sha256=artifact.sha256,
            ))

    def copy_best_or_blank(self, submission: Submission, submission_root: Path, user: User) -> None:
        if not submission.competition_id:
            return
        template_dir = submission_root / "templates"
        template_dir.mkdir(parents=True, exist_ok=True)
        settings = RuntimePathService().settings
        template_roots = {
            "web": settings.web_template_files_root,
            "mobile": settings.mobile_template_files_root,
            "cli": settings.cli_template_files_root,
        }
        catalog = RequirementCatalogService.for_catalog(self.db, "competition")
        entries = {item.id: item for item in catalog.scan_entries()}
        for task_id, artifact, run in self.resolve_best_templates(submission.competition_id, user):
            destination = template_dir / f"{self.task_slug(task_id)}.zip"
            if artifact is not None and run is not None:
                source = Path(artifact.archive_path)
                if not source.is_file() or self.file_sha256(source) != artifact.sha256:
                    raise ValueError("The selected highest-scoring template failed its integrity check")
                shutil.copy2(source, destination)
                source_artifact_id = artifact.id
                source_run_id = run.id
            else:
                task = entries.get(task_id)
                category = str(task.category if task else "web").strip().lower()
                source_root = template_roots.get(category, settings.web_template_files_root)
                self._archive_directory(source_root, destination)
                source_artifact_id = "blank"
                source_run_id = "blank"
            digest = self.file_sha256(destination)
            self.db.add(SubmissionTemplateSource(
                id=uuid.uuid4().hex,
                submission_id=submission.id,
                target_requirement_id=task_id,
                source_artifact_id=source_artifact_id,
                source_run_id=source_run_id,
                archive_path=str(destination),
                sha256=digest,
            ))

    @classmethod
    def _archive_directory(
        cls,
        source_root: Path,
        destination: Path,
        *,
        prefix: str | PurePosixPath = "",
    ) -> None:
        if not source_root.is_dir():
            raise FileNotFoundError(f"Blank application template not found: {source_root}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        archive_prefix = PurePosixPath(prefix) if str(prefix).strip() else PurePosixPath()
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.comment = cls.SNAPSHOT_FORMAT
            for directory, dirnames, filenames in os.walk(source_root, followlinks=False):
                directory_path = Path(directory)
                dirnames[:] = sorted(
                    name
                    for name in dirnames
                    if cls._include(directory_path / name, source_root)
                )
                for name in sorted([*dirnames, *filenames]):
                    path = directory_path / name
                    if not cls._include(path, source_root):
                        continue
                    relative = path.relative_to(source_root).as_posix()
                    archive_name = (archive_prefix / PurePosixPath(relative)).as_posix()
                    if path.is_symlink():
                        info = zipfile.ZipInfo(archive_name)
                        info.create_system = 3
                        info.external_attr = path.lstat().st_mode << 16
                        archive.writestr(info, os.readlink(path).encode("utf-8"))
                    elif path.is_dir() or path.is_file():
                        archive.write(path, archive_name)

    def source_for_submission_task(self, submission_id: str, requirement_id: str) -> SubmissionTemplateSource | None:
        return self.db.scalar(select(SubmissionTemplateSource).where(
            SubmissionTemplateSource.submission_id == submission_id,
            SubmissionTemplateSource.target_requirement_id == requirement_id,
        ))

    def resolve_stage_baseline(self, competition_id: str, requirement_id: str, user: User, *, billing_mode: str = "self_funded") -> RunTemplateArtifact | None:
        """Use the best run of the previous stage's latest evaluated snapshot."""
        from app.services.requirement_catalog import RequirementCatalogService
        base, stage = RequirementCatalogService.stage_info(requirement_id)
        if stage is None or stage <= 1:
            return None
        previous_slug = f"{base}-stage-{stage - 1}"
        catalog = RequirementCatalogService.for_catalog(self.db, "competition")
        previous = next((task for task in catalog.scan_entries() if task.id == f"{competition_id}--{previous_slug}"), None)
        if previous is None:
            return None
        from app.services.leaderboard_scoring import compute_score, rank_key, scoring_cost_for_run
        config = catalog._scoring_config(competition_id)
        ownership = self._ownership_filter(user, competition_id)
        rows = self.db.execute(
            select(RunTemplateArtifact, Run)
            .select_from(Run)
            .outerjoin(RunTemplateArtifact, Run.id == RunTemplateArtifact.run_id)
            .join(Submission, Submission.id == Run.submission_id)
            .where(Run.competition_id == competition_id)
            .where(Submission.deprecated_at.is_(None))
            .where(Run.requirement_id.in_([previous_slug, f"{competition_id}--{previous_slug}", f"{competition_id}-{previous_slug}"]))
            .where(Run.status.in_([SubmissionStatus.PASSED.value, SubmissionStatus.FAILED.value]))
            .where(Run.manual_edit_used.is_(False))
            .where(Run.billing_mode == billing_mode)
            .where(ownership)
            .order_by(desc(Submission.created_at), desc(Run.finished_at), desc(Run.created_at), desc(Run.id))
        ).all()
        latest_submission_id = None
        best_artifact = None
        best_run = None
        best_key = None
        for artifact, run in rows:
            # A failed setup is not a completed application evaluation.
            if int(run.passed_count or 0) + int(run.failed_count or 0) <= 0:
                continue
            if competition_id == "hackathon" and billing_mode == "official_evaluation" and run.evaluation_started_at is None:
                continue
            breakdown = compute_score(passed=run.passed_count, test_total=previous.total_tests,
                                  cost=scoring_cost_for_run(run, config.currency), currency=config.currency,
                                  config=config)
            if billing_mode == "official_evaluation" and breakdown.score is None:
                continue
            if latest_submission_id is None:
                latest_submission_id = run.submission_id
            if run.submission_id != latest_submission_id:
                continue
            # Rows are newest-first, so equal scores retain the newest run.
            key = rank_key(breakdown.score, breakdown.pass_rate, breakdown.cost, run.created_at)[:-1]
            if best_key is None or key < best_key:
                best_key = key
                best_artifact = artifact
                best_run = run
        # Repair legacy filtered artifacts only for the selected best run.
        # The executor's local workspace may be unavailable on the API host;
        # in that case preserve the existing archived fallback.
        workspace_path = getattr(best_run, "workspace_path", None)
        if workspace_path and (Path(workspace_path) / "template").is_dir():
            best_artifact = self.freeze_completed_run(best_run) or best_artifact
        if best_artifact is None:
            return None
        path = Path(best_artifact.archive_path)
        if not path.is_file() or self.file_sha256(path) != best_artifact.sha256:
            return None
        return best_artifact

    def _ownership_filter(self, user: User, source_competition_id: str):
        filters = [Run.user_id == user.id]
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user.id))
        if membership:
            entry = self.db.scalar(select(CompetitionEntry).where(
                CompetitionEntry.competition_id == source_competition_id,
                CompetitionEntry.team_id == membership.team_id,
            ))
            if entry:
                filters.append(Run.competition_entry_id == entry.id)
        return or_(*filters)

    @classmethod
    def extract_verified(cls, archive_path: Path, expected_sha256: str, destination: Path) -> None:
        if cls.file_sha256(archive_path) != expected_sha256:
            raise ValueError("The saved generated template failed its integrity check")
        destination_root = destination.resolve()
        with zipfile.ZipFile(archive_path, "r") as archive:
            links = []
            directories = []
            for member in archive.infolist():
                relative = PurePosixPath(member.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Generated template contains an unsafe path")
                target = (destination / Path(*relative.parts)).resolve()
                target.relative_to(destination_root)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    links.append((target, archive.read(member).decode("utf-8")))
                    continue
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    if mode:
                        directories.append((target, stat.S_IMODE(mode)))
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    if mode:
                        target.chmod(stat.S_IMODE(mode))
            # Create links last so archive entries cannot write through them.
            for target, link in links:
                if Path(link).is_absolute():
                    raise ValueError("Generated template contains an absolute symbolic link")
                (target.parent / link).resolve().relative_to(destination_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(link)
            for target, mode in reversed(directories):
                target.chmod(mode)
