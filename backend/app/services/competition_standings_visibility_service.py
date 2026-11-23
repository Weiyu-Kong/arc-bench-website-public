"""Read-only public standings policies for competition leaderboards.

Apply after scoring, before credit-source filtering and public re-ranking.
Internal scores, template eligibility, and historical run data are unaffected.
"""

import csv
import math
import re
import unicodedata
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.core.config import ROOT_DIR
from app.models.competition_account import CompetitionEntry, Team
from app.models.run import Run
from app.models.user import User


PUBLIC_HACKATHON_LEADERBOARDS = frozenset({"hackathon", "hackathon-evolution"})


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


def name_aliases(value: str) -> set[str]:
    value = normalized_name(value)
    aliases = {value}
    match = re.fullmatch(r"(.+?)\s*\(([^()]*)\)", value)
    if match:
        aliases.update(part.strip() for part in match.groups())
    # Truncated prefixes cannot reliably identify a team. Their complete
    # parenthesized alias and captain email still identify the roster entry.
    return {alias for alias in aliases if alias and "…" not in alias and "..." not in alias}


class CompetitionStandingsVisibilityService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def filter_minimum_cost(entries):
        """Hide rows below CNY 0.1 on every board, including Smoke Competition."""
        def below_minimum(row):
            # Legacy cross-competition aggregates expose only total_token_cost.
            cost = row.total_cost if row.total_cost is not None else row.total_token_cost
            return cost is not None and math.isfinite(float(cost)) and float(cost) < 0.1

        return [row for row in entries if not below_minimum(row)]

    @staticmethod
    def _finalists() -> tuple[set[str], set[str]]:
        supplied = ROOT_DIR / "决赛结果.csv"
        path = supplied if supplied.is_file() else Path(__file__).resolve().parents[1] / "resources" / "hackathon_finalists.csv"
        names, emails = set(), set()
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames or not ({"队伍名", "队长邮箱"} <= set(reader.fieldnames)
                                                or {"team_name", "leader_email"} <= set(reader.fieldnames)):
                    raise ValueError("Missing finalist columns")
                for row in reader:
                    names.update(name_aliases(row.get("队伍名") or row.get("team_name") or ""))
                    email = str(row.get("队长邮箱") or row.get("leader_email") or "").strip().casefold()
                    if email:
                        emails.add(email)
            if not names:
                raise ValueError("Empty finalist roster")
        except (OSError, ValueError, csv.Error) as exc:
            raise RuntimeError("Competition standings are temporarily unavailable") from exc
        return names, emails

    def filter(self, competition_id: str, entries, *, preliminary_overall=None):
        if competition_id not in PUBLIC_HACKATHON_LEADERBOARDS or not entries:
            return entries
        names, emails = self._finalists()
        reference = preliminary_overall if preliminary_overall is not None else entries
        all_entries = list(entries) + list(reference)
        run_ids = {run_id for row in all_entries for run_id in row.source_run_ids}
        captain = aliased(User)
        owner = aliased(User)
        records = self.db.execute(
            select(
                Run.id, Run.competition_entry_id, Run.user_id, Run.token_cost_usd,
                CompetitionEntry.display_name, Team.name,
                captain.email, owner.email,
            )
            .outerjoin(CompetitionEntry, CompetitionEntry.id == Run.competition_entry_id)
            .outerjoin(Team, Team.id == CompetitionEntry.team_id)
            .outerjoin(captain, captain.id == Team.leader_user_id)
            .outerjoin(owner, owner.id == Run.user_id)
            .where(Run.id.in_(run_ids), Run.competition_id == competition_id)
        ).all() if run_ids else []
        identities = {record[0]: record for record in records}

        def row_names(row):
            values = name_aliases(row.username)
            for run_id in row.source_run_ids:
                record = identities.get(run_id)
                if record:
                    values.update(name_aliases(record[4] or ""))
                    values.update(name_aliases(record[5] or ""))
            return values

        def is_finalist(row):
            for run_id in row.source_run_ids:
                record = identities.get(run_id)
                if record:
                    # Prefer the captain's identity; submitters can be team members.
                    email = str(record[6] or record[7] or "").strip().casefold()
                    if email in emails:
                        return True
            return bool(row_names(row) & names)

        def participant(row):
            for run_id in row.source_run_ids:
                record = identities.get(run_id)
                if record:
                    return ("entry", record[1]) if record[1] else ("user", record[2])
            return ("name", normalized_name(row.username))

        def low_cost(row):
            cost = row.total_cost
            # A missing scoring cost is +infinity, not a cheap evaluation.
            return cost is not None and math.isfinite(float(cost)) and float(cost) < 0.01

        if competition_id == "hackathon-evolution":
            return [row for row in entries if is_finalist(row)]

        # Locate Abram on the full preliminary board after removing cheap
        # records. Task and funding filters must never move this cutoff.
        reference = [row for row in reference if not low_cost(row)]
        anchor = next((index for index, row in enumerate(reference) if "abram" in row_names(row)), None)
        if anchor is None:
            # Without the anchor, only known finalists can be safely published.
            return [row for row in entries if not low_cost(row) and is_finalist(row)]
        hidden = {participant(row) for row in reference[:anchor] if not is_finalist(row)}
        return [row for row in entries if not low_cost(row) and participant(row) not in hidden]
