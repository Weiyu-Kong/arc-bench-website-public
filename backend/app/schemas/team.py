from datetime import datetime

from pydantic import BaseModel, Field


class TeamSummary(BaseModel):
    id: str
    name: str
    slogan: str | None = None
    leader_user_id: str
    leader_username: str
    member_count: int
    source_provider: str | None = None
    source_team_id: str | None = None
    github_repo: str | None = None
    model_name: str | None = None
    harness: str | None = None
    created_at: datetime


class CompetitionBudgetSummary(BaseModel):
    competition_id: str
    initial_amount_cny: float
    remaining_amount_cny: float
    currency: str = "CNY"


class TeamMemberSummary(BaseModel):
    user_id: str
    email: str
    username: str
    display_name: str | None = None
    real_name: str | None = None
    student_id: str | None = None
    avatar_url: str | None = None
    role: str
    created_at: datetime


class TeamJoinRequestSummary(BaseModel):
    id: str
    team_id: str
    user_id: str
    username: str
    display_name: str | None = None
    real_name: str | None = None
    student_id: str | None = None
    avatar_url: str | None = None
    team_name: str
    status: str
    created_at: datetime


class MyTeamResponse(BaseModel):
    team: TeamSummary | None = None
    members: list[TeamMemberSummary] = Field(default_factory=list)
    incoming_requests: list[TeamJoinRequestSummary] = Field(default_factory=list)
    pending_request: TeamJoinRequestSummary | None = None
    source_managed: bool = False
    competition_budgets: list[CompetitionBudgetSummary] = Field(default_factory=list)


class CreateTeamRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slogan: str | None = Field(default=None, max_length=240)


class UpdateTeamRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class RequestTeamJoinResponse(BaseModel):
    request: TeamJoinRequestSummary
