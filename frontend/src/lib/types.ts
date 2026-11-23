export type UserApiKeySummary = {
  id: string;
  name: string | null;
  masked_key: string;
  created_at: string;
};

export type RequirementSummary = {
  id: string;
  display_id: string;
  title: string;
  category: string;
  summary: string;
  test_runner: string;
  total_tests: number;
  module_count: number;
};

export type RequirementDetail = RequirementSummary & {
  requirements_markdown: string;
  requirements_yaml: string | null;
  prerequisites_markdown: string;
  assets_base_url: string;
  references_base_url: string;
};

export type CompetitionTaskDownloadLinks = {
  requirement_document: string | null;
  prerequisites_document: string | null;
  tests_bundle: string | null;
  demo_bundle: string | null;
  full_bundle: string | null;
};

export type CompetitionTaskSummary = RequirementSummary & {
  assets_base_url: string;
  references_base_url: string;
  public_downloads: CompetitionTaskDownloadLinks | null;
  stage_number?: number | null;
  scoring_task_id?: string | null;
  previous_stage_id?: string | null;
  unlocked?: boolean;
  unlock_score?: number | null;
  source_run_id?: string | null;
};

export type RequirementTestFile = {
  path: string;
  content: string;
};

export type RequirementTests = {
  files: RequirementTestFile[];
};

export type CompetitionSummary = {
  id: string;
  title: string;
  type: string;
  summary: string;
  task_count: number;
  total_tests: number;
  is_public: boolean;
  starts_at: string | null;
  ends_at: string | null;
  status: "open" | "upcoming" | string;
  notice: string;
  template_required: boolean;
  template_source_competition_id: string | null;
  initial_budget_cny: number | null;
  tasks?: CompetitionTaskSummary[];
};

export type CompetitionTemplateOption = {
  artifact_id: string;
  source_run_id: string;
  source_submission_id: string;
  submission_name: string | null;
  model_name: string | null;
  test_pass_rate: number | null;
  completed_at: string;
};

export type CompetitionTemplateOptionsResponse = {
  source_competition_id: string;
  tasks: Array<{
    target_task_id: string;
    target_task_title: string;
    options: CompetitionTemplateOption[];
  }>;
};

export type CompetitionLeaderboardEntry = {
  username: string;
  model_name: string | null;
  track: string;
  avg_pass_rate: number;
  avg_feature_implementation_rate: number;
  total_token_millions: number | null;
  total_token_cost: number | null;
  token_cost_currency: string | null;
  cost_efficiency: number | null;
  efficiency_threshold: number;
  efficiency_eligible: boolean;
  seniority_label: "junior" | "senior";
  avg_runtime_seconds: number | null;
  submission_count: number;
  task_id: string | null;
  task_title: string | null;
  is_complete: boolean;
  score: number | null;
  score_available: boolean;
  pass_rate: number;
  passed_count: number;
  test_total: number;
  total_cost: number | null;
  expected_cost: number | null;
  cost_ratio: number | null;
  score_regime: "zero" | "reward" | "penalty" | "unavailable" | string;
  submitted_at: string | null;
  is_low_cost: boolean;
  rank: number;
  scoring_route?: string | null;
  source_run_ids?: string[];
  funding_source?: "self_funded" | "official_evaluation" | "mixed" | null;
  account_emails?: string[];
};

export type CompetitionTaskRunScore = {
  task_id: string;
  task_title: string;
  run_id: string | null;
  status: string | null;
  test_pass_rate: number | null;
  feature_implementation_rate: number | null;
  run_duration_seconds: number | null;
  token_count: number | null;
  token_cost_usd: number | null;
  token_cost_currency: string | null;
  completed_at: string | null;
  score: number | null;
  passed_count: number;
  test_total: number;
  score_regime: "zero" | "reward" | "penalty" | "unavailable" | string;
};

