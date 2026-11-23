from datetime import datetime

from pydantic import BaseModel


class AdminUserEntry(BaseModel):
    id: str
    username: str
    email: str
    classroom_id: str | None
    classroom_name: str | None
    is_leader: bool
    created_at: datetime


class AdminUserPage(BaseModel):
    items: list[AdminUserEntry]
    total: int
    page: int
    page_size: int = 20


class AdminSubmissionEntry(BaseModel):
    id: str
    competition_id: str | None
    display_name: str | None
    model_name: str | None
    original_filename: str
    runtime: str
    created_at: datetime
    deprecated_at: datetime | None
    run_count: int = 0


class AdminSubmissionPage(BaseModel):
    items: list[AdminSubmissionEntry]
    total: int
    page: int
    page_size: int = 20


class AdminRunEntry(BaseModel):
    id: str
    requirement_id: str
    status: str
    score: float | None
    test_pass_rate: float | None
    passed_count: int
    failed_count: int
    token_cost_usd: float | None
    token_cost_currency: str | None
    created_at: datetime
    finished_at: datetime | None


class AdminRunPage(BaseModel):
    items: list[AdminRunEntry]
    total: int
    page: int
    page_size: int = 20


class AdminSubmissionCompetition(BaseModel):
    id: str
    title: str
