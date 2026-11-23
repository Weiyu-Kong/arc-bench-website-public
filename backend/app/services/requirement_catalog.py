from __future__ import annotations

import importlib.util
import io
import re
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session
import yaml

from app.core.enums import SubmissionStatus
from app.core.config import get_settings
from app.models.requirement import Requirement
from app.models.competition_account import CompetitionEntry, Team, TeamMembership
from app.models.submission import Submission
from app.models.run import Run
from app.models.user import User
from app.schemas.requirement import (
    BenchmarkDetail,
    BenchmarkDownloadLinks,
    BenchmarkSummary,
    BenchmarkTaskSummary,
    CompetitionDetail,
    CompetitionLeaderboardEntry,
    CompetitionSubmissionHistoryEntry,
    CompetitionSummary,
    CompetitionTaskRunScore,
    CompetitionTaskDownloadLinks,
    CompetitionTaskSummary,
    RequirementDetail,
    RequirementSummary,
    RequirementTestFile,
    RequirementTests,
)
from app.services.competition_leaderboard_selection_service import CompetitionLeaderboardSelectionService
from app.services.leaderboard_scoring import (
    ScoringConfig,
    ScoreBreakdown,
    compute_score,
    funding_source_for_runs,
    aggregate_run_cost,
    scoring_cost_for_run,
    sum_scoring_costs,
    rank_key,
)


try:
    _YamlLoader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover - minimal PyYAML builds
    _YamlLoader = yaml.SafeLoader


_SCAN_CACHE: dict[str, tuple[tuple[tuple[str, int], ...], list["CatalogRequirementEntry"]]] = {}
_SCAN_LOCK = threading.Lock()


@dataclass
class CatalogRequirementEntry:
    id: str
    competition_id: str | None
    title: str
    category: str
    summary: str
    test_runner: str
    total_tests: int
    module_count: int
    requirements_path: Path
    requirements_yaml_path: Path
    prerequisites_path: Path
    tests_path: Path
    assets_path: Path
    references_path: Path