export type CompetitionSubmissionHistoryEntry = {
  can_delete: boolean;
  delete_block_reason: string | null;
  id: string;
  display_name: string | null;
  model_name: string | null;
  original_filename: string;
  runtime: string;
  credential_mode: "self_funded" | "official_evaluation" | string;
  created_at: string;
  task_scores: CompetitionTaskRunScore[];
  average_test_pass_rate: number;
  average_feature_implementation_rate: number;
  total_run_duration_seconds: number;
  total_token_count: number | null;
  token_cost_usd: number | null;
  token_cost_currency: string | null;
  is_selected_score: boolean;
  leaderboard_selection_mode: "auto" | "manual";
  can_select_for_leaderboard: boolean;
  score: number | null;
  score_available: boolean;
  pass_rate: number;
  passed_count: number;
  test_total: number;
  total_cost: number | null;
};

export type CompetitionDetail = CompetitionSummary & {
  downloads: CompetitionTaskDownloadLinks | null;
  tasks: CompetitionTaskSummary[];
  description_markdown?: string;
  flow: string[];
  rules: string[];
  current_score?: CompetitionLeaderboardEntry | null;
};

export type BenchmarkDownloadLinks = {
  track_bundle: string | null;
  task_bundle: string | null;
};

export type BenchmarkSummary = {
  id: string;
  title: string;
  type: string;
  summary: string;
  task_count: number;
  total_tests: number;
  downloads: BenchmarkDownloadLinks | null;
};

export type BenchmarkDetail = BenchmarkSummary & {
  tasks: Array<RequirementSummary & { downloads: BenchmarkDownloadLinks | null }>;
};

export type SubmissionStep = {
  key: string;
  title: string;
  status: string;
  description: string;
  logs: string[];
};

export type SubmissionSummary = {
  id: string;
  submission_id: string;
  display_name: string | null;
  model_name: string | null;
  requirement_id: string;
  catalog: "playground" | "competition" | "benchmark" | "my_tasks" | string;
  competition_id: string | null;
  billing_mode: "self_funded" | "official_evaluation" | string;
  evaluation_started_at: string | null;
  settled_at: string | null;
  runtime: string;
  agent_source: "upload" | string;
  original_filename: string;
  status: string;
  score: number | null;
  test_pass_rate: number | null;
  passed_count: number;
  failed_count: number;
  run_duration_seconds: number | null;
  token_count: number | null;
  token_cost_usd: number | null;
  token_cost_currency: string | null;
  cost_source?: "model_proxy" | string | null;
  cost_complete?: boolean | null;
  feature_implemented_count: number;
  feature_total_count: number;
  feature_implementation_rate: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  failure_reason: string | null;
};

export type AgentSubmissionSummary = {
  id: string;
  display_name: string | null;
  model_name: string | null;
  catalog: string;
  competition_id: string | null;
  credential_mode: "self_funded" | "official_evaluation" | string;
  requirement_id: string | null;
  runtime: string;
  agent_source: "upload" | string;
  original_filename: string;
  created_at: string;
};

export type SubmissionDetail = SubmissionSummary & {
  steps: SubmissionStep[];
  stdout_path: string | null;
  stderr_path: string | null;
  result_path: string | null;
  workspace_path: string | null;
  logs_available: boolean;
  tests: Array<{
    name: string;
    status: string;
    passed?: boolean;
    file?: string | null;
    duration_ms: number;
    error: string | null;
    req_id?: string | null;
    scenario_id?: string | null;
  }>;
  node_states: Record<string, RequirementVisualState>;
  can_pause: boolean;
  can_resume: boolean;
  can_rewind: boolean;
  can_manual_edit: boolean;
  manual_edit_node_id: string | null;
  manual_edit_phase: "design" | "implement" | null;
  manual_edit_dirty: boolean;
  pause_available: boolean;
  can_cancel: boolean;
  can_continue: boolean;
};

