from typing import Literal

from pydantic import BaseModel, Field


class RequirementSummary(BaseModel):
    id: str
    display_id: str
    title: str
    category: str
    summary: str
    test_runner: str
    total_tests: int
    module_count: int


class RequirementDetail(RequirementSummary):
    requirements_markdown: str
    requirements_yaml: str | None = None
    prerequisites_markdown: str
    assets_base_url: str
    references_base_url: str


class CompetitionTaskDownloadLinks(BaseModel):
    requirement_document: str | None = None
    prerequisites_document: str | None = None
    tests_bundle: str | None = None
    demo_bundle: str | None = None
    full_bundle: str | None = None


class CompetitionTaskSummary(RequirementSummary):
    assets_base_url: str
    references_base_url: str
    stage_number: int | None = None
    scoring_task_id: str | None = None
    previous_stage_id: str | None = None
    unlocked: bool = True
    unlock_score: float | None = None
    source_run_id: str | None = None


class RequirementTestFile(BaseModel):
    path: str
    content: str


class RequirementTests(BaseModel):
    files: list[RequirementTestFile] = Field(default_factory=list)
    public_downloads: CompetitionTaskDownloadLinks | None = None


class CompetitionSummary(BaseModel):
    id: str
    title: str
    type: str
    summary: str
    task_count: int
    total_tests: int
    is_public: bool
    starts_at: str | None = None
    ends_at: str | None = None
    status: str = "upcoming"
    notice: str = "Tasks will be published here."
    template_required: bool = False
    template_source_competition_id: str | None = None
    initial_budget_cny: float | None = None


class CompetitionTemplateOption(BaseModel):
    artifact_id: str
    source_run_id: str
    source_submission_id: str
    submission_name: str | None = None
    model_name: str | None = None
    test_pass_rate: float | None = None
    completed_at: str


class CompetitionTaskTemplateOptions(BaseModel):
    target_task_id: str
    target_task_title: str
    options: list[CompetitionTemplateOption] = Field(default_factory=list)


class CompetitionTemplateOptionsResponse(BaseModel):
    source_competition_id: str
    tasks: list[CompetitionTaskTemplateOptions] = Field(default_factory=list)


class CompetitionLeaderboardEntry(BaseModel):
    username: str
    model_name: str | None = None
    track: str
    avg_pass_rate: float
    avg_feature_implementation_rate: float = 0.0
    total_token_millions: float | None = None
    total_token_cost: float | None = None
    token_cost_currency: str | None = None
    cost_efficiency: float | None = None
    efficiency_threshold: float = 80.0
    efficiency_eligible: bool = False
    seniority_label: Literal["junior", "senior"]
    avg_runtime_seconds: int | None = None
    submission_count: int
    task_id: str | None = None
    task_title: str | None = None
    is_complete: bool = True
    score: float | None = None
    score_available: bool = False
    pass_rate: float = 0.0
    passed_count: int = 0
    test_total: int = 0
    total_cost: float | None = None
    expected_cost: float | None = None
    cost_ratio: float | None = None
    score_regime: str = "unavailable"
    submitted_at: str | None = None
    is_low_cost: bool = False
    rank: int = 0
    scoring_route: str | None = None
    source_run_ids: list[str] = Field(default_factory=list)
    funding_source: Literal["self_funded", "official_evaluation", "mixed"] | None = None
    # Account emails behind this row. A team row includes every current member.
    # Legacy client metadata. Class boards use server-side membership instead.
    account_emails: list[str] = Field(default_factory=list)


class CompetitionTaskRunScore(BaseModel):
    task_id: str
    task_title: str
    run_id: str | None = None
    status: str | None = None
    test_pass_rate: float | None = None
    feature_implementation_rate: float | None = None
    run_duration_seconds: int | None = None
    token_count: int | None = None
    token_cost_usd: float | None = None
    token_cost_currency: str | None = None
    completed_at: str | None = None
    score: float | None = None
    passed_count: int = 0
    test_total: int = 0
    score_regime: str = "unavailable"


class CompetitionLeaderboardSelectionRequest(BaseModel):
    submission_id: str | None = Field(default=None, min_length=1, max_length=64)


class CompetitionSubmissionHistoryEntry(BaseModel):
    id: str
    display_name: str | None = None
    model_name: str | None = None
    original_filename: str
    runtime: str
    credential_mode: str = "self_funded"
    created_at: str
    task_scores: list[CompetitionTaskRunScore] = Field(default_factory=list)
    average_test_pass_rate: float = 0.0
    average_feature_implementation_rate: float = 0.0
    total_run_duration_seconds: int = 0
    total_token_count: int | None = None
    token_cost_usd: float | None = None
    token_cost_currency: str | None = None
    is_selected_score: bool = False
    leaderboard_selection_mode: Literal["auto", "manual"] = "auto"
    can_select_for_leaderboard: bool = False
    can_delete: bool = False
    delete_block_reason: str | None = None
    score: float | None = None
    score_available: bool = False
    pass_rate: float = 0.0
    passed_count: int = 0
    test_total: int = 0
    total_cost: float | None = None


class CompetitionDetail(CompetitionSummary):
    downloads: CompetitionTaskDownloadLinks | None = None
    tasks: list[CompetitionTaskSummary]
    description_markdown: str = ""
    flow: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    current_score: CompetitionLeaderboardEntry | None = None


class BenchmarkDownloadLinks(BaseModel):
    track_bundle: str | None = None
    task_bundle: str | None = None


class BenchmarkSummary(BaseModel):
    id: str
    title: str
    type: str
    summary: str
    task_count: int
    total_tests: int
    downloads: BenchmarkDownloadLinks | None = None


class BenchmarkTaskSummary(RequirementSummary):
    downloads: BenchmarkDownloadLinks | None = None


class BenchmarkDetail(BenchmarkSummary):
    tasks: list[BenchmarkTaskSummary]
