"""Administrator-only inspection of uploads and their exact historical runs."""
import hashlib
import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.competition_template import RunTemplateArtifact
from app.models.run import Run
from app.models.submission import Submission
from app.models.user import User
from app.schemas.admin import AdminRunEntry, AdminRunPage, AdminSubmissionEntry, AdminSubmissionPage
from app.services.admin_leaderboard_export_service import AdminLeaderboardExportService
from app.services.preview_source_service import PreviewSourceService
from app.services.requirement_catalog import RequirementCatalogService


class AdminUserSubmissionService:
    def __init__(self, db: Session):
        self.db = db
        self.storage = AdminLeaderboardExportService(db)

    def require_user(self, user_id: str) -> None:
        if not self.db.scalar(select(User.id).where(User.id == user_id)):
            raise HTTPException(404, "User not found.")

    def require_submission(self, user_id: str, submission_id: str) -> Submission:
        # Never let a nested user URL resolve another user's submission.
        submission = self.db.scalar(select(Submission).where(
            Submission.id == submission_id, Submission.user_id == user_id,
            Submission.catalog == "competition",
        ))
        if submission is None:
            raise HTTPException(404, "Submission not found.")
        return submission

    def competitions(self, user_id: str) -> list[dict]:
        self.require_user(user_id)
        ids = self.db.scalars(select(Submission.competition_id).where(
            Submission.user_id == user_id, Submission.catalog == "competition",
            Submission.competition_id.is_not(None),
        ).distinct().order_by(Submission.competition_id)).all()
        titles = {item.id: item.title for item in RequirementCatalogService.for_catalog(self.db, "competition").list_competitions()} if ids else {}
        titles["first-competition"] = "Smoke Competition"
        return [{"id": value, "title": titles.get(value, value)} for value in ids]

    def submissions(self, user_id: str, page: int, competition_id: str | None) -> AdminSubmissionPage:
        self.require_user(user_id)
        query = select(
            Submission.id, Submission.competition_id, Submission.display_name, Submission.model_name,
            Submission.original_filename, Submission.runtime, Submission.created_at, Submission.deprecated_at,
        ).where(Submission.user_id == user_id, Submission.catalog == "competition")
        if competition_id:
            query = query.where(Submission.competition_id == competition_id)
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.execute(query.order_by(Submission.created_at.desc(), Submission.id.desc())
                               .offset((page - 1) * 20).limit(20)).mappings().all()
        ids = [row["id"] for row in rows]
        counts = dict(self.db.execute(select(Run.submission_id, func.count()).where(
            Run.submission_id.in_(ids), Run.catalog == "competition",
        ).group_by(Run.submission_id)).all()) if ids else {}
        return AdminSubmissionPage(items=[AdminSubmissionEntry(**row, run_count=counts.get(row["id"], 0)) for row in rows], total=total, page=page)

    def runs(self, user_id: str, submission_id: str, page: int) -> AdminRunPage:
        submission = self.require_submission(user_id, submission_id)
        query = select(
            Run.id, Run.requirement_id, Run.status, Run.score, Run.test_pass_rate,
            Run.passed_count, Run.failed_count, Run.token_cost_usd, Run.token_cost_currency,
            Run.created_at, Run.finished_at,
        ).where(Run.submission_id == submission.id, Run.catalog == "competition")
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.execute(query.order_by(Run.created_at.desc(), Run.id.desc()).offset((page - 1) * 20).limit(20)).mappings()
        return AdminRunPage(items=[AdminRunEntry.model_validate(row) for row in rows], total=total, page=page)

    def agent(self, user_id: str, submission_id: str) -> Path:
        submission = self.require_submission(user_id, submission_id)
        try:
            return self.storage.checked_path(submission.archive_path)
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(404, "Submission archive is not available") from exc

    def artifact(self, user_id: str, submission_id: str, run_id: str) -> Path:
        submission = self.require_submission(user_id, submission_id)
        run = self.db.scalar(select(Run).where(
            Run.id == run_id, Run.submission_id == submission.id, Run.catalog == "competition",
        ))
        if run is None:
            raise HTTPException(404, "Run not found.")
        artifact = self.db.scalar(select(RunTemplateArtifact).where(RunTemplateArtifact.run_id == run.id))
        directory = Path(tempfile.mkdtemp(prefix="arcbench-admin-artifact-"))
        try:
            def copy_verified(value: str | Path, expected: str) -> Path:
                source = self.storage.checked_path(value)
                suffix = ".zip" if source.suffix.lower() == ".zip" else ".tar.gz"
                destination = directory / f"application{suffix}"
                digest = hashlib.sha256()
                with source.open("rb") as incoming, destination.open("wb") as output:
                    for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                        output.write(chunk)
                        digest.update(chunk)
                if not expected or digest.hexdigest() != expected:
                    destination.unlink(missing_ok=True)
                    raise ValueError("Saved artifact integrity validation failed")
                return destination

            if artifact:
                try:
                    return copy_verified(artifact.archive_path, artifact.sha256)
                except (OSError, ValueError, RuntimeError):
                    pass
            if run.user_id:
                try:
                    self.storage.checked_path(self.storage.paths.get_shared_run_root(run.user_id, run.id) / "preview-sources" / "current.json")
                    snapshot, digest, _revision = PreviewSourceService().current(run.user_id, run.id)
                    return copy_verified(snapshot, digest)
                except (OSError, ValueError, RuntimeError):
                    pass
            raise HTTPException(404, "Run artifacts are unavailable or failed integrity validation.")
        except BaseException:
            shutil.rmtree(directory)
            raise