export type SubmissionLogs = {
  events: string;
  stdout: string;
  stderr: string;
  console: string;
  log_offset: number;
  last_event_id: string | null;
  visual_events: SubmissionVisualEvent[];
  runner_events?: SubmissionRunnerEvent[];
  runner_event_lines?: string[];
};

export type SubmissionTraceabilityInterface = {
  interface_id: string;
  req_ids: string[];
  type: string;
  content: string;
  file_path: string;
  first_line: string | null;
  implemented: boolean;
  callers: string[];
  callees: string[];
};

export type SubmissionTraceabilityTest = {
  test_id: string;
  req_id: string;
  scenario_id: string | null;
  type: string;
  file_path: string;
  first_line: string | null;
  status: "passed" | "failed" | null;
  content?: string | null;
};

export type SubmissionTraceabilityPayload = {
  interfaces: SubmissionTraceabilityInterface[];
  tests: SubmissionTraceabilityTest[];
};

export type SubmissionSourcePayload = {
  kind: "file" | "diff";
  file_path: string;
  language: string;
  content: string;
  first_line: number;
};

export type SubmissionCommitChangedFile = {
  file_path: string;
  change_type: "A" | "M" | "D" | "R" | string;
  old_file_path: string | null;
};

export type SubmissionCommitHistoryEntry = {
  oid: string;
  short_oid: string;
  committed_at: string;
  message: string;
  node_id: string | null;
  phase: "design" | "implement" | null;
  summary: string | null;
  changed_files: SubmissionCommitChangedFile[];
};

export type SubmissionCommitHistoryPayload = {
  availability: "available" | "workspace_unavailable" | "git_unavailable";
  commits: SubmissionCommitHistoryEntry[];
};

export type SubmissionVisualEvent = {
  type: "requirement_state";
  node_id: string;
  phase: "design" | "implement" | "test";
  status: "completed" | "passed" | "failed";
  timestamp: string;
  message: string | null;
};

export type SubmissionRunnerEvent = {
  event_id: string;
  timestamp: string;
  stage: "Preparing environment" | "Running agent" | "Evaluating result" | string;
  status: string;
  summary: string;
  heartbeat: boolean;
  artifact_reference: string | null;
};

export type NotificationItem = {
  id: string;
  run_id: string | null;
  team_join_request_id: string | null;
  kind: string;
  title: string;
  body: string;
  is_read: boolean;
  created_at: string;
};

export type NotificationList = {
  items: NotificationItem[];
  unread_count: number;
};

export type RequirementVisualState = "default" | "design" | "implement" | "test-passed" | "test-failed";

export type SubmissionEditableTaskPayload = {
  requirements_md: string;
  requirements_yaml: string;
  prerequisites_md: string;
  edited_node_id?: string | null;
};

export type SubmissionTaskAssets = {
  assets_base_url: string;
  references_base_url: string;
};

export type SubmissionPreviewStatus = {
  available: boolean;
  stale: boolean;
  preview_url: string | null;
  workspace_head_oid: string | null;
  preview_head_oid: string | null;
  error: string | null;
};

export type SubmissionManualEditCommitPreview = {
  message: string;
  node_id: string | null;
  phase: "design" | "implement" | null;
  dirty: boolean;
  dirty_files: string[];
};

export type SubmissionSseRefresh = {
  submission: boolean;
  logs: boolean;
  commit_history: boolean;
  traceability_selected: boolean;
  traceability_all: boolean;
  preview: boolean;
};

export type SubmissionSseEvent = {
  submission_id: string;
  timestamp: number;
  version: number;
  refresh: SubmissionSseRefresh;
  reason: string | null;
};

export type UserTaskSummary = {
  id: string;
  title: string;
  task_type: "web" | "mobile" | "kernel" | "mixed" | "cli";
  summary: string;
  root_requirement_id: string;
  node_count: number;
  atomic_count: number;
  created_at: string;
  updated_at: string;
};

