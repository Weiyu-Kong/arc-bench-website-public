from pydantic import BaseModel, EmailStr, Field


class ConfirmCompetitionRegistrationRequest(BaseModel):
    member_emails: list[EmailStr] = Field(default_factory=list, max_length=20)
    confirmed: bool


class CompetitionRegistrationStatus(BaseModel):
    competition_id: str
    competition_type: str
    registered: bool
    can_confirm: bool
    team_id: str | None = None
    team_name: str | None = None
    is_team_leader: bool = False
    initial_budget_cny: float
    remaining_budget_cny: float | None = None
    currency: str = "CNY"
    message: str | None = None