class RequirementCatalogService:
    _STAGE_RE = re.compile(r"^(?P<base>.+)-stage-(?P<number>\d+)$", re.IGNORECASE)

    @classmethod
    def stage_info(cls, requirement_id: str) -> tuple[str, int | None]:
        slug = requirement_id.rsplit("--", 1)[-1].strip().lower()
        match = cls._STAGE_RE.match(slug)
        if not match:
            return slug, None
        return match.group("base"), int(match.group("number"))
    def __init__(
        self,
        db: Session,
        *,
        catalog_name: str,
        requirements_root: Path,
        tests_root: Path,
    ):
        self.db = db
        self.settings = get_settings()
        self.catalog_name = catalog_name
        self.requirements_root = requirements_root
        self.tests_root = tests_root

    @classmethod
    def for_catalog(cls, db: Session, catalog: str) -> "RequirementCatalogService":
        settings = get_settings()
        if catalog in {"competition", "benchmark"}:
            return cls(
                db,
                catalog_name=catalog,
                requirements_root=settings.requirements_root,
                tests_root=settings.tests_root,
            )
        if catalog == "playground":
            return cls(
                db,
                catalog_name="playground",
                requirements_root=settings.playground_requirements_root,
                tests_root=settings.playground_tests_root,
            )
        raise ValueError(f"Unknown catalog '{catalog}'")

    def _catalog_signature(self) -> tuple[tuple[str, int], ...]:
        """Return a cheap directory signature for the task catalog."""
        parts: list[tuple[str, int]] = []
        for _category, tasks_root, _tests_root in self._iter_catalog_sources():
            try:
                if not tasks_root.exists():
                    continue
                parts.append((str(tasks_root), tasks_root.stat().st_mtime_ns))
                for task_dir in tasks_root.iterdir():
                    if not task_dir.is_dir():
                        continue
                    parts.append((str(task_dir), task_dir.stat().st_mtime_ns))
                    for name in ("requirements", "tests"):
                        path = task_dir / name
                        if path.exists():
                            parts.append((str(path), path.stat().st_mtime_ns))
            except OSError:
                continue
        return tuple(parts)

    def scan_entries(self) -> list[CatalogRequirementEntry]:
        signature = self._catalog_signature()
        cached = _SCAN_CACHE.get(self.catalog_name)
        if cached and cached[0] == signature:
            return list(cached[1])
        with _SCAN_LOCK:
            cached = _SCAN_CACHE.get(self.catalog_name)
            if cached and cached[0] == signature:
                return list(cached[1])
            rows = self._scan_entries_uncached()
            _SCAN_CACHE[self.catalog_name] = (signature, rows)
            return list(rows)

    def _scan_entries_uncached(self) -> list[CatalogRequirementEntry]:
        rows: list[CatalogRequirementEntry] = []
        for category, tasks_root, tests_root in self._iter_catalog_sources():
            if not tasks_root.exists():
                continue

            for task_dir in sorted(tasks_root.iterdir()):
                if not task_dir.is_dir():
                    continue

                source_requirement_id = task_dir.name
                requirement_id = (
                    f"{category}--{source_requirement_id}"
                    if self.catalog_name == "competition"
                    else source_requirement_id
                )
                # A competition directory identifies the event, not the
                # deployable task type. Competition packs currently run as
                # web applications regardless of their event ID.
                task_category = "web" if self.catalog_name == "competition" else category
                resolved_paths = self._resolve_task_requirement_paths(task_dir)
                if resolved_paths is None:
                    continue
                requirements_path, requirement_yaml_path, prerequisites_path, assets_path, references_path = resolved_paths
                tests_path = task_dir / "tests"
                requirement_metadata = self._read_requirement_metadata(requirement_yaml_path)
                description = requirement_metadata.get("description")
                description_text = description.get("text", "") if isinstance(description, dict) else description
                leaf_requirement_count = self._count_leaf_nodes(requirement_metadata)
                display_test_count = self._count_test_cases(tests_path)
                rows.append(
                    CatalogRequirementEntry(
                        id=requirement_id,
                        competition_id=category if self.catalog_name == "competition" else None,
                        title=str(requirement_metadata.get("name") or requirement_id).strip() or requirement_id,
                        category=task_category,
                        summary=str(description_text or "").strip(),
                        test_runner="playwright",
                        total_tests=display_test_count,
                        module_count=leaf_requirement_count,
                        requirements_path=requirements_path,
                        requirements_yaml_path=requirement_yaml_path,
                        prerequisites_path=prerequisites_path,
                        tests_path=tests_path,
                        assets_path=assets_path,
                        references_path=references_path,
                    )
                )

        return rows

    def _resolve_task_requirement_paths(self, task_dir: Path) -> tuple[Path, Path, Path, Path, Path] | None:
        """Resolve a task from its YAML source without requiring rendered Markdown.

        ``requirements.yaml`` is the source of truth for every catalog. Markdown
        is only a presentation artifact and is rendered on demand when a detail
        page or document download needs it.
        """
        candidates = (
            task_dir / "requirements" / "requirements.yaml",
            task_dir / "requirements" / "requirements.yml",
            task_dir / "requirements.yaml",
            task_dir / "requirements.yml",
        )
        requirement_yaml_path = next((path for path in candidates if path.is_file()), None)
        if requirement_yaml_path is None:
            return None

        requirements_path = requirement_yaml_path.with_suffix(".md")
        source_dir = requirement_yaml_path.parent
        references_path = source_dir / "reference"
        assets_path = source_dir / "assets"
        if source_dir == task_dir:
            # Existing task packs keep references beside requirements.yaml.
            references_path = task_dir / "reference"
            assets_path = task_dir / "assets"
        return (
            requirements_path,
            requirement_yaml_path,
            source_dir / "prerequisites.md",
            assets_path,
            references_path,
        )

    @staticmethod
    def _render_requirement_markdown(requirement_yaml_path: Path) -> None:
        script_path = Path(__file__).resolve().parents[3] / "scripts" / "render_competition_requirements.py"
        spec = importlib.util.spec_from_file_location("arcbench_competition_requirement_renderer", script_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load requirement renderer: {script_path}")
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        renderer.convert_file(requirement_yaml_path)

    @staticmethod
    def _read_requirement_metadata(requirement_yaml_path: Path) -> dict[str, Any]:
        try:
            payload = yaml.load(requirement_yaml_path.read_text(encoding="utf-8"), Loader=_YamlLoader) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid requirement YAML: {requirement_yaml_path}") from exc
        return payload if isinstance(payload, dict) else {}

    def _iter_catalog_sources(self) -> list[tuple[str, Path, Path]]:
        if self.catalog_name == "competition":
            sources: list[tuple[str, Path, Path]] = []
            for competition_root in self._competition_roots():
                sources.append(
                    (
                        self._competition_id(competition_root.name),
                        competition_root,
                        competition_root,
                    )
                )
            return sources

        source_root = self.settings.arc_bench_root if self.catalog_name == "benchmark" else self.settings.playground_root
        if not source_root.is_dir():
            return []
        return [
            (track_root.name, track_root, track_root)
            for track_root in sorted(source_root.iterdir())
            if track_root.is_dir() and not track_root.name.startswith(".")
        ]

    def _competition_roots(self) -> list[Path]:
        root = self.settings.competition_root
        if not root.is_dir():
            return []
        return [path for path in sorted(root.iterdir(), key=lambda path: (path.name != "first-competition", path.name))
                if path.is_dir() and not path.name.startswith(".")]

    def _scoring_config(self, competition_id: str) -> ScoringConfig:
        competition_root = next(
            (root for root in self._competition_roots() if self._competition_id(root.name) == competition_id),
            None,
        )
        payload = self._read_competition_config(competition_root).get("scoring", {})
        if not isinstance(payload, dict):
            payload = {}
        defaults = ScoringConfig()

        def number(name: str, default: float, *, minimum: float = 0.0) -> float:
            try:
                value = float(payload.get(name, default))
            except (TypeError, ValueError):
                return default
            return value if isfinite(value) and value > minimum else default

        ratio_min = number("ratio_min", defaults.ratio_min)
        ratio_max = number("ratio_max", defaults.ratio_max)
        if ratio_max < ratio_min:
            ratio_max = defaults.ratio_max
        return ScoringConfig(
            alpha=number("alpha", defaults.alpha),
            beta=number("beta", defaults.beta),
            cost_per_test=number("cost_per_test", defaults.cost_per_test),
            cost_floor=number("cost_floor", defaults.cost_floor),
            ratio_min=ratio_min,
            ratio_max=ratio_max,
            currency=defaults.currency,
        )

    @staticmethod
    def _competition_id(name: str) -> str:
        return "-".join(part for part in name.strip().lower().replace("_", " ").split() if part)

    @staticmethod
    def _normalize_competition_category(app_dir_name: str) -> str:
        if app_dir_name == "webapp":
            return "web"
        if app_dir_name == "mobileapp":
            return "mobile"
        if app_dir_name.endswith("app"):
            return app_dir_name[:-3]
        return app_dir_name

    def sync_to_db(self, requirement_id: str | None = None) -> None:
        for entry in self.scan_entries():
            if requirement_id and entry.id != requirement_id:
                continue

            existing = self.db.get(Requirement, entry.id)
            data = {
                "title": entry.title,
                "category": entry.category,
                "summary": entry.summary,
                "test_runner": entry.test_runner,
                "requirements_path": str(entry.requirements_path),
                "prerequisites_path": str(entry.prerequisites_path),
                "tests_path": str(entry.tests_path),
                "assets_path": str(entry.assets_path),
                "references_path": str(entry.references_path),
                "total_tests": entry.total_tests,
                "module_count": entry.module_count,
            }
            if existing:
                for key, value in data.items():
                    setattr(existing, key, value)
            else:
                self.db.add(Requirement(id=entry.id, **data))

        self.db.commit()

    def list_requirements(self) -> list[RequirementSummary]:
        rows = self.scan_entries()
        display_ids = self._build_display_id_map(rows)
        return [self._to_requirement_summary(row, display_ids.get(row.id, row.id)) for row in rows]

    def get_requirement_detail(self, requirement_id: str, base_url: str) -> RequirementDetail:
        rows = self.scan_entries()
        display_ids = self._build_display_id_map(rows)
        requirement = self.get_entry(requirement_id, rows)
        self._ensure_requirement_markdown(requirement)
        requirements_markdown = requirement.requirements_path.read_text(encoding="utf-8")
        requirements_yaml = requirement.requirements_yaml_path.read_text(encoding="utf-8")
        prerequisites_markdown = self._read_text_if_exists(requirement.prerequisites_path)

        return RequirementDetail(
            id=requirement.id,
            display_id=display_ids.get(requirement.id, requirement.id),
            title=requirement.title,
            category=requirement.category,
            summary=requirement.summary,
            test_runner=requirement.test_runner,
            total_tests=requirement.total_tests,
            module_count=requirement.module_count,
            requirements_markdown=requirements_markdown,
            requirements_yaml=requirements_yaml,
            prerequisites_markdown=prerequisites_markdown,
            assets_base_url=f"{base_url}/api/requirements/{requirement.id}/assets?catalog={self.catalog_name}",
            references_base_url=f"{base_url}/api/requirements/{requirement.id}/references?catalog={self.catalog_name}",
        )

    def list_competitions(self) -> list[CompetitionSummary]:
        rows = self.scan_entries()
        grouped: dict[str, list[CatalogRequirementEntry]] = {}
        for row in rows:
            grouped.setdefault(row.competition_id or row.category, []).append(row)

        competitions: list[CompetitionSummary] = []
        for root in self._competition_roots():
            competition_id = self._competition_id(root.name)
            competitions.append(self._competition_summary_model(competition_id, root.name, grouped.get(competition_id, []), root))

        return competitions

    def list_competition_leaderboard(
        self,
        track: str = "all",
        competition_id: str | None = None,
        task_id: str | None = None,
        allowed_competition_ids: set[str] | None = None,
    ) -> list[CompetitionLeaderboardEntry]:
        normalized_track = track.strip().lower() or "all"
        efficiency_threshold = max(0.0, min(100.0, float(self.settings.leaderboard_efficiency_threshold)))
        if normalized_track not in {"all", "web", "mobile", "kernel"}:
            raise ValueError(f"Unsupported leaderboard track '{track}'")

        rows = self.scan_entries()
        if allowed_competition_ids is not None:
            rows = [row for row in rows if (row.competition_id or "").lower() in allowed_competition_ids]
        requirement_ids_by_category: dict[str, list[str]] = {}
        for row in rows:
            requirement_ids_by_category.setdefault(row.category, []).append(row.id)

        normalized_competition_id = competition_id.strip().lower() if competition_id else None
        if normalized_competition_id:
            competition_tasks = [item for item in rows if item.competition_id == normalized_competition_id]
            normalized_task_id = (task_id or "all").strip().lower()
            root = next((root for root in self._competition_roots() if self._competition_id(root.name) == normalized_competition_id), None)
            if self._read_competition_config(root).get("progressive_tasks"):
                if normalized_task_id not in {"", "all"} and not any(
                    item.id.lower() == normalized_task_id or item.id.rsplit("--", 1)[-1].lower() == normalized_task_id
                    for item in competition_tasks
                ):
                    raise ValueError(f"Task '{task_id}' does not belong to competition '{competition_id}'")
                from app.services.progressive_competition_service import ProgressiveCompetitionService
                return ProgressiveCompetitionService(self).leaderboard(normalized_competition_id, competition_tasks, normalized_task_id or "all")
            if normalized_task_id not in {"", "all"}:
                selected_task = next(
                    (item for item in competition_tasks if item.id.lower() == normalized_task_id
                     or item.id.rsplit("--", 1)[-1].lower() == normalized_task_id),
                    None,
                )
                if selected_task is None:
                    raise ValueError(f"Task '{task_id}' does not belong to competition '{competition_id}'")
                competition_tasks = [selected_task]
            else:
                normalized_task_id = "all"
            require_complete = normalized_task_id == "all"
            if require_complete:
                return self._list_complete_competition_leaderboard(
                    normalized_competition_id,
                    competition_tasks,
                    efficiency_threshold,
                )
            return self._list_single_task_competition_leaderboard(
                normalized_competition_id,
                competition_tasks[0],
                efficiency_threshold,
            )

        if normalized_track == "all":
            requirement_ids = [requirement_id for ids in requirement_ids_by_category.values() for requirement_id in ids]
        else:
            requirement_ids = requirement_ids_by_category.get(normalized_track, [])

        if not requirement_ids:
            return []

        query = (
            select(Run, User.username, Submission.model_name)
            .join(User, Run.user_id == User.id)
            .outerjoin(Submission, Run.submission_id == Submission.id)
            .where(Submission.deprecated_at.is_(None))
            .where(Run.requirement_id.in_(requirement_ids))
            .where(Run.user_id.is_not(None))
            .where(Run.status.in_([SubmissionStatus.PASSED.value, SubmissionStatus.FAILED.value]))
            .where(Run.score.is_not(None))
        )

        run_rows = self.db.execute(query).all()
        competition_ids = {run.competition_id for run, _username, _model in run_rows if run.competition_id}
        active_snapshots = self.db.scalars(select(Submission).where(
            Submission.catalog == "competition",
            Submission.competition_id.in_(competition_ids),
            Submission.deprecated_at.is_(None),
        )).all() if competition_ids else []
        grouped_snapshots = {}
        for snapshot in active_snapshots:
            grouped_snapshots.setdefault(snapshot.competition_id, []).append(snapshot)
        selected_ids = set()
        selector = CompetitionLeaderboardSelectionService(self.db)
        for snapshots in grouped_snapshots.values():
            choices, _manual_ids = selector.resolve(snapshots)
            selected_ids.update(snapshot.id for snapshot in choices.values())
        aggregates: dict[tuple[str, str], dict[str, object]] = {}
        for submission, username, source_model_name in run_rows:
            if submission.competition_id and submission.submission_id not in selected_ids:
                continue
            model_name = (source_model_name or "").strip() or None
            key = (submission.user_id or "", model_name or "")
            aggregate = aggregates.setdefault(
                key,
                {
                    "username": username,
                    "model_name": model_name,
                    "pass_rate_sum": 0.0,
                    "submission_count": 0,
                    "runtime_sum": 0,
                    "runtime_count": 0,
                    "token_count_sum": 0,
                    "token_metrics_complete": True,
                    "cost_sum": 0.0,
                    "cost_metrics_complete": True,
                    "cost_currencies": set(),
                    "runs": [],
                },
            )
            aggregate["runs"].append(submission)
            aggregate["pass_rate_sum"] = float(aggregate["pass_rate_sum"]) + float(submission.score or 0.0)
            aggregate["submission_count"] = int(aggregate["submission_count"]) + 1
            if submission.run_duration_seconds is not None:
                aggregate["runtime_sum"] = int(aggregate["runtime_sum"]) + int(submission.run_duration_seconds)
                aggregate["runtime_count"] = int(aggregate["runtime_count"]) + 1
            if submission.token_count is None:
                aggregate["token_metrics_complete"] = False
            else:
                aggregate["token_count_sum"] = int(aggregate["token_count_sum"]) + int(submission.token_count)
            if submission.token_cost_usd is None or not submission.token_cost_currency:
                aggregate["cost_metrics_complete"] = False
            else:
                aggregate["cost_sum"] = float(aggregate["cost_sum"]) + float(submission.token_cost_usd)
                currencies = aggregate["cost_currencies"]
                if isinstance(currencies, set):
                    currencies.add(submission.token_cost_currency)

        leaderboard: list[CompetitionLeaderboardEntry] = []
        for aggregate in aggregates.values():
            submission_count = int(aggregate["submission_count"])
            runtime_count = int(aggregate["runtime_count"])
            avg_runtime_seconds = int(round(int(aggregate["runtime_sum"]) / runtime_count)) if runtime_count > 0 else None
            avg_pass_rate = round(float(aggregate["pass_rate_sum"]) / submission_count, 1)
            total_cost = float(aggregate["cost_sum"]) if aggregate["cost_metrics_complete"] else None
            currencies = aggregate["cost_currencies"]
            cost_currency = next(iter(currencies)) if isinstance(currencies, set) and len(currencies) == 1 else None
            leaderboard.append(
                CompetitionLeaderboardEntry(
                    username=str(aggregate["username"]),
                    funding_source=funding_source_for_runs(aggregate["runs"]),
                    source_run_ids=[run.id for run in aggregate["runs"]],
                    model_name=aggregate["model_name"] if isinstance(aggregate["model_name"], str) or aggregate["model_name"] is None else None,
                    track=normalized_competition_id or normalized_track,
                    avg_pass_rate=avg_pass_rate,
                    total_token_millions=(
                        round(int(aggregate["token_count_sum"]) / 1_000_000, 3)
                        if aggregate["token_metrics_complete"]
                        else None
                    ),
                    total_token_cost=total_cost,
                    token_cost_currency=cost_currency,
                    cost_efficiency=self._cost_efficiency(avg_pass_rate, total_cost, efficiency_threshold),
                    efficiency_threshold=efficiency_threshold,
                    efficiency_eligible=avg_pass_rate >= efficiency_threshold,
                    seniority_label=self._seniority_label(avg_pass_rate, efficiency_threshold),
                    avg_runtime_seconds=avg_runtime_seconds,
                    submission_count=submission_count,
                )
            )

        leaderboard.sort(key=self._leaderboard_sort_key)
        return leaderboard

    def _emails_by_entry(self, loaded_entries: list, email_by_user: dict[str, str]) -> dict[str, list[str]]:
        """Map a competition entry to the account emails that own its score."""
        team_ids = {entry.team_id for entry in loaded_entries if getattr(entry, "team_id", None)}
        memberships = list(self.db.scalars(
            select(TeamMembership).where(TeamMembership.team_id.in_(team_ids))
        ).all()) if team_ids else []
        missing_user_ids = {
            membership.user_id for membership in memberships if str(membership.user_id) not in email_by_user
        }
        missing_user_ids.update(
            entry.user_id for entry in loaded_entries
            if getattr(entry, "user_id", None) and str(entry.user_id) not in email_by_user
        )
        if missing_user_ids:
            for user in self.db.scalars(select(User).where(User.id.in_(missing_user_ids))).all():
                email = getattr(user, "email", None)
                if email and str(email).strip():
                    email_by_user[str(user.id)] = str(email).strip().lower()
        emails_by_team: dict[str, list[str]] = {}
        for membership in memberships:
            email = email_by_user.get(str(membership.user_id))
            if not email:
                continue
            bucket = emails_by_team.setdefault(membership.team_id, [])
            if email not in bucket:
                bucket.append(email)
        emails_by_entry: dict[str, list[str]] = {}
        for entry in loaded_entries:
            if getattr(entry, "team_id", None):
                emails_by_entry[str(entry.id)] = list(emails_by_team.get(entry.team_id, []))
            elif getattr(entry, "user_id", None) and email_by_user.get(str(entry.user_id)):
                emails_by_entry[str(entry.id)] = [email_by_user[str(entry.user_id)]]
        return emails_by_entry

    @staticmethod
    def _row_account_emails(
        emails_by_entry: dict[str, list[str]],
        email_by_user: dict[str, str],
        entry_id: str | None,
        user_id: str | None,
    ) -> list[str]:
        emails = list(emails_by_entry.get(str(entry_id or ""), []))
        user_email = email_by_user.get(str(user_id or ""))
        if user_email and user_email not in emails:
            emails.append(user_email)
        return emails

    def _list_complete_competition_leaderboard(
        self,
        competition_id: str,
        task_entries: list[CatalogRequirementEntry],
        efficiency_threshold: float,
    ) -> list[CompetitionLeaderboardEntry]:
        """Rank submissions after aggregating passed tests and CNY cost.

        The original official Hackathon requires every task on a submission;
        Hackathon Evolution also ranks partial submissions, treating missing
        task results as zero passed tests and zero cost.
        """
        if not task_entries:
            return []
        competition = next(
            (item for item in self.list_competitions() if item.id == competition_id),
            None,
        )
        is_official_competition = bool(
            competition and str(competition.type or "").strip().lower() == "official"
        )
        aliases: dict[str, set[str]] = {
            task.id: {
                task.id.lower(),
                task.id.rsplit("--", 1)[-1].lower(),
                f"{competition_id}-{task.id.rsplit('--', 1)[-1]}".lower(),
            }
            for task in task_entries
        }
        task_by_id = {task.id: task for task in task_entries}
        reverse_alias = {alias: canonical for canonical, values in aliases.items() for alias in values}
        runs = self.db.scalars(
            select(Run)
            .where(Run.competition_id == competition_id)
            .where(Run.requirement_id.in_(list(reverse_alias)))
            .where(Run.status.in_([SubmissionStatus.PASSED.value, SubmissionStatus.FAILED.value]))
            .order_by(desc(Run.finished_at), desc(Run.created_at))
        ).all()
        if is_official_competition:
            runs = [
                run for run in runs
                if (run.billing_mode == "self_funded"
                    and run.finished_at is not None
                    or run.billing_mode == "official_evaluation"
                    and run.evaluation_started_at is not None
                    and run.finished_at is not None)
                and int(run.passed_count or 0) + int(run.failed_count or 0) > 0
            ]
        config = self._scoring_config(competition_id)
        submission_ids = {str(run.submission_id) for run in runs}
        submission_query = select(Submission).where(
            Submission.competition_id == competition_id,
        )
        submissions = {
            item.id: item
            for item in self.db.scalars(submission_query).all()
        } if (submission_ids or is_official_competition) else {}
        selected_submissions, _manual_ids = CompetitionLeaderboardSelectionService(self.db).resolve(submissions.values())
        best_runs: dict[tuple[str, str], tuple[Run, ScoreBreakdown]] = {}
        for run in runs:
            snapshot = submissions.get(str(run.submission_id))
            if snapshot is None or selected_submissions.get(
                str(snapshot.competition_entry_id or snapshot.user_id or "")
            ) is not snapshot:
                continue
            if is_official_competition and (
                snapshot is None
                or (snapshot.credential_mode != "official_evaluation"
                    and snapshot.credential_mode != "self_funded")
                or (snapshot.credential_mode == "official_evaluation" and snapshot.official_evaluation_started_at is None)
            ):
                continue
            canonical_task = reverse_alias.get(str(run.requirement_id or "").strip().lower())
            if canonical_task is None:
                continue
            catalog_test_total = max(0, int(task_by_id[canonical_task].total_tests))
            breakdown = compute_score(
                passed=min(max(0, int(run.passed_count)), catalog_test_total),
                test_total=catalog_test_total,
                cost=scoring_cost_for_run(run, config.currency),
                currency=config.currency,
                config=config,
            )
            if breakdown.score is None:
                continue
            key = (str(run.submission_id), canonical_task)
            previous = best_runs.get(key)
            submitted_at = submissions.get(str(run.submission_id)).created_at if submissions.get(str(run.submission_id)) else run.created_at
            if previous is None or rank_key(breakdown.score, breakdown.pass_rate, breakdown.cost, submitted_at) < rank_key(
                previous[1].score, previous[1].pass_rate, previous[1].cost, submitted_at
            ):
                best_runs[key] = (run, breakdown)

        groups: dict[str, dict[str, tuple[Run, ScoreBreakdown]]] = {}
        for (submission_id, task_id), value in best_runs.items():
            groups.setdefault(submission_id, {})[task_id] = value
        if not groups:
            return []

        user_ids = {str(run.user_id) for values in groups.values() for run, _ in values.values() if run.user_id}
        loaded_users = list(self.db.scalars(select(User).where(User.id.in_(user_ids))).all()) if user_ids else []
        usernames = {str(user.id): user.username for user in loaded_users}
        email_by_user = {
            str(user.id): str(user.email).strip().lower()
            for user in loaded_users
            if getattr(user, "email", None) and str(user.email).strip()
        }
        entry_ids = {str(run.competition_entry_id) for values in groups.values() for run, _ in values.values() if run.competition_entry_id}
        loaded_entries = list(self.db.scalars(select(CompetitionEntry).where(CompetitionEntry.id.in_(entry_ids))).all()) if entry_ids else []
        entries = {str(entry.id): entry.display_name for entry in loaded_entries}
        emails_by_entry = self._emails_by_entry(loaded_entries, email_by_user)

        # Select the highest-scoring snapshot for each user/team entry. Tasks
        # without a completed run
        # contribute zero passed tests, zero cost, and their full catalog test
        # count to the aggregate denominator.
        best_by_participant: dict[str, CompetitionLeaderboardEntry] = {}
        for submission_id, by_task in groups.items():
            # The all-tasks leaderboard is submission-based.  The original
            # Hackathon requires a valid current-suite run for every task.
            # Hackathon Evolution intentionally allows a partial submission:
            # missing tasks contribute zero passed tests and zero cost below.
            if (
                is_official_competition
                and competition_id != "hackathon-evolution"
                and set(by_task) != set(task_by_id)
            ):
                continue
            selected_runs = [value[0] for value in by_task.values()]
            first = selected_runs[0]
            passed_count = sum(
                min(max(0, int(by_task[task.id][0].passed_count)), max(0, int(task.total_tests)))
                if task.id in by_task else 0
                for task in task_entries
            )
            test_total = sum(max(0, int(task.total_tests)) for task in task_entries)
            total_cost = aggregate_run_cost(selected_runs, config.currency)
            breakdown = compute_score(
                passed=passed_count,
                test_total=test_total,
                cost=total_cost,
                currency=config.currency,
                config=config,
            )
            if breakdown.score is None:
                continue
            feature_rates = [
                float(by_task[task.id][0].feature_implementation_rate or 0.0)
                if task.id in by_task else 0.0
                for task in task_entries
            ]
            durations = [int(run.run_duration_seconds or 0) for run in selected_runs]
            token_complete = all(run.token_count is not None for run in selected_runs)
            snapshot = submissions.get(submission_id)
            submitted_at = snapshot.created_at if snapshot else min(run.created_at for run in selected_runs)
            first_entry_id = str(first.competition_entry_id) if first.competition_entry_id else ""
            username = entries.get(first_entry_id) or usernames.get(str(first.user_id), "Unknown")
            candidate = CompetitionLeaderboardEntry(
                username=username,
                account_emails=self._row_account_emails(emails_by_entry, email_by_user, first_entry_id, str(first.user_id or "")),
                model_name=first.model_name,
                funding_source=funding_source_for_runs(selected_runs),
                source_run_ids=[run.id for run in selected_runs],
                track=competition_id,
                avg_pass_rate=breakdown.pass_rate,
                avg_feature_implementation_rate=round(sum(feature_rates) / len(feature_rates), 1),
                total_token_millions=round(sum(int(run.token_count) for run in selected_runs) / 1_000_000, 3) if token_complete else None,
                total_token_cost=total_cost,
                token_cost_currency=config.currency,
                cost_efficiency=self._cost_efficiency(breakdown.pass_rate, total_cost, efficiency_threshold),
                efficiency_threshold=efficiency_threshold,
                efficiency_eligible=breakdown.pass_rate >= efficiency_threshold,
                seniority_label=self._seniority_label(breakdown.pass_rate, efficiency_threshold),
                avg_runtime_seconds=int(round(sum(durations) / len(task_entries))),
                submission_count=1,
                task_id=None,
                task_title=None,
                is_complete=set(by_task) == set(task_by_id),
                score=breakdown.score,
                score_available=True,
                pass_rate=breakdown.pass_rate,
                passed_count=passed_count,
                test_total=test_total,
                total_cost=total_cost,
                expected_cost=breakdown.expected_cost,
                cost_ratio=breakdown.ratio,
                score_regime=breakdown.regime,
                submitted_at=submitted_at.isoformat(),
                is_low_cost=total_cost is not None and total_cost < 0.01,
            )
            participant_key = first_entry_id or str(first.user_id or "")
            previous = best_by_participant.get(participant_key)
            if previous is None or self._new_leaderboard_key(candidate) < self._new_leaderboard_key(previous):
                best_by_participant[participant_key] = candidate
        leaderboard = list(best_by_participant.values())
        # Mixed legacy/new data may identify the same user by different
        # participant keys (for example, an old user-only row and a new entry
        # row). Retain the row belonging to the highest-scoring snapshot.
        by_username: dict[str, CompetitionLeaderboardEntry] = {}
        for candidate in leaderboard:
            key = candidate.username.strip().lower()
            previous = by_username.get(key)
            if previous is None or self._new_leaderboard_key(candidate) < self._new_leaderboard_key(previous):
                by_username[key] = candidate
        leaderboard = list(by_username.values())
        leaderboard.sort(key=self._new_leaderboard_key)
        for index, item in enumerate(leaderboard, 1):
            item.rank = index
        return leaderboard

    def _list_single_task_competition_leaderboard(
        self,
        competition_id: str,
        task: CatalogRequirementEntry,
        efficiency_threshold: float,
    ) -> list[CompetitionLeaderboardEntry]:
        competition = next(
            (item for item in self.list_competitions() if item.id == competition_id),
            None,
        )
        is_official_competition = bool(
            competition and str(competition.type or "").strip().lower() == "official"
        )
        aliases = {
            task.id.lower(),
            task.id.rsplit("--", 1)[-1].lower(),
            f"{competition_id}-{task.id.rsplit('--', 1)[-1]}".lower(),
        }
        runs = self.db.scalars(
            select(Run)
            .where(Run.competition_id == competition_id)
            .where(Run.requirement_id.in_(aliases))
            .where(Run.status.in_([SubmissionStatus.PASSED.value, SubmissionStatus.FAILED.value]))
        ).all()
        if is_official_competition:
            runs = [
                run for run in runs
                if (run.billing_mode == "self_funded"
                    and run.finished_at is not None
                    or run.billing_mode == "official_evaluation"
                    and run.evaluation_started_at is not None
                    and run.finished_at is not None)
                and int(run.passed_count or 0) + int(run.failed_count or 0) > 0
            ]
        config = self._scoring_config(competition_id)
        submission_ids = {str(run.submission_id) for run in runs}
        submission_query = select(Submission).where(
            Submission.competition_id == competition_id,
        )
        submissions = {
            item.id: item
            for item in self.db.scalars(submission_query).all()
        } if (submission_ids or is_official_competition) else {}
        selected_submissions, _manual_ids = CompetitionLeaderboardSelectionService(self.db).resolve(submissions.values())
        entry_ids = {str(run.competition_entry_id) for run in runs if run.competition_entry_id}
        loaded_entries = list(self.db.scalars(select(CompetitionEntry).where(CompetitionEntry.id.in_(entry_ids))).all()) if entry_ids else []
        entries = {str(entry.id): entry.display_name for entry in loaded_entries}
        user_ids = {str(run.user_id) for run in runs if run.user_id}
        loaded_users = list(self.db.scalars(select(User).where(User.id.in_(user_ids))).all()) if user_ids else []
        usernames = {str(user.id): user.username for user in loaded_users}
        email_by_user = {
            str(user.id): str(user.email).strip().lower()
            for user in loaded_users
            if getattr(user, "email", None) and str(user.email).strip()
        }
        emails_by_entry = self._emails_by_entry(loaded_entries, email_by_user)
        counts: dict[str, set[str]] = {}
        best: dict[str, CompetitionLeaderboardEntry] = {}
        for run in runs:
            snapshot = submissions.get(str(run.submission_id))
            if snapshot is None or selected_submissions.get(
                str(snapshot.competition_entry_id or snapshot.user_id or "")
            ) is not snapshot:
                continue
            if is_official_competition and (
                snapshot is None
                or (snapshot.credential_mode != "official_evaluation"
                    and snapshot.credential_mode != "self_funded")
                or (snapshot.credential_mode == "official_evaluation" and snapshot.official_evaluation_started_at is None)
            ):
                continue
            catalog_test_total = max(0, int(task.total_tests))
            breakdown = compute_score(
                passed=min(max(0, int(run.passed_count)), catalog_test_total),
                test_total=catalog_test_total,
                cost=scoring_cost_for_run(run, config.currency),
                currency=config.currency,
                config=config,
            )
            if breakdown.score is None:
                continue
            participant_key = str(run.competition_entry_id or run.user_id or "")
            if not participant_key:
                continue
            counts.setdefault(participant_key, set()).add(str(run.submission_id))
            submitted_at = snapshot.created_at if snapshot else run.created_at
            entry_id = str(run.competition_entry_id or "")
            username = entries.get(entry_id) or usernames.get(str(run.user_id), "Unknown")
            candidate = CompetitionLeaderboardEntry(
                username=username,
                account_emails=self._row_account_emails(emails_by_entry, email_by_user, entry_id, str(run.user_id or "")),
                model_name=run.model_name,
                funding_source=funding_source_for_runs([run]),
                source_run_ids=[run.id],
                track=competition_id,
                avg_pass_rate=breakdown.pass_rate,
                avg_feature_implementation_rate=float(run.feature_implementation_rate or 0.0),
                total_token_millions=round(run.token_count / 1_000_000, 3) if run.token_count is not None else None,
                total_token_cost=breakdown.cost,
                token_cost_currency=config.currency,
                cost_efficiency=self._cost_efficiency(breakdown.pass_rate, breakdown.cost, efficiency_threshold),
                efficiency_threshold=efficiency_threshold,
                efficiency_eligible=breakdown.pass_rate >= efficiency_threshold,
                seniority_label=self._seniority_label(breakdown.pass_rate, efficiency_threshold),
                avg_runtime_seconds=run.run_duration_seconds,
                submission_count=1,
                task_id=task.id.rsplit("--", 1)[-1],
                task_title=task.title,
                is_complete=True,
                score=breakdown.score,
                score_available=True,
                pass_rate=breakdown.pass_rate,
                passed_count=breakdown.passed,
                test_total=breakdown.test_total,
                total_cost=breakdown.cost,
                expected_cost=breakdown.expected_cost,
                cost_ratio=breakdown.ratio,
                score_regime=breakdown.regime,
                submitted_at=submitted_at.isoformat(),
                is_low_cost=breakdown.cost is not None and breakdown.cost < 0.01,
            )
            previous = best.get(participant_key)
            if previous is None or self._new_leaderboard_key(candidate) < self._new_leaderboard_key(previous):
                best[participant_key] = candidate
        for participant_key, item in best.items():
            item.submission_count = len(counts.get(participant_key, set()))
        by_username: dict[str, CompetitionLeaderboardEntry] = {}
        for candidate in best.values():
            username_key = candidate.username.strip().lower()
            previous = by_username.get(username_key)
            if previous is None or self._new_leaderboard_key(candidate) < self._new_leaderboard_key(previous):
                by_username[username_key] = candidate
        leaderboard = list(by_username.values())
        leaderboard.sort(key=self._new_leaderboard_key)
        for index, item in enumerate(leaderboard, 1):
            item.rank = index
        return leaderboard

    @staticmethod
    def _new_leaderboard_key(item: CompetitionLeaderboardEntry) -> tuple:
        submitted_at = datetime.fromisoformat(item.submitted_at) if item.submitted_at else datetime.max
        return rank_key(item.score, item.pass_rate, item.total_cost, submitted_at)

    @staticmethod
    def _run_pass_rate(run: Run) -> float:
        """Return the existing pass-rate value used by the leaderboard."""
        return float(run.test_pass_rate if run.test_pass_rate is not None else run.score or 0.0)

    @staticmethod
    def _leaderboard_candidate_key(item: CompetitionLeaderboardEntry) -> tuple[float, int, float]:
        """Order snapshots when selecting one best result per participant."""
        return (
            float(item.avg_pass_rate),
            1 if item.efficiency_eligible else 0,
            float(item.cost_efficiency or 0.0),
        )

    @staticmethod
    def _cost_efficiency(pass_rate: float, cost: float | None, threshold: float) -> float | None:
        if pass_rate < threshold or cost is None or cost <= 0:
            return None
        return round(pass_rate / cost, 4)

    @staticmethod
    def _seniority_label(pass_rate: float, threshold: float) -> str:
        return "senior" if pass_rate >= threshold else "junior"

    @staticmethod
    def _leaderboard_sort_key(item: CompetitionLeaderboardEntry) -> tuple:
        if item.efficiency_eligible:
            return (
                0,
                0 if item.cost_efficiency is not None else 1,
                -(item.cost_efficiency or 0.0),
                -item.avg_pass_rate,
                item.avg_runtime_seconds if item.avg_runtime_seconds is not None else 10**9,
                item.username.lower(),
                (item.model_name or "").lower(),
            )
        return (
            1,
            0,
            -item.avg_pass_rate,
            -item.submission_count,
            item.avg_runtime_seconds if item.avg_runtime_seconds is not None else 10**9,
            item.username.lower(),
            (item.model_name or "").lower(),
        )

    def list_competition_submission_history(
        self,
        competition_id: str,
        user_id: str,
    ) -> list[CompetitionSubmissionHistoryEntry]:
        """Return one row per uploaded competition agent and its latest task scores.

        Re-running a task never overwrites a previous run.  The competition view
        treats the most recently completed run of a task as that submission's
        current task score.  Missing tasks count as zero in the submission's
        average so partially run submissions cannot receive an inflated score.
        """
        normalized_competition_id = competition_id.strip().lower()
        rows = self.scan_entries()
        task_entries = [entry for entry in rows if entry.competition_id == normalized_competition_id]
        if not any(self._competition_id(root.name) == normalized_competition_id for root in self._competition_roots()):
            raise LookupError(f"Competition '{competition_id}' not found")

        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user_id))
        entry = None
        if membership and normalized_competition_id != "first-competition":
            entry = self.db.scalar(
                select(CompetitionEntry).where(
                    CompetitionEntry.competition_id == normalized_competition_id,
                    CompetitionEntry.team_id == membership.team_id,
                )
            )
        if entry is None:
            entry = self.db.scalar(
                select(CompetitionEntry).where(
                    CompetitionEntry.competition_id == normalized_competition_id,
                    CompetitionEntry.user_id == user_id,
                )
            )
        team = self.db.get(Team, entry.team_id) if entry and entry.team_id else None
        can_select_for_leaderboard = not (entry and entry.team_id) or bool(team and team.leader_user_id == user_id)
        return self._competition_submission_history(
            normalized_competition_id,
            task_entries=task_entries,
            competition_entry_id=entry.id if entry else None,
            legacy_user_id=user_id,
            can_select_for_leaderboard=can_select_for_leaderboard,
        )

    def _competition_submission_history(
        self,
        competition_id: str,
        *,
        task_entries: list[CatalogRequirementEntry],
        competition_entry_id: str | None,
        legacy_user_id: str | None,
        require_complete: bool = False,
        can_select_for_leaderboard: bool = False,
    ) -> list[CompetitionSubmissionHistoryEntry]:
        snapshots_query = (
            select(Submission)
            .where(Submission.deprecated_at.is_(None))
            .where(Submission.competition_id == competition_id)
            .order_by(desc(Submission.created_at))
        )
        if competition_entry_id:
            snapshots_query = snapshots_query.where(Submission.competition_entry_id == competition_entry_id)
        elif legacy_user_id:
            snapshots_query = snapshots_query.where(
                Submission.user_id == legacy_user_id,
                Submission.competition_entry_id.is_(None),
            )
        else:
            return []
        snapshots = self.db.scalars(snapshots_query).all()
        from app.services.competition_submission_policy import deletion_reason
        latest_snapshot = max(snapshots, key=lambda item: (item.created_at, item.id), default=None)
        deletion_runs = self.db.scalars(select(Run).where(
            Run.submission_id == latest_snapshot.id
        )).all() if latest_snapshot else []

        snapshot_ids = [snapshot.id for snapshot in snapshots]
        completed_statuses = [SubmissionStatus.PASSED.value, SubmissionStatus.FAILED.value]
        task_ids = [task.id for task in task_entries]
        # A few submissions were created before competition tasks were moved
        # under ``data/competition`` and therefore retain the old benchmark
        # requirement id (for example ``12306``). Accept both identifiers so
        # those runs still contribute to the same competition task.
        task_id_aliases = {
            task.id: {
                task.id.lower(),
                task.id.rsplit("--", 1)[-1].lower(),
                f"{competition_id}-{task.id.rsplit('--', 1)[-1]}".lower(),
            }
            for task in task_entries
        }
        task_by_id = {task.id: task for task in task_entries}
        run_query = (
            select(Run)
            .where(Run.requirement_id.in_([alias for aliases in task_id_aliases.values() for alias in aliases]))
            .where(Run.status.in_(completed_statuses))
            .order_by(desc(Run.finished_at), desc(Run.created_at))
        )
        # Normally a run points to its immutable upload snapshot. Older
        # competition runs may have been written before that relation was
        # stabilized; retain them when their competition entry/user matches.
        if competition_entry_id:
            run_query = run_query.where(
                (Run.submission_id.in_(snapshot_ids))
                | (Run.competition_entry_id == competition_entry_id)
            )
        elif legacy_user_id:
            run_query = run_query.where(
                (Run.submission_id.in_(snapshot_ids))
                | (Run.user_id == legacy_user_id)
            )
        else:
            return []
        runs = self.db.scalars(run_query).all()

        snapshots.sort(key=lambda item: item.created_at, reverse=True)
        config = self._scoring_config(competition_id)
        snapshot_created_at = {snapshot.id: snapshot.created_at for snapshot in snapshots}
        root = next((root for root in self._competition_roots() if self._competition_id(root.name) == competition_id), None)
        progressive_routes = self._read_competition_config(root).get("progressive_tasks", {})

        # For each uploaded snapshot and task, use its best historical run.
        # A completed run with a zero score is still a real result and must
        # remain visible in the task matrix.
        best_runs: dict[tuple[str, str], tuple[Run, ScoreBreakdown]] = {}
        for run in runs:
            raw_requirement_id = str(run.requirement_id or "").strip().lower()
            task_id = next(
                (canonical for canonical, aliases in task_id_aliases.items() if raw_requirement_id in aliases),
                None,
            )
            # Legacy rows occasionally used a compound separator or retained
            # only the final task directory. Resolve by the final component as
            # a last resort, while still requiring one run per catalog task.
            if task_id is None:
                suffix = raw_requirement_id.rsplit("--", 1)[-1].rsplit("-", 1)[-1]
                task_id = next(
                    (canonical for canonical, aliases in task_id_aliases.items()
                     if suffix == canonical.rsplit("--", 1)[-1].lower()),
                    raw_requirement_id,
                )
            key = (str(run.submission_id), task_id)
            catalog_task = task_by_id.get(task_id)
            catalog_test_total = max(0, int(catalog_task.total_tests)) if catalog_task else 0
            breakdown = compute_score(
                passed=min(max(0, int(run.passed_count)), catalog_test_total),
                test_total=catalog_test_total,
                cost=scoring_cost_for_run(run, config.currency),
                currency=config.currency,
                config=config,
            )
            previous = best_runs.get(key)
            submitted_at = snapshot_created_at.get(str(run.submission_id), run.created_at)
            if previous is None or rank_key(breakdown.score, breakdown.pass_rate, breakdown.cost, submitted_at) < rank_key(
                previous[1].score, previous[1].pass_rate, previous[1].cost, submitted_at
            ):
                best_runs[key] = (run, breakdown)

        history: list[CompetitionSubmissionHistoryEntry] = []
        for snapshot in snapshots:
            delete_block_reason = deletion_reason(
                self.db, snapshot, latest_id=latest_snapshot.id if latest_snapshot else None,
                runs=deletion_runs if latest_snapshot and snapshot.id == latest_snapshot.id else [],
            )
            task_scores: list[CompetitionTaskRunScore] = []
            feature_rate_sum = 0.0
            duration_sum = 0
            token_count_sum = 0
            token_metrics_complete = True
            token_cost_sum = 0.0
            cost_metrics_complete = True
            cost_currencies: set[str] = set()
            completed_run_count = 0
            passed_count = 0
            test_total = sum(max(0, int(task.total_tests)) for task in task_entries)
            for task in task_entries:
                selected_run = best_runs.get((snapshot.id, task.id))
                if selected_run is None:
                    task_scores.append(
                        CompetitionTaskRunScore(
                            task_id=task.id,
                            task_title=task.title,
                            test_pass_rate=0.0,
                            token_cost_usd=0.0,
                            token_cost_currency=config.currency,
                            score=0.0,
                            passed_count=0,
                            test_total=max(0, int(task.total_tests)),
                            score_regime="zero",
                        )
                    )
                    continue
                run, task_breakdown = selected_run
                task_test_total = max(0, int(task.total_tests))
                task_passed = min(task_breakdown.passed, task_test_total)
                test_pass_rate = 100.0 * task_passed / task_test_total if task_test_total else 0.0
                feature_rate = run.feature_implementation_rate
                feature_rate_sum += float(feature_rate or 0.0)
                duration_sum += int(run.run_duration_seconds or 0)
                completed_run_count += 1
                passed_count += task_passed
                if run.token_count is None:
                    token_metrics_complete = False
                else:
                    token_count_sum += int(run.token_count)
                if task_breakdown.cost is None:
                    cost_metrics_complete = False
                else:
                    token_cost_sum += task_breakdown.cost
                    cost_currencies.add(config.currency)
                task_scores.append(
                    CompetitionTaskRunScore(
                        task_id=task.id,
                        task_title=task.title,
                        run_id=run.id,
                        status=run.status,
                        test_pass_rate=test_pass_rate,
                        feature_implementation_rate=feature_rate,
                        run_duration_seconds=run.run_duration_seconds,
                        token_count=run.token_count,
                        token_cost_usd=task_breakdown.cost,
                        token_cost_currency=config.currency if task_breakdown.cost is not None else None,
                        completed_at=run.finished_at.isoformat() if run.finished_at else None,
                        score=task_breakdown.score,
                        passed_count=task_passed,
                        test_total=task_test_total,
                        score_regime=task_breakdown.regime,
                    )
                )
            task_count = len(task_entries)
            aggregate_currency = (
                next(iter(cost_currencies))
                if completed_run_count > 0 and cost_metrics_complete and len(cost_currencies) == 1
                else None
            )
            aggregate = compute_score(
                passed=passed_count,
                test_total=test_total,
                cost=token_cost_sum if aggregate_currency else None,
                currency=aggregate_currency,
                config=config,
            )
            if progressive_routes:
                # Snapshot summaries must not count the full task and its
                # alternative stages twice. The selected submission score
                # is exposed separately on CompetitionDetail.current_score.
                scored = {score.task_id.rsplit("--", 1)[-1]: score for score in task_scores}
                logical = [task for task in task_entries if self.stage_info(task.id)[1] is None]
                chosen = []
                for task in logical:
                    slug = task.id.rsplit("--", 1)[-1]
                    direct = scored[slug]
                    direct_score = compute_score(passed=direct.passed_count, test_total=task.total_tests,
                                                 cost=direct.token_cost_usd if direct.run_id else 0,
                                                 currency=direct.token_cost_currency or config.currency, config=config)
                    candidates = [direct_score]
                    route = progressive_routes.get(slug)
                    if route:
                        stages = [scored.get(f"{slug}-stage-{n}") for n in range(1, int(route.get("stage_count", 3)) + 1)]
                        total = int(route.get("test_total") or task.total_tests)
                        stage_passed = sum(stage.passed_count for stage in stages if stage)
                        candidates.append(compute_score(
                            passed=stage_passed, test_total=total,
                            cost=sum_scoring_costs(stage.token_cost_usd for stage in stages if stage),
                            currency=config.currency, config=config,
                        ))
                    chosen.append(min(candidates, key=lambda result: rank_key(result.score, result.pass_rate, result.cost, snapshot.created_at)))
                passed_count = sum(result.passed for result in chosen)
                test_total = sum(result.test_total for result in chosen)
                aggregate = compute_score(passed=passed_count, test_total=test_total,
                                          cost=sum_scoring_costs(result.cost for result in chosen),
                                          currency=config.currency, config=config)
            history.append(
                CompetitionSubmissionHistoryEntry(
                    id=snapshot.id,
                    display_name=snapshot.display_name,
                    model_name=snapshot.model_name,
                    original_filename=snapshot.original_filename,
                    runtime=snapshot.runtime,
                    credential_mode=snapshot.credential_mode,
                    can_delete=delete_block_reason is None,
                    can_select_for_leaderboard=can_select_for_leaderboard and delete_block_reason is None,
                    delete_block_reason=delete_block_reason,
                    created_at=snapshot.created_at.isoformat(),
                    task_scores=task_scores,
                    average_test_pass_rate=round(aggregate.pass_rate, 1),
                    average_feature_implementation_rate=round(feature_rate_sum / task_count, 1) if task_count else 0.0,
                    total_run_duration_seconds=duration_sum,
                    total_token_count=(
                        token_count_sum if completed_run_count > 0 and token_metrics_complete else None
                    ),
                    token_cost_usd=(
                        token_cost_sum if completed_run_count > 0 and cost_metrics_complete and len(cost_currencies) == 1 else None
                    ),
                    token_cost_currency=(
                        aggregate_currency
                    ),
                    score=aggregate.score,
                    score_available=aggregate.score is not None,
                    pass_rate=aggregate.pass_rate,
                    passed_count=passed_count,
                    test_total=test_total,
                    total_cost=aggregate.cost,
                )
            )

        choices, manual_ids = CompetitionLeaderboardSelectionService(self.db).resolve(snapshots)
        selected_ids = {snapshot.id for snapshot in choices.values()}
        for item in history:
            item.is_selected_score = item.id in selected_ids
            item.leaderboard_selection_mode = "manual" if manual_ids else "auto"
        return history

    def get_competition_detail(self, competition_id: str, base_url: str, user: User | None = None) -> CompetitionDetail:
        rows = self.scan_entries()
        display_ids = self._build_display_id_map(rows)
        competition_tasks = [row for row in rows if row.competition_id == competition_id]
        competition_root = next((root for root in self._competition_roots() if self._competition_id(root.name) == competition_id), None)
        if competition_root is None:
            raise LookupError(f"Competition '{competition_id}' not found")

        tasks = [
            self._to_competition_task(row, base_url, is_public=False, display_id=display_ids.get(row.id, row.id))
            for row in competition_tasks
        ]
        routes = self._read_competition_config(competition_root).get("progressive_tasks", {})
        for task in tasks:
            base, stage = self.stage_info(task.id)
            if stage is None or base not in routes:
                continue
            task.stage_number = stage
            task.scoring_task_id = f"{competition_id}--{base}"
            if stage > 1:
                task.previous_stage_id = f"{competition_id}--{base}-stage-{stage - 1}"
                from app.services.competition_template_service import CompetitionTemplateService
                source = CompetitionTemplateService(self.db).resolve_stage_baseline(competition_id, task.id, user) if user else None
                task.source_run_id = source.run_id if source else "blank"
        summary = self._competition_summary_model(competition_id, competition_root.name, competition_tasks, competition_root)
        description_markdown = self._read_competition_markdown(competition_root)
        current_score = None
        if routes and user:
            membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user.id))
            entry = self.db.scalar(select(CompetitionEntry).where(
                CompetitionEntry.competition_id == competition_id,
                CompetitionEntry.team_id == membership.team_id,
            )) if membership else None
            from app.services.progressive_competition_service import ProgressiveCompetitionService
            scores = ProgressiveCompetitionService(self).leaderboard(competition_id, competition_tasks, participant_id=entry.id if entry else user.id)
            current_score = scores[0] if scores else None
        return CompetitionDetail(
            **summary.model_dump(),
            downloads=None,
            tasks=tasks,
            current_score=current_score,
            description_markdown=description_markdown,
            flow=[
                "Upload an agent snapshot or select a built-in one.",
                "Compile the large requirement into smaller runnable modules.",
                "Run the GitHub-style and spreadsheet-style tasks in order.",
                "Inspect evidence and refine the system until the output is stable.",
            ],
            rules=[
                "Treat the requirement document as the source of truth.",
                "Keep changes small enough to verify each step.",
                "Use the task artifacts to explain what your agent changed.",
            ],
        )

    def _competition_summary_model(
        self,
        competition_id: str,
        directory_name: str,
        tasks: list[CatalogRequirementEntry],
        competition_root: Path | None = None,
    ) -> CompetitionSummary:
        config = self._read_competition_config(competition_root)
        is_hackathon = "hackathon" in competition_id
        is_evolution = competition_id == "hackathon-evolution"
        is_smoke = competition_id == "smoke"
        is_smoke_evolution = competition_id == "smoke-evolution"
        title = str(config.get("title") or "").strip() or (
            "Agentic Software Factory Hackathon (Evolution)"
            if is_evolution
            else "Agentic Software Factory Hackathon"
            if is_hackathon
            else "Smoke Competition (Evolution)"
            if is_smoke_evolution
            else "Smoke Competition"
            if is_smoke
            else "Demo Competition"
            if competition_id == "demo"
            else directory_name
        )
        configured_description = str(config.get("description") or "").strip()
        configured_starts_at = str(config.get("starts_at") or "").strip() or None
        configured_ends_at = str(config.get("ends_at") or "").strip() or None
        configured_status = str(config.get("status") or "").strip().lower()
        configured_type = str(config.get("type") or "").strip().lower()
        if competition_id == "first-competition" and configured_status == "open":
            today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
            if configured_starts_at and today < configured_starts_at[:10]:
                configured_status = "upcoming"
            elif configured_ends_at and today > configured_ends_at[:10]:
                configured_status = "ended"
        return CompetitionSummary(
            id=competition_id,
            title=title,
            type=configured_type or ("hackathon" if is_hackathon else "demo"),
            summary=(
                configured_description
                or (
                    "An empty practice competition for validating the ARC-Bench submission workflow."
                    if competition_id == "demo"
                    else "Given an existing software system, extend its features and functionality from expanded requirements without breaking existing behavior."
                    if is_evolution
                    else "Extend the generated counter and dice applications with small new behaviors while preserving their existing features."
                    if is_smoke_evolution
                    else "Build minimal counter and dice applications to validate the competition submission, run, and leaderboard pipeline."
                    if is_smoke
                    else "A software-factory hackathon that compiles a large requirement into a GitHub-style collaboration system and a Google Sheets-style data workspace."
                    if is_hackathon
                    else self._competition_summary(competition_id, len(tasks))
                )
            ),
            task_count=len(tasks),
            total_tests=sum(task.total_tests for task in tasks),
            is_public=True,
            starts_at=configured_starts_at or ("2026-09-01" if is_hackathon else None),
            ends_at=configured_ends_at or ("2026-10-17" if is_hackathon else None),
            status=configured_status or ("upcoming" if is_hackathon or not tasks else "open"),
            notice=(
                "Evolve the existing GitHub-style and spreadsheet-style systems while preserving all previously supported behavior."
                if is_evolution
                else "Use generated outputs from the Smoke Competition as the starting templates for both evolution tasks."
                if is_smoke_evolution
                else "Two compact task packs are available for fast end-to-end pipeline checks."
                if is_smoke
                else "The hackathon focuses on software factory decomposition, requirements compilation, and two concrete task packs."
                if is_hackathon
                else "No tasks have been published yet. Add task folders directly to the competition directory."
                if not tasks
                else "Task packs are ready. Create a submission from this competition page."
            ),
            template_required=bool(config.get("template_required", False)),
            template_source_competition_id=(
                str(config.get("template_source_competition_id") or "").strip().lower() or None
            ),
            initial_budget_cny=0.0,
        )

    @staticmethod
    def _read_competition_config(competition_root: Path | None) -> dict[str, Any]:
        if competition_root is None:
            return {}
        config_path = competition_root / "competition.yaml"
        if not config_path.is_file():
            return {}
        try:
            payload = yaml.load(config_path.read_text(encoding="utf-8"), Loader=_YamlLoader) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"Invalid competition configuration: {config_path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Competition configuration must be a mapping: {config_path}")
        return payload

    @staticmethod
    def _read_competition_markdown(competition_root: Path | None) -> str:
        """Read the optional human-authored brief beside competition.yaml."""
        if competition_root is None:
            return ""
        markdown_path = competition_root / "competition.md"
        try:
            return markdown_path.read_text(encoding="utf-8").strip() if markdown_path.is_file() else ""
        except OSError as exc:
            raise ValueError(f"Unable to read competition description: {markdown_path}") from exc

    def list_benchmarks(self, base_url: str) -> list[BenchmarkSummary]:
        rows = self.scan_entries()
        grouped: dict[str, list[CatalogRequirementEntry]] = {}
        for row in rows:
            grouped.setdefault(row.category, []).append(row)

        benchmarks: list[BenchmarkSummary] = []
        for category, items in sorted(grouped.items(), key=lambda item: self._competition_sort_key(item[0])):
            benchmarks.append(
                BenchmarkSummary(
                    id=category,
                    title=self._benchmark_title(category),
                    type=category,
                    summary=self._benchmark_summary(category, len(items)),
                    task_count=len(items),
                    total_tests=sum(item.total_tests for item in items),
                    downloads=BenchmarkDownloadLinks(
                        track_bundle=f"{base_url}/api/benchmarks/{category}/download",
                    ),
                )
            )
        return benchmarks

    def get_benchmark_detail(self, benchmark_id: str, base_url: str) -> BenchmarkDetail:
        rows = self.scan_entries()
        display_ids = self._build_display_id_map(rows)
        benchmark_tasks = [row for row in rows if row.category == benchmark_id]
        if not benchmark_tasks:
            raise LookupError(f"Benchmark '{benchmark_id}' not found")

        tasks = [
            self._to_benchmark_task_summary(row, base_url, display_ids.get(row.id, row.id))
            for row in benchmark_tasks
        ]
        return BenchmarkDetail(
            id=benchmark_id,
            title=self._benchmark_title(benchmark_id),
            type=benchmark_id,
            summary=self._benchmark_summary(benchmark_id, len(tasks)),
            task_count=len(tasks),
            total_tests=sum(task.total_tests for task in tasks),
            downloads=BenchmarkDownloadLinks(
                track_bundle=f"{base_url}/api/benchmarks/{benchmark_id}/download",
            ),
            tasks=tasks,
        )

    def get_document(self, requirement_id: str, kind: str) -> str:
        requirement = self.get_entry(requirement_id)
        if kind == "requirements":
            self._ensure_requirement_markdown(requirement)
        path = requirement.requirements_path if kind == "requirements" else requirement.prerequisites_path
        return self._read_text_if_exists(path)

    def get_asset_path(self, requirement_id: str, asset_kind: str, relative_path: str) -> Path:
        requirement = self.get_entry(requirement_id)
        base_dir = requirement.assets_path if asset_kind == "assets" else requirement.references_path
        if not base_dir.exists():
            raise FileNotFoundError(relative_path)
        target = (base_dir / relative_path).resolve()
        if not str(target).startswith(str(base_dir.resolve())) or not target.exists():
            raise FileNotFoundError(relative_path)
        return target

    def build_public_task_bundle(self, requirement_id: str) -> tuple[bytes, str]:
        requirement = self.get_entry(requirement_id)
        if self._is_official_competition(requirement.competition_id):
            raise PermissionError("Evaluator tests are private for official competitions")
        self._ensure_requirement_markdown(requirement)
        archive_name = f"arcbench-public-{requirement_id}.zip"
        entries = [
            (requirement.requirements_path, f"{requirement.id}/requirements/requirements.md"),
            (requirement.prerequisites_path, f"{requirement.id}/requirements/prerequisites.md"),
            (requirement.tests_path, f"{requirement.id}/tests"),
            (requirement.assets_path, f"{requirement.id}/requirements/assets"),
            (requirement.references_path, f"{requirement.id}/requirements/reference"),
        ]
        entries.append((requirement.requirements_yaml_path, f"{requirement.id}/requirements/requirements.yaml"))
        return self._build_zip(entries, archive_name)

    def build_public_task_document(self, requirement_id: str, kind: str) -> tuple[bytes, str]:
        requirement = self.get_entry(requirement_id)
        if kind == "requirements":
            self._ensure_requirement_markdown(requirement)
        source = requirement.requirements_path if kind == "requirements" else requirement.prerequisites_path
        return self._read_bytes_if_exists(source), source.name

    def build_public_task_tests_bundle(self, requirement_id: str) -> tuple[bytes, str]:
        requirement = self.get_entry(requirement_id)
        if self._is_official_competition(requirement.competition_id):
            raise PermissionError("Evaluator tests are private for official competitions")
        archive_name = f"arcbench-public-{requirement_id}-tests.zip"
        return self._build_zip([(requirement.tests_path, f"{requirement.id}/tests")], archive_name)

    def _is_official_competition(self, competition_id: str | None) -> bool:
        if not competition_id:
            return False
        root = next(
            (root for root in self._competition_roots() if self._competition_id(root.name) == competition_id),
            None,
        )
        return bool(
            root
            and str(self._read_competition_config(root).get("type") or "").strip().lower() == "official"
        )

    def build_public_task_demo_bundle(self, requirement_id: str) -> tuple[bytes, str]:
        requirement = self.get_entry(requirement_id)
        archive_name = f"arcbench-public-{requirement_id}-demo.zip"
        return self._build_zip(
            [
                (requirement.assets_path, f"{requirement.id}/assets"),
                (requirement.references_path, f"{requirement.id}/reference"),
            ],
            archive_name,
        )

    def build_public_competition_bundle(self) -> tuple[bytes, str]:
        rows = self.scan_entries()
        private_test_competition_ids = {
            self._competition_id(root.name)
            for root in self._competition_roots()
            if str(self._read_competition_config(root).get("type") or "").strip().lower() == "official"
        }
        entries: list[tuple[Path, str]] = []
        for requirement in rows:
            self._ensure_requirement_markdown(requirement)
            entries.extend(
                [
                    (requirement.requirements_path, f"public/{requirement.id}/requirements/requirements.md"),
                    (requirement.prerequisites_path, f"public/{requirement.id}/requirements/prerequisites.md"),
                    (requirement.assets_path, f"public/{requirement.id}/requirements/assets"),
                    (requirement.references_path, f"public/{requirement.id}/requirements/reference"),
                ]
            )
            if requirement.competition_id not in private_test_competition_ids:
                entries.append((requirement.tests_path, f"public/{requirement.id}/tests"))
            entries.append((requirement.requirements_yaml_path, f"public/{requirement.id}/requirements/requirements.yaml"))
        return self._build_zip(entries, "arcbench-public-competition.zip")

    def build_competition_requirements_bundle(self, competition_id: str) -> tuple[bytes, str]:
        """Build the participant-facing requirements/reference archive.

        Tests, assets, and prerequisites are intentionally excluded.  This is
        used by the official Hackathon where the evaluator's test suite must
        remain private while the requirement tree and reference material stay
        downloadable as one reproducible archive.
        """
        normalized_id = competition_id.strip().lower()
        rows = [row for row in self.scan_entries() if row.competition_id == normalized_id]
        if not rows:
            raise LookupError(f"Competition '{competition_id}' not found")
        entries: list[tuple[Path, str]] = []
        for requirement in rows:
            entries.append((requirement.requirements_yaml_path, f"{requirement.id}/requirements.yaml"))
            if requirement.references_path.exists():
                entries.append((requirement.references_path, f"{requirement.id}/reference"))
        return self._build_zip(entries, f"arcbench-{normalized_id}-requirements.zip")

    def build_benchmark_track_bundle(self, benchmark_id: str) -> tuple[bytes, str]:
        rows = self.scan_entries()
        benchmark_tasks = [row for row in rows if row.category == benchmark_id]
        if not benchmark_tasks:
            raise LookupError(f"Benchmark '{benchmark_id}' not found")

        entries: list[tuple[Path, str]] = []
        for requirement in benchmark_tasks:
            root = f"{benchmark_id}"
            entries.extend(
                [
                    (requirement.requirements_path.parent, f"{root}/requirements/{requirement.id}"),
                    (requirement.tests_path, f"{root}/tests/{requirement.id}"),
                ]
            )

        readme = self._build_benchmark_bundle_readme(benchmark_id, benchmark_tasks)
        return self._build_zip_with_virtual_files(
            entries,
            [(f"{benchmark_id}/README.md", readme)],
            f"arcbench-{benchmark_id}-bundle.zip",
        )

    def build_benchmark_task_bundle(self, requirement_id: str) -> tuple[bytes, str]:
        requirement = self.get_entry(requirement_id)
        archive_name = f"arcbench-{requirement.id}-bundle.zip"
        entries = [
            (requirement.requirements_path.parent, f"{requirement.id}/requirements"),
            (requirement.tests_path, f"{requirement.id}/tests"),
        ]
        readme = self._build_benchmark_bundle_readme(requirement.category, [requirement])
        return self._build_zip_with_virtual_files(
            entries,
            [(f"{requirement.id}/README.md", readme)],
            archive_name,
        )

    def get_entry(self, requirement_id: str, rows: list[CatalogRequirementEntry] | None = None) -> CatalogRequirementEntry:
        entries = rows if rows is not None else self.scan_entries()
        for entry in entries:
            if entry.id == requirement_id:
                return entry
        raise LookupError(f"Requirement '{requirement_id}' not found")

    def _ensure_requirement_markdown(self, requirement: CatalogRequirementEntry) -> None:
        if (not requirement.requirements_path.is_file()
                or requirement.requirements_yaml_path.stat().st_mtime > requirement.requirements_path.stat().st_mtime):
            self._render_requirement_markdown(requirement.requirements_yaml_path)

    def get_requirement_tests(self, requirement_id: str) -> RequirementTests:
        requirement = self.get_entry(requirement_id)
        if not requirement.tests_path.is_dir():
            return RequirementTests()

        files: list[RequirementTestFile] = []
        for path in sorted(requirement.tests_path.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            files.append(RequirementTestFile(path=path.relative_to(requirement.tests_path).as_posix(), content=content))
        return RequirementTests(files=files)

    def _build_zip(self, entries: list[tuple[Path, str]], archive_name: str) -> tuple[bytes, str]:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source, target in entries:
                if not source.exists():
                    continue
                if source.is_dir():
                    for path in sorted(source.rglob("*")):
                        if path.is_file():
                            archive.write(path, arcname=f"{target}/{path.relative_to(source).as_posix()}")
                else:
                    archive.write(source, arcname=target)
        return buffer.getvalue(), archive_name

    def _build_zip_with_virtual_files(
        self,
        entries: list[tuple[Path, str]],
        virtual_files: list[tuple[str, str]],
        archive_name: str,
    ) -> tuple[bytes, str]:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source, target in entries:
                if not source.exists():
                    continue
                if source.is_dir():
                    for path in sorted(source.rglob("*")):
                        if path.is_file():
                            archive.write(path, arcname=f"{target}/{path.relative_to(source).as_posix()}")
                else:
                    archive.write(source, arcname=target)
            for target, content in virtual_files:
                archive.writestr(target, content)
        return buffer.getvalue(), archive_name

    def _to_requirement_summary(self, row: CatalogRequirementEntry, display_id: str) -> RequirementSummary:
        return RequirementSummary(
            id=row.id,
            display_id=display_id,
            title=row.title,
            category=row.category,
            summary=row.summary,
            test_runner=row.test_runner,
            total_tests=row.total_tests,
            module_count=row.module_count,
        )

    def _to_benchmark_task_summary(
        self,
        row: CatalogRequirementEntry,
        base_url: str,
        display_id: str,
    ) -> BenchmarkTaskSummary:
        return BenchmarkTaskSummary(
            id=row.id,
            display_id=display_id,
            title=row.title,
            category=row.category,
            summary=row.summary,
            test_runner=row.test_runner,
            total_tests=row.total_tests,
            module_count=row.module_count,
            downloads=BenchmarkDownloadLinks(
                task_bundle=f"{base_url}/api/benchmarks/tasks/{row.id}/download",
            ),
        )

    def _to_competition_task(
        self,
        row: CatalogRequirementEntry,
        base_url: str,
        is_public: bool,
        display_id: str,
    ) -> CompetitionTaskSummary:
        downloads = None
        if is_public:
            downloads = CompetitionTaskDownloadLinks(
                requirement_document=f"{base_url}/api/competitions/public/tasks/{row.id}/download/requirements",
                prerequisites_document=f"{base_url}/api/competitions/public/tasks/{row.id}/download/prerequisites",
                tests_bundle=f"{base_url}/api/competitions/public/tasks/{row.id}/download/tests",
                demo_bundle=f"{base_url}/api/competitions/public/tasks/{row.id}/download/demo",
                full_bundle=f"{base_url}/api/competitions/public/tasks/{row.id}/download/full",
            )
        return CompetitionTaskSummary(
            id=row.id,
            display_id=display_id,
            title=row.title,
            category=row.category,
            summary=row.summary,
            test_runner=row.test_runner,
            total_tests=row.total_tests,
            module_count=row.module_count,
            assets_base_url=f"{base_url}/api/requirements/{row.id}/assets?catalog=competition",
            references_base_url=f"{base_url}/api/requirements/{row.id}/references?catalog=competition",
            public_downloads=downloads,
        )

    @staticmethod
    def _build_display_id_map(rows: list[CatalogRequirementEntry]) -> dict[str, str]:
        grouped: dict[str, list[CatalogRequirementEntry]] = {}
        for row in rows:
            grouped.setdefault(row.category, []).append(row)

        display_ids: dict[str, str] = {}
        for _, items in sorted(grouped.items(), key=lambda item: RequirementCatalogService._competition_sort_key(item[0])):
            for index, row in enumerate(items, start=1):
                display_ids[row.id] = f"TASK-{index:03d}"
        return display_ids

    @staticmethod
    def _competition_sort_key(category: str) -> tuple[int, str]:
        priority = {
            "web": 0,
            "mobile": 1,
            "android": 1,
        }
        return (priority.get(category, 99), category)

    def _count_leaf_nodes(self, node: dict[str, Any]) -> int:
        children = node.get("children")
        if not isinstance(children, list) or len(children) == 0:
            return 1
        valid_children = [child for child in children if isinstance(child, dict)]
        if len(valid_children) == 0:
            return 1
        return sum(self._count_leaf_nodes(child) for child in valid_children)

    @staticmethod
    def _count_test_cases(tests_path: Path) -> int:
        if not tests_path.exists() or not tests_path.is_dir():
            return 0
        declaration = re.compile(r"\b(?:test|it)(?:\.(?:only|skip|todo|fails))?\s*\(")
        total = 0
        for path in tests_path.rglob("*"):
            if not path.is_file():
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            total += len(declaration.findall(source))
        return total

    @staticmethod
    def _read_text_if_exists(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _read_bytes_if_exists(path: Path) -> bytes:
        if not path.exists():
            return b""
        return path.read_bytes()

    @staticmethod
    def _competition_title(category: str) -> str:
        if category == "web":
            return "Web Competition"
        if category == "mobile":
            return "Mobile Competition"
        if category == "android":
            return "Mobile Competition"
        return f"{category.title()} Competition"

    @staticmethod
    def _competition_summary(category: str, task_count: int) -> str:
        if category == "web":
            return f"Browser-based product tasks with Playwright evaluation across {task_count} benchmark tasks."
        if category in {"mobile", "android"}:
            return f"Mobile application tasks across {task_count} benchmark tasks."
        return f"{task_count} benchmark tasks in the {category} track."

    @staticmethod
    def _benchmark_title(category: str) -> str:
        if category == "web":
            return "Web Applications"
        if category in {"mobile", "android"}:
            return "Mobile Applications"
        return f"{category.title()} Applications"

    @staticmethod
    def _benchmark_summary(category: str, task_count: int) -> str:
        if category == "web":
            return f"ARC-Bench web application tasks with executable test suites across {task_count} benchmarks."
        if category in {"mobile", "android"}:
            return f"ARC-Bench mobile application tasks across {task_count} benchmarks."
        return f"ARC-Bench tasks across {task_count} benchmarks in the {category} track."

    @staticmethod
    def _build_benchmark_bundle_readme(category: str, tasks: list[CatalogRequirementEntry]) -> str:
        title = RequirementCatalogService._benchmark_title(category)
        lines = [
            f"# ARC-Bench / {title}",
            "",
            "This archive contains the requirement documents and test suites for this ARC-Bench track.",
            "",
            "## Included",
            "",
            "- `requirements/<task>/` with every file from the task requirement directory",
            "- `tests/<task>/` containing the benchmark test cases",
            "",
            "## How to run tests",
            "",
            "1. Prepare the corresponding project template or generated implementation.",
            "2. Install the task runtime dependencies required by the target project.",
            "3. Run the provided test suite inside the benchmark runner environment.",
            "4. For web tasks, the tests are Playwright-based and expect the target app to be running.",
            "",
            "## Tasks",
            "",
        ]
        for item in tasks:
            lines.append(f"- `{item.id}`: {item.title}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _extract_title(markdown: str, fallback: str) -> str:
        for line in markdown.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[2:].strip() if stripped.startswith("#") else stripped
        return fallback

    @staticmethod
    def _extract_summary(markdown: str) -> str:
        non_empty_lines = [line.strip() for line in markdown.splitlines() if line.strip()]
        if len(non_empty_lines) >= 2:
            return non_empty_lines[1]
        return ""