export type UserTaskDetail = UserTaskSummary & {
  yaml_content: string;
  markdown_content: string;
};

export type UserTaskDraft = {
  draft_id: string;
  references_base_url: string;
  title: string;
  task_type: "web" | "mobile" | "kernel" | "mixed" | "cli";
  yaml_content: string;
  markdown_content: string;
};

export type UserSummary = {
  id: string;
  email: string;
  username: string;
  github_email: string | null;
  github_username: string | null;
  registration_source: "standard" | "hackathon";
  display_name: string | null;
  real_name: string | null;
  student_id: string | null;
  avatar_url: string | null;
  created_at: string;
  email_verified_at?: string | null;
  is_admin: boolean;
};

export type UpdateProfilePayload = {
  real_name?: string | null;
  student_id?: string | null;
  github_email?: string | null;
  github_username?: string | null;
};

export type TeamSummary = {
  id: string;
  name: string;
  slogan: string | null;
  leader_user_id: string;
  leader_username: string;
  member_count: number;
  source_provider: string | null;
  source_team_id: string | null;
  github_repo: string | null;
  model_name: string | null;
  harness: string | null;
  created_at: string;
};

export type CompetitionBudgetSummary = {
  competition_id: string;
  initial_amount_cny: number;
  remaining_amount_cny: number;
  currency: "CNY" | string;
};

export type TeamMemberSummary = {
  user_id: string;
  email: string;
  username: string;
  display_name: string | null;
  real_name: string | null;
  student_id: string | null;
  avatar_url: string | null;
  role: "leader" | "member";
  created_at: string;
};

export type TeamJoinRequestSummary = {
  id: string;
  team_id: string;
  user_id: string;
  username: string;
  display_name: string | null;
  real_name: string | null;
  student_id: string | null;
  avatar_url: string | null;
  team_name: string;
  status: "PENDING" | "ACCEPTED" | "DECLINED";
  created_at: string;
};

export type MyTeamResponse = {
  team: TeamSummary | null;
  members: TeamMemberSummary[];
  incoming_requests: TeamJoinRequestSummary[];
  pending_request: TeamJoinRequestSummary | null;
  source_managed: boolean;
  competition_budgets: CompetitionBudgetSummary[];
};

export type CompetitionRegistrationStatus = {
  competition_id: string;
  competition_type: "official" | string;
  registered: boolean;
  can_confirm: boolean;
  team_id: string | null;
  team_name: string | null;
  is_team_leader: boolean;
  initial_budget_cny: number;
  remaining_budget_cny: number | null;
  currency: "CNY" | string;
  message: string | null;
};

export type WorkspaceFileEntry = {
  path: string;
  name: string;
  is_directory: boolean;
  children?: WorkspaceFileEntry[];
};

export type WorkspaceFileListPayload = {
  files: WorkspaceFileEntry[];
};

export type FileUpdatePayload = {
  path: string;
  content: string;
};

export type TestCreatePayload = {
  test_id: string;
  req_id: string;
  test_type: string;
  scenario_id?: string | null;
  file_path?: string | null;
};

export type AuthResponse = {
  user: UserSummary;
};

export type EmailVerificationChallenge = {
  verification_required: true;
  challenge_id: string;
  email: string;
  expires_in: number;
  resend_after: number;
};

export type AuthStartResponse = AuthResponse | EmailVerificationChallenge;

export type PasswordResetGrant = {
  reset_token: string;
  expires_in: number;
};

export type ClassroomTeam = {
  id: string;
  name: string;
  leader_user_id: string;
  members: Array<{ user_id: string; name: string; is_leader: boolean }>;
};

export type MyClassroomResponse = {
  classroom: { id: string; name: string; description: string; member_count: number; team_count: number } | null;
  team: ClassroomTeam | null;
  tracks: Array<{ id: string; title: string; status: string }>;
  teams: ClassroomTeam[];
  is_team_leader: boolean;
};
