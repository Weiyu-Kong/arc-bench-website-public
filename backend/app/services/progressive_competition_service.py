"""Read-time scoring for alternative full-task and progressive-stage routes."""

from datetime import datetime

from sqlalchemy import select

from app.models.competition_account import CompetitionEntry
from app.models.run import Run
from app.models.submission import Submission
from app.models.user import User
from app.schemas.requirement import CompetitionLeaderboardEntry
from app.services.competition_leaderboard_selection_service import CompetitionLeaderboardSelectionService
from app.services.leaderboard_scoring import (
    aggregate_run_cost,
    scoring_cost_for_run,
    sum_scoring_costs,
    compute_score,
    funding_source_for_runs,
    rank_key,
)


class ProgressiveCompetitionService:
    def __init__(self, catalog):
        self.catalog = catalog
        self.db = catalog.db

    def leaderboard(self, competition_id, tasks, task_id=None, participant_id=None):
        config = self.catalog._scoring_config(competition_id)
        root = next((root for root in self.catalog._competition_roots() if root.name == competition_id), None)
        routes = self.catalog._read_competition_config(root).get("progressive_tasks", {})
        by_slug = {task.id.rsplit("--", 1)[-1]: task for task in tasks}
        snapshot_query = select(Submission).where(Submission.competition_id == competition_id, Submission.deprecated_at.is_(None))
        snapshots = {snapshot.id: snapshot for snapshot in self.db.scalars(snapshot_query).all()}
        selected_snapshots, _manual_ids = CompetitionLeaderboardSelectionService(self.db).resolve(snapshots.values())
        runs = self.db.scalars(select(Run).where(
            Run.competition_id == competition_id,
            Run.status.in_(["PASSED", "FAILED"]),
        )).all()
        latest = {}
        for run in runs:
            snapshot = snapshots.get(run.submission_id)
            slug = str(run.requirement_id or "").rsplit("--", 1)[-1].lower()
            if slug not in by_slug and slug.startswith(f"{competition_id}-"):
                slug = slug[len(competition_id) + 1:]
            if snapshot is None or slug not in by_slug or run.manual_edit_used:
                continue
            if int(run.passed_count or 0) + int(run.failed_count or 0) <= 0 or run.finished_at is None:
                continue
            if snapshot.credential_mode not in {"self_funded", "official_evaluation"}:
                continue
            if run.billing_mode != snapshot.credential_mode:
                continue
            if run.billing_mode == "official_evaluation" and (
                snapshot.official_evaluation_started_at is None
                or run.evaluation_started_at is None
            ):
                continue
            breakdown = compute_score(
                passed=run.passed_count,
                test_total=by_slug[slug].total_tests,
                cost=scoring_cost_for_run(run, config.currency),
                currency=config.currency,
                config=config,
            )
            if breakdown.score is None:
                continue
            participant = str(run.competition_entry_id or run.user_id or "")
            if not participant:
                continue
            if participant_id is not None and participant != participant_id:
                continue
            if selected_snapshots.get(participant) is None or selected_snapshots[participant].id != run.submission_id:
                continue
            key = (participant, slug)
            # Only the selected snapshot participates. Within it, select
            # the highest-scoring valid run for each task.
            recency = (snapshot.created_at, run.finished_at, run.created_at, run.id)
            previous = latest.get(key)
            if previous is None or snapshot.created_at > previous[0][0] or (
                run.submission_id == previous[1].submission_id and rank_key(
                    breakdown.score, breakdown.pass_rate, breakdown.cost, snapshot.created_at
                ) < rank_key(previous[2].score, previous[2].pass_rate, previous[2].cost, snapshot.created_at)
            ):
                latest[key] = (recency, run, breakdown)

        loaded_entries = list(self.db.scalars(select(CompetitionEntry).where(
            CompetitionEntry.competition_id == competition_id
        )).all())
        entries = {entry.id: entry.display_name for entry in loaded_entries}
        user_ids = {value[1].user_id for value in latest.values() if value[1].user_id}
        loaded_users = list(self.db.scalars(select(User).where(User.id.in_(user_ids))).all()) if user_ids else []
        users = {user.id: user.username for user in loaded_users}
        email_by_user = {
            str(user.id): str(user.email).strip().lower()
            for user in loaded_users
            if getattr(user, "email", None) and str(user.email).strip()
        }
        emails_by_entry = self.catalog._emails_by_entry(loaded_entries, email_by_user)
        logical_tasks = {slug: task for slug, task in by_slug.items() if self.catalog.stage_info(slug)[1] is None}
        selected_slug = str(task_id or "all").rsplit("--", 1)[-1].lower()
        rows = []
        for participant in {key[0] for key in latest}:
            results = {}
            for slug, task in logical_tasks.items():
                direct = latest.get((participant, slug))
                candidates = []
                if direct:
                    candidates.append((direct[2], [direct[1]], "original"))
                route = routes.get(slug)
                if route:
                    count = int(route.get("stage_count", 3))
                    stage_values = [latest.get((participant, f"{slug}-stage-{number}")) for number in range(1, count + 1)]
                    # Every stage result belongs to this participant's selected snapshot.
                    stage_runs = [value[1] for value in stage_values if value]
                    if stage_runs:
                        total = int(route.get("test_total") or task.total_tests)
                        breakdown = compute_score(
                            passed=sum(value[2].passed for value in stage_values if value),
                            test_total=total,
                            cost=sum_scoring_costs(value[2].cost for value in stage_values if value),
                            currency=config.currency, config=config,
                        )
                        candidates.append((breakdown, stage_runs, "stages"))
                if candidates:
                    results[slug] = min(candidates, key=lambda value: rank_key(
                        value[0].score, value[0].pass_rate, value[0].cost,
                        max(snapshots[run.submission_id].created_at for run in value[1]),
                    ))
            if selected_slug != "all":
                if selected_slug in results:
                    breakdown, selected_runs, route_name = results[selected_slug]
                else:
                    value = latest.get((participant, selected_slug))
                    if value is None:
                        continue
                    breakdown, selected_runs, route_name = value[2], [value[1]], "stage"
                complete = True
            else:
                if not results:
                    continue
                selected_runs = [run for value in results.values() for run in value[1]]
                total = sum((results[slug][0].test_total if slug in results else task.total_tests)
                            for slug, task in logical_tasks.items())
                breakdown = compute_score(passed=sum(value[0].passed for value in results.values()),
                                          test_total=total, cost=aggregate_run_cost(selected_runs, config.currency),
                                          currency=config.currency, config=config)
                complete = set(results) == set(logical_tasks)
                route_name = results.get("github", (None, None, "original"))[2]
            newest = max(selected_runs, key=lambda run: snapshots[run.submission_id].created_at)
            submitted_at = snapshots[newest.submission_id].created_at
            tokens = sum(run.token_count for run in selected_runs) if all(run.token_count is not None for run in selected_runs) else None
            rows.append(CompetitionLeaderboardEntry(
                username=entries.get(participant) or users.get(newest.user_id, "Unknown"),
                account_emails=self.catalog._row_account_emails(
                    emails_by_entry, email_by_user, newest.competition_entry_id, newest.user_id,
                ),
                model_name=newest.model_name, track=competition_id,
                avg_pass_rate=breakdown.pass_rate, seniority_label="junior",
                total_token_millions=tokens / 1_000_000 if tokens is not None else None,
                total_token_cost=breakdown.cost, token_cost_currency=config.currency,
                avg_runtime_seconds=int(sum(run.run_duration_seconds or 0 for run in selected_runs) / len(selected_runs)),
                submission_count=len({run.submission_id for run in selected_runs}),
                task_id=None if selected_slug == "all" else selected_slug,
                task_title=by_slug[selected_slug].title if selected_slug in by_slug else None,
                is_complete=complete, score=breakdown.score, score_available=breakdown.score is not None,
                pass_rate=breakdown.pass_rate, passed_count=breakdown.passed, test_total=breakdown.test_total,
                total_cost=breakdown.cost, expected_cost=breakdown.expected_cost, cost_ratio=breakdown.ratio,
                score_regime=breakdown.regime, submitted_at=submitted_at.isoformat(),
                is_low_cost=breakdown.cost is not None and breakdown.cost < 0.01,
                scoring_route=route_name, source_run_ids=[run.id for run in selected_runs],
                funding_source=funding_source_for_runs(selected_runs),
            ))
        rows.sort(key=lambda row: rank_key(row.score, row.pass_rate, row.total_cost, datetime.fromisoformat(row.submitted_at)))
        for rank, row in enumerate(rows, 1):
            row.rank = rank
        return rows
