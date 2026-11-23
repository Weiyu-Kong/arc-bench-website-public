"""Lifecycle service for immutable agent submissions and their run creation."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.core.enums import AgentSourceType, RuntimeType, SubmissionStatus
from app.models.requirement import Requirement
from app.models.competition_account import CompetitionEntry, Team, TeamMembership
from app.models.run import Run
from app.models.submission import Submission
from app.models.user import User
from app.services.model_provider_service import ModelProviderService
from app.services.classroom_service import ClassroomService
from app.services.requirement_catalog import RequirementCatalogService
from app.services.runtime_path_service import RuntimePathService
from app.services.submission_service import DEFAULT_STEPS, RunService
from app.services.user_task_service import UserTaskService
from app.services.competition_access_service import CompetitionAccessService
from app.services.competition_registration_service import CompetitionRegistrationService
from app.services.competition_template_service import CompetitionTemplateService
from app.services.demo_agent_service import DemoAgentService
from app.services.competition_submission_policy import deletion_reason


class AgentSubmissionService:
    def __init__(self, db: Session):
        self.db = db
        self.runtime_paths = RuntimePathService()
        self.settings = self.runtime_paths.settings
        self.model_providers = ModelProviderService(self.settings.runtime_config_path)

    def create(
        self,
        *,
        user_id: str,
        requirement_id: str | None,
        competition_id: str | None,
        runtime: RuntimeType,
        catalog: str,
        upload: UploadFile | None,
        display_name: str | None,
        base_url: str | None,
        api_key: str | None,
        model: str | None,
        visual_model: str | None = None,
        agent_source: AgentSourceType,
        credential_mode: str = "self_funded",
        task_type: str | None = None,
        template_selections: dict[str, str] | None = None,
    ) -> Submission:
        user = self._get_user(user_id)
        normalized_catalog = catalog.strip().lower()
        normalized_competition_id = (competition_id or "").strip().lower() or None
        normalized_requirement_id = (requirement_id or "").strip() or None
        competition_entry: CompetitionEntry | None = None
        normalized_credential_mode = str(credential_mode or "self_funded").strip().lower()
        if normalized_credential_mode not in {"self_funded", "official_evaluation"}:
            raise ValueError("Unknown submission credential mode")
        # Accept the legacy form value, but all new submissions use the user's key.
        normalized_credential_mode = "self_funded"
        if agent_source == AgentSourceType.BUILTIN_OCTOS_AGENT:
            raise ValueError("Built-in Octos agent is temporarily unavailable")
        if normalized_catalog == "competition":
            if not normalized_competition_id:
                raise ValueError("competition_id is required for a competition submission")
            competitions = RequirementCatalogService.for_catalog(self.db, "competition").list_competitions()
            competition = next((item for item in competitions if item.id == normalized_competition_id), None)
            if competition is None:
                raise LookupError(f"Competition '{normalized_competition_id}' not found")
            if str(competition.status or "").strip().lower() != "open":
                raise ValueError("This competition is no longer accepting submissions")
            CompetitionAccessService(self.db).require_access(user, normalized_competition_id)
            is_official_competition = competition.type == "official"
            class_entry = ClassroomService(self.db).ensure_entry(user.id, normalized_competition_id)
            if class_entry is not None:
                competition_entry = class_entry
            elif is_official_competition:
                if normalized_competition_id == "first-competition":
                    competition_entry = CompetitionRegistrationService(self.db).require_personal_entry(user, normalized_competition_id)
                elif normalized_competition_id == "hackathon":
                    _team, competition_entry = self._require_official_team_leader(
                        user, normalized_competition_id
                    )
                else:
                    competition_entry = CompetitionRegistrationService(self.db).ensure_invited_team_entry(
                        user,
                        normalized_competition_id,
                        0,
                    )
            if not competition.template_required and template_selections:
                raise ValueError("This competition does not accept generated template selections")
            if competition_entry is None:
                competition_entry = self._competition_entry_for_user(user, normalized_competition_id)
            normalized_requirement_id = f"{normalized_competition_id}--__agent__"
        else:
            if not normalized_requirement_id:
                raise ValueError("A requirement id is required for a Playground submission")
            if normalized_catalog == "my_tasks":
                _task, requirement = UserTaskService(self.db).get_owned_task_for_submission(user, normalized_requirement_id)
            else:
                RequirementCatalogService.for_catalog(self.db, normalized_catalog).sync_to_db(normalized_requirement_id)
                requirement = self.db.get(Requirement, normalized_requirement_id)
            if requirement is None:
                raise LookupError(f"Requirement '{normalized_requirement_id}' not found")
            if task_type and task_type.strip().lower() != str(requirement.category or "").strip().lower():
                raise ValueError("Task type does not match the selected requirement")

        provider = self.model_providers.resolve_model(
            api_key=api_key,
            base_url=base_url,
            model=model,
            require_api_key=agent_source != AgentSourceType.DEMO_REPLAY,
        )
        submission_id = uuid.uuid4().hex[:12]
        submission = Submission(
            id=submission_id,
            user_id=user.id,
            display_name=self._normalize_display_name(display_name),
            # Legacy clients may still supply model hints. New uploads select
            # models in agent code, so do not label them with a default model.
            model_name=str(model or "").strip() or None,
            visual_model=str(visual_model or "").strip() or None,
            openai_base_url=provider.openai_base_url,
            openai_api_key=provider.openai_api_key,
            catalog=normalized_catalog,
            competition_id=normalized_competition_id,
            competition_entry_id=competition_entry.id if competition_entry else None,
            competition_owner_display_name=competition_entry.display_name if competition_entry else None,
            credential_mode=normalized_credential_mode,
            requirement_id=normalized_requirement_id,
            runtime=runtime.value,
            agent_source=agent_source.value,
            original_filename="",
            archive_path="",
        )
        submission_root = self.runtime_paths.get_shared_submission_root(user.id, submission.id)
        submission_root.mkdir(parents=True, exist_ok=True)
        archive_path = submission_root / "agent.zip"
        if agent_source == AgentSourceType.BUILTIN_ARC_AGENT:
            if runtime != RuntimeType.PYTHON:
                raise ValueError("Built-in ARC agent only supports Python runtime")
            RunService._write_builtin_arc_agent_archive(archive_path)
            original_filename = "builtin-arc-agent.zip"
            RunService._validate_agent_archive(archive_path, runtime)
        elif agent_source == AgentSourceType.DEMO_REPLAY:
            if runtime != RuntimeType.PYTHON:
                raise ValueError("The Quick Start demo only supports Python runtime")
            try:
                archive_path.write_bytes(DemoAgentService().build_bundle()[0])
            except FileNotFoundError as exc:
                raise RuntimeError(str(exc)) from exc
            original_filename = "quick-start-demo.zip"
            RunService._validate_agent_archive(archive_path, runtime)
        else:
            if upload is None or not upload.filename or not upload.filename.lower().endswith(".zip"):
                raise ValueError("Only .zip uploads are supported")
            with archive_path.open("wb") as output:
                shutil.copyfileobj(upload.file, output)
            original_filename = upload.filename
            RunService._validate_agent_archive(archive_path, runtime)
        submission.original_filename = original_filename
        submission.archive_path = str(archive_path)
        self.db.add(submission)
        self.db.flush()
        if normalized_catalog == "competition" and competition.template_required:
            # Evolution baselines are selected by the server. Never trust
            # application files embedded in the uploaded agent archive.
            CompetitionTemplateService(self.db).copy_best_or_blank(submission, submission_root, user)
        self.db.commit()
        self.db.refresh(submission)
        return submission

    def create_run(self, submission_id: str, user_id: str, *, requirement_id: str | None = None) -> Run:
        submission = self.get(submission_id, user_id, allow_team_entry=True)
        if submission.deprecated_at is not None:
            raise ValueError("This submission is deprecated and cannot be run")
        if submission.agent_source != AgentSourceType.DEMO_REPLAY.value and not str(submission.openai_api_key or "").strip():
            raise ValueError("Upload a new submission with your own API key before running this task.")
        user = self._get_user(user_id)
        target_requirement_id = (requirement_id or submission.requirement_id or "").strip()
        if submission.catalog == "competition":
            if not submission.competition_id:
                raise ValueError("Competition submission is missing its competition id")
            CompetitionAccessService(self.db).require_access(user, submission.competition_id)
            competition = next(
                (item for item in RequirementCatalogService.for_catalog(self.db, "competition").list_competitions() if item.id == submission.competition_id),
                None,
            )
            class_team = ClassroomService(self.db).require_leader(user.id, submission.competition_id)
            if class_team is None and competition and competition.type == "official":
                if submission.competition_id == "first-competition":
                    CompetitionRegistrationService(self.db).require_personal_entry(user, submission.competition_id)
                else:
                    self._require_official_team_leader(user, submission.competition_id)
            latest_id = self.db.scalar(
                select(Submission.id)
                .where(Submission.catalog == "competition")
                .where(Submission.deprecated_at.is_(None))
                .where(Submission.competition_id == submission.competition_id)
                .where(
                    Submission.competition_entry_id == submission.competition_entry_id
                    if submission.competition_entry_id
                    else Submission.user_id == submission.user_id
                )
                .order_by(desc(Submission.created_at))
                .limit(1)
            )
            if latest_id != submission.id:
                raise ValueError("Runs must use the latest saved agent submission for this competition")
            if not target_requirement_id.startswith(f"{submission.competition_id}--") or target_requirement_id.endswith("--__agent__"):
                raise ValueError("The selected task does not belong to this competition")
            RequirementCatalogService.for_catalog(self.db, "competition").sync_to_db(target_requirement_id)
        elif target_requirement_id != submission.requirement_id:
            raise ValueError("A Playground run must use the task selected for its submission")
        if self.db.get(Requirement, target_requirement_id) is None:
            raise LookupError(f"Requirement '{target_requirement_id}' not found")
        source_archive = Path(submission.archive_path)
        if not source_archive.is_file():
            raise FileNotFoundError("The submitted agent archive is no longer available")

        run_id = uuid.uuid4().hex[:12]
        template_source = CompetitionTemplateService(self.db).source_for_submission_task(submission.id, target_requirement_id)
        # Progressive hackathon stages inherit the latest completed application
        # from the preceding stage. The uploaded agent remains the generator,
        # while the generated application is seeded from the immutable artifact.
        if submission.competition_id and template_source is None:
            template_source = CompetitionTemplateService(self.db).resolve_stage_baseline(
                submission.competition_id,
                target_requirement_id,
                user,
                billing_mode="self_funded",
            )
        competition = next(
            (item for item in RequirementCatalogService.for_catalog(self.db, "competition").list_competitions() if item.id == submission.competition_id),
            None,
        ) if submission.catalog == "competition" else None
        _base_slug, _stage_number = RequirementCatalogService.stage_info(target_requirement_id)
        if competition and competition.template_required and template_source is None:
            raise ValueError("This submission is missing the required generated template for the selected task")
        run = Run(
            id=run_id,
            user_id=user.id,
            submission_id=submission.id,
            submission_display_name=submission.display_name,
            model_name=submission.model_name,
            visual_model=submission.visual_model,
            openai_base_url=submission.openai_base_url,
            openai_api_key=submission.openai_api_key,
            original_filename=submission.original_filename,
            catalog=submission.catalog,
            competition_id=submission.competition_id,
            competition_entry_id=submission.competition_entry_id,
            competition_owner_display_name=submission.competition_owner_display_name,
            billing_mode="self_funded",
            budget_competition_entry_id=None,
            initiated_by_user_id=user.id,
            requirement_id=target_requirement_id,
            runtime=submission.runtime,
            agent_source=submission.agent_source,
            agent_archive_path="",
            status=SubmissionStatus.PENDING.value,
            steps_json=json.dumps([step.model_dump() for step in DEFAULT_STEPS]),
            template_source_run_id=(getattr(template_source, "source_run_id", None) or getattr(template_source, "run_id", None)) if template_source else None,
            template_archive_path=template_source.archive_path if template_source else None,
            template_archive_sha256=template_source.sha256 if template_source else None,
        )
        run_root = self.runtime_paths.get_shared_run_root(user.id, run.id)
        run_root.mkdir(parents=True, exist_ok=True)
        if _stage_number and _stage_number > 1 and template_source is None:
            destination = run_root / "baseline-template.zip"
            CompetitionTemplateService._archive_directory(self.settings.web_template_files_root, destination)
            run.template_source_run_id = "blank"
            run.template_archive_path = str(destination)
            run.template_archive_sha256 = CompetitionTemplateService.file_sha256(destination)
        elif _stage_number and _stage_number > 1 and template_source is not None:
            destination = run_root / "baseline-template.zip"
            shutil.copy2(template_source.archive_path, destination)
            run.template_archive_path = str(destination)
        archive_path = run_root / "agent.zip"
        shutil.copy2(source_archive, archive_path)
        requirement = self.db.get(Requirement, target_requirement_id)
        if requirement is None:
            raise LookupError(f"Requirement '{target_requirement_id}' not found")
        self._snapshot_run_requirements(run, requirement)
        run.agent_archive_path = str(archive_path)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def _snapshot_run_requirements(self, run: Run, requirement: Requirement) -> None:
        """Persist immutable task inputs where every API and Executor can read them."""
        if not run.user_id:
            raise ValueError("Run has no owner user")
        source_root = Path(requirement.requirements_path).resolve().parent
        if not source_root.is_dir():
            raise FileNotFoundError(f"Requirement source directory is not available: {source_root}")
        destination = self.runtime_paths.get_shared_run_requirements_root(run.user_id, run.id)
        if destination.exists():
            raise FileExistsError(f"Run requirements snapshot already exists: {destination}")
        destination.mkdir(parents=True)
        for filename in ("requirements.md", "requirements.yaml", "requirements.yml", "prerequisites.md"):
            source = source_root / filename
            if source.is_file():
                shutil.copy2(source, destination / filename)
        for directory_name in ("assets", "reference"):
            source = source_root / directory_name
            if source.is_dir():
                shutil.copytree(source, destination / directory_name)

    def list(self, user_id: str, *, requirement_id: str | None = None, competition_id: str | None = None) -> list[Submission]:
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user_id))
        owner_filter = Submission.user_id == user_id
        if membership:
            team_entry_ids = select(CompetitionEntry.id).where(CompetitionEntry.team_id == membership.team_id)
            owner_filter = or_(owner_filter, Submission.competition_entry_id.in_(team_entry_ids))
        query = select(Submission).where(owner_filter, Submission.deprecated_at.is_(None)).order_by(desc(Submission.created_at))
        if requirement_id:
            query = query.where(Submission.requirement_id == requirement_id)
        if competition_id:
            query = query.where(Submission.competition_id == competition_id)
        return self.db.scalars(query).all()

    def get(self, submission_id: str, user_id: str | None = None, *, allow_team_entry: bool = False) -> Submission:
        submission = self.db.get(Submission, submission_id)
        if submission is None:
            raise LookupError(f"Submission '{submission_id}' not found")
        if user_id is not None and submission.user_id != user_id:
            if not allow_team_entry or not self._user_can_access_team_entry(user_id, submission.competition_entry_id):
                raise LookupError(f"Submission '{submission_id}' not found")
        return submission

    def delete(self, submission_id: str, user_id: str) -> None:
        """Deprecate a snapshot without removing its record or stored files."""
        submission = self.get(submission_id, user_id, allow_team_entry=True)
        competition = next(
            (item for item in RequirementCatalogService.for_catalog(self.db, "competition").list_competitions()
             if item.id == submission.competition_id),
            None,
        ) if submission.competition_id else None
        class_team = ClassroomService(self.db).require_leader(user_id, submission.competition_id) if submission.competition_id else None
        if class_team is None and competition and competition.type == "official":
            if submission.competition_id == "first-competition":
                CompetitionRegistrationService(self.db).require_personal_entry(self._get_user(user_id), submission.competition_id)
            else:
                self._require_official_team_leader(self._get_user(user_id), submission.competition_id)
        if submission.deprecated_at is not None:
            return
        reason = deletion_reason(self.db, submission)
        if reason:
            raise ValueError(reason)
        active_run = self.db.scalar(
            select(Run.id)
            .where(Run.submission_id == submission.id)
            .where(Run.status.in_([
                SubmissionStatus.PENDING.value,
                SubmissionStatus.QUEUED.value,
                SubmissionStatus.STARTING.value,
                SubmissionStatus.RUNNING.value,
                SubmissionStatus.PAUSE_REQUESTED.value,
                SubmissionStatus.PAUSED.value,
                SubmissionStatus.RESUME_REQUESTED.value,
            ]))
            .limit(1)
        )
        if active_run:
            raise ValueError("Finish or cancel this submission's active run before deleting the submission")
        submission.deprecated_at = datetime.utcnow()
        self.db.add(submission)
        self.db.commit()

    def delete_many(self, submission_ids: list[str], user_id: str) -> dict[str, list]:
        """Best-effort deletion of owned or team-owned agent snapshots."""
        unique_ids = list(dict.fromkeys(item.strip() for item in submission_ids if item and item.strip()))
        if not unique_ids:
            raise ValueError("Choose at least one submission to delete")
        deleted_ids: list[str] = []
        skipped: list[dict[str, str]] = []
        for submission_id in unique_ids:
            try:
                self.delete(submission_id, user_id)
                deleted_ids.append(submission_id)
            except Exception as exc:  # noqa: BLE001 - batch deletion reports each failed item
                self.db.rollback()
                skipped.append({"id": submission_id, "reason": str(exc)})
        return {"deleted_ids": deleted_ids, "skipped": skipped}

    def _remove_archive_file_if_local(self, archive_path: str | None) -> None:
        """Remove a snapshot archive visible on this host, safely.

        API and executor hosts are being moved to a shared NFS mount.  Until
        that is complete, a submission row can point at an archive that is
        only visible on another host.  Deletion must remain successful in that
        situation, while avoiding a path traversal through a stale database
        value.  A remote-only archive is left for the storage cleanup job.
        """
        if not archive_path:
            return
        candidate = Path(archive_path)
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            return
        allowed_roots = (
            self.settings.shared_storage_root.resolve(),
            self.settings.user_submissions_root.resolve(),
        )
        if not any(resolved == root or root in resolved.parents for root in allowed_roots):
            return
        try:
            if resolved.is_file():
                resolved.unlink()
        except FileNotFoundError:
            return

    def _get_user(self, user_id: str) -> User:
        user = self.db.get(User, user_id)
        if user is None:
            raise LookupError(f"User '{user_id}' not found")
        return user

    def _competition_entry_for_user(self, user: User, competition_id: str) -> CompetitionEntry:
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user.id))
        if membership:
            team = self.db.get(Team, membership.team_id)
            if team is None:
                raise LookupError("Your team is no longer available")
            entry = self.db.scalar(
                select(CompetitionEntry).where(
                    CompetitionEntry.competition_id == competition_id,
                    CompetitionEntry.team_id == team.id,
                )
            )
            if entry is None:
                entry = CompetitionEntry(
                    competition_id=competition_id,
                    owner_kind="team",
                    team_id=team.id,
                    display_name=team.name,
                )
                self.db.add(entry)
                self.db.flush()
            return entry
        entry = self.db.scalar(
            select(CompetitionEntry).where(
                CompetitionEntry.competition_id == competition_id,
                CompetitionEntry.user_id == user.id,
            )
        )
        if entry is None:
            entry = CompetitionEntry(
                competition_id=competition_id,
                owner_kind="user",
                user_id=user.id,
                display_name=user.display_name or user.username,
            )
            self.db.add(entry)
            self.db.flush()
        return entry

    def _require_official_team_leader(self, user: User, competition_id: str) -> tuple[Team, CompetitionEntry]:
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user.id))
        if membership is None:
            raise PermissionError("Join a confirmed official competition team before submitting")
        team = self.db.get(Team, membership.team_id)
        if team is None:
            raise LookupError("Your team is no longer available")
        if team.leader_user_id != user.id:
            raise PermissionError("Only the team leader can manage official competition submissions")
        entry = self.db.scalar(
            select(CompetitionEntry).where(
                CompetitionEntry.competition_id == competition_id,
                CompetitionEntry.team_id == team.id,
                CompetitionEntry.confirmed_at.is_not(None),
            )
        )
        if entry is None:
            raise PermissionError("Confirm the official competition team before submitting")
        return team, entry

    def _user_can_access_team_entry(self, user_id: str, entry_id: str | None) -> bool:
        if not entry_id:
            return False
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user_id))
        entry = self.db.get(CompetitionEntry, entry_id)
        return bool(membership and entry and entry.owner_kind == "team" and entry.team_id == membership.team_id)

    @staticmethod
    def _normalize_display_name(value: str | None) -> str:
        if value is None:
            return "submission"
        normalized = " ".join(value.strip().split())
        if len(normalized) > 120:
            raise ValueError("Submission name must be 120 characters or fewer")
        return normalized or "submission"
