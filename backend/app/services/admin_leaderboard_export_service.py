"""Package the exact persisted inputs and outputs behind one leaderboard row."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.competition_template import RunTemplateArtifact
from app.models.run import Run
from app.models.submission import Submission
from app.schemas.requirement import CompetitionLeaderboardEntry
from app.services.preview_source_service import PreviewSourceService
from app.services.runtime_path_service import RuntimePathService


class AdminLeaderboardExportService:
    def __init__(self, db: Session):
        self.db = db
        self.paths = RuntimePathService()
        self.allowed_roots = [
            self.paths.settings.shared_storage_root.resolve(),
            self.paths.settings.user_submissions_root.resolve(),
        ]

    @staticmethod
    def segment(value: str) -> str:
        # IDs are also used inside ZIP paths; never use display names or filenames.
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("Invalid persisted record identifier")
        return value

    def checked_path(self, value: str | Path) -> Path:
        path = Path(value).resolve()
        if not any(path.is_relative_to(root) for root in self.allowed_roots):
            raise ValueError("File is outside configured export storage")
        if not path.is_file():
            raise FileNotFoundError("Saved file is unavailable")
        return path

    def build(self, entry: CompetitionLeaderboardEntry, filters: dict) -> tuple[Path, bool]:
        runs_by_id = {run.id: run for run in self.db.scalars(
            select(Run).where(Run.id.in_(entry.source_run_ids))
        ).all()}
        if set(runs_by_id) != set(entry.source_run_ids):
            raise LookupError("Leaderboard records changed; refresh and try again")
        runs = [runs_by_id[run_id] for run_id in dict.fromkeys(entry.source_run_ids)]
        for run in runs:
            self.segment(run.id)
            self.segment(run.submission_id)
        submissions = {item.id: item for item in self.db.scalars(
            select(Submission).where(Submission.id.in_({run.submission_id for run in runs}))
        ).all()}
        artifacts = {item.run_id: item for item in self.db.scalars(
            select(RunTemplateArtifact).where(RunTemplateArtifact.run_id.in_(runs_by_id))
        ).all()}
        manifest = {
            "version": 1, "exported_at": datetime.now(timezone.utc).isoformat(),
            "filters": filters,
            "leaderboard": entry.model_dump(mode="json", exclude={"account_emails"}),
            "runs": [], "files": [], "issues": [], "incomplete": False,
        }
        export_dir = Path(tempfile.mkdtemp(prefix="arcbench-admin-export-"))
        destination = export_dir / "leaderboard-export.zip"
        try:
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                def issue(name: str, reason: str) -> None:
                    manifest["issues"].append({"file": name, "reason": reason})

                def add_json(value: dict, name: str) -> None:
                    content = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
                    archive.writestr(name, content)
                    manifest["files"].append({"file": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})

                def add_file(value: str | Path | None, name: str, expected: str | None = None) -> bool:
                    staged = export_dir / "staged-file"
                    try:
                        if not value:
                            raise FileNotFoundError("No saved file was recorded")
                        source = self.checked_path(value)
                        digest = hashlib.sha256()
                        # Stage a stable copy: hashes describe the bytes actually downloaded,
                        # even if shared logs or snapshot pointers change during packaging.
                        with source.open("rb") as incoming, staged.open("wb") as output:
                            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                                output.write(chunk)
                                digest.update(chunk)
                        checksum = digest.hexdigest()
                        if expected is not None and checksum != expected:
                            raise ValueError("SHA256 validation failed")
                    except FileNotFoundError:
                        issue(name, "missing")
                        return False
                    except OSError:
                        issue(name, "unreadable")
                        return False
                    except ValueError as exc:
                        issue(name, str(exc))
                        return False
                    else:
                        archive.write(staged, name)
                        manifest["files"].append({"file": name, "sha256": checksum, "bytes": staged.stat().st_size})
                        return True
                    finally:
                        staged.unlink(missing_ok=True)

                for submission_id in sorted({run.submission_id for run in runs}):
                    submission = submissions.get(submission_id)
                    add_file(submission.archive_path if submission else None, f"agents/{submission_id}/agent.zip")

                for run in runs:
                    base = f"runs/{run.id}"
                    metadata = {field: getattr(run, field) for field in (
                        "id", "submission_id", "competition_id", "requirement_id", "status",
                        "score", "test_pass_rate", "passed_count", "failed_count",
                        "token_count", "token_cost_usd", "token_cost_currency",
                        "run_duration_seconds", "model_name", "active_generation", "cost_source", "cost_complete",
                    )}
                    for field in ("created_at", "started_at", "finished_at"):
                        value = getattr(run, field)
                        metadata[field] = value.isoformat() if value else None
                    try:
                        steps = json.loads(run.steps_json or "[]")
                        if not isinstance(steps, list):
                            raise ValueError("Invalid saved steps")
                        metadata["steps"] = [
                            {key: step[key] for key in ("key", "title", "status", "description") if isinstance(step.get(key), str)}
                            for step in steps if isinstance(step, dict)
                        ]
                    except (ValueError, TypeError):
                        issue(f"{base}/metadata.json", "saved_steps_invalid")
                    add_json(metadata, f"{base}/metadata.json")
                    record = {"run_id": run.id, "submission_id": run.submission_id,
                              "requirement_id": run.requirement_id, "snapshot_source": None}
                    manifest["runs"].append(record)
                    artifact = artifacts.get(run.id)

                    def add_snapshot(path: str | Path, digest: str) -> bool:
                        suffix = ".zip" if str(path).lower().endswith(".zip") else ".tar.gz"
                        return add_file(path, f"{base}/application{suffix}", digest)

                    if artifact and add_snapshot(artifact.archive_path, artifact.sha256):
                        record["snapshot_source"] = "run_template_artifact"
                    else:
                        try:
                            if not run.user_id:
                                raise FileNotFoundError("Run has no shared storage identity")
                            # Validate the pointer itself before the existing reader opens it.
                            self.checked_path(self.paths.get_shared_run_root(run.user_id, run.id) / "preview-sources" / "current.json")
                            snapshot, digest, revision = PreviewSourceService().current(run.user_id, run.id)
                            if add_snapshot(snapshot, digest):
                                record["snapshot_source"] = "shared_preview_source"
                                record["revision"] = revision
                        except (OSError, ValueError, RuntimeError):
                            issue(f"{base}/application", "shared_snapshot_unavailable")

                    for field, name in (("stdout_path", "stdout.log"), ("stderr_path", "stderr.log")):
                        add_file(getattr(run, field), f"{base}/logs/{name}")
                    if run.user_id:
                        debug = self.paths.get_shared_log_path(run.user_id, run.id, run.active_generation, "execution.debug")
                        if debug.exists():
                            add_file(debug, f"{base}/logs/execution.debug.log")
                    if run.result_path:
                        add_file(run.result_path, f"{base}/result.json")

                manifest["incomplete"] = bool(manifest["issues"])
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            return destination, manifest["incomplete"]
        except BaseException:
            shutil.rmtree(export_dir)
            raise
