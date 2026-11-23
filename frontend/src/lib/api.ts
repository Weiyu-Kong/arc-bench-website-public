import type { MyClassroomResponse } from "./types";
import type {
  AuthResponse,
  AuthStartResponse,
  EmailVerificationChallenge,
  PasswordResetGrant,
  AgentSubmissionSummary,
  BenchmarkDetail,
  BenchmarkSummary,
  CompetitionLeaderboardEntry,
  CompetitionSubmissionHistoryEntry,
  CompetitionDetail,
  CompetitionRegistrationStatus,
  CompetitionSummary,
  CompetitionTemplateOptionsResponse,
  RequirementDetail,
  RequirementTests,
  SubmissionEditableTaskPayload,
  SubmissionTaskAssets,
  SubmissionSourcePayload,
  RequirementSummary,
  SubmissionDetail,
  SubmissionCommitHistoryPayload,
  SubmissionLogs,
  SubmissionManualEditCommitPreview,
  SubmissionPreviewStatus,
  SubmissionSseEvent,
  SubmissionSummary,
  SubmissionTraceabilityPayload,
  UserTaskDraft,
  UserTaskDetail,
  UserTaskSummary,
  WorkspaceFileListPayload,
  FileUpdatePayload,
  NotificationItem,
  NotificationList,
  TestCreatePayload,
  MyTeamResponse,
  TeamJoinRequestSummary,
  UpdateProfilePayload,
  UserApiKeySummary,
} from "./types";

export const API_BASE = (import.meta.env.VITE_API_BASE?.trim() || "/api").replace(/\/$/, "");

export function apiUrl(path: string): string {
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

export type BulkDeleteResult = {
  deleted_ids: string[];
  skipped: Array<{ id: string; reason: string }>;
};

export type AdminLeaderboardExportRequest = {
  track: "all" | "web" | "mobile" | "kernel";
  competition_id?: string;
  task_id?: string;
  funding_source: "all" | "self_funded" | "official_evaluation";
  classroom_id?: string;
  source_run_ids: string[];
};

export type AdminUserEntry = {
  id: string;
  username: string;
  email: string;
  classroom_id: string | null;
  classroom_name: string | null;
  is_leader: boolean;
  created_at: string;
};

export type AdminUserPage = {
  items: AdminUserEntry[];
  total: number;
  page: number;
  page_size: number;
};

export type AdminSubmissionEntry = {
  id: string;
  competition_id: string | null;
  display_name: string | null;
  model_name: string | null;
  original_filename: string;
  runtime: string;
  created_at: string;
  deprecated_at: string | null;
  run_count: number;
};

export type AdminRunEntry = {
  id: string;
  requirement_id: string;
  status: string;
  score: number | null;
  test_pass_rate: number | null;
  passed_count: number;
  failed_count: number;
  token_cost_usd: number | null;
  token_cost_currency: string | null;
  created_at: string;
  finished_at: string | null;
};

export type AdminHistoryPage<T> = { items: T[]; total: number; page: number; page_size: number };

export type AdminExecutor = {
  executor_id: string;
  status?: string;
  accepting_tasks?: string;
  running_tasks?: number;
  updated_at?: string;
  age_seconds?: number;
  health?: Record<string, unknown>;
  [key: string]: unknown;
};

export type AdminExecutorDetail = AdminExecutor & {
  tasks?: Array<Record<string, unknown>>;
  completed_tasks?: Array<Record<string, unknown>>;
  control?: Record<string, unknown>;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type ApiErrorPayload = {
  detail?: string | Array<{ msg?: string; loc?: Array<string | number> }>;
};

function formatErrorMessage(payload: ApiErrorPayload, fallback: string): string {
  if (typeof payload.detail === "string" && payload.detail.trim()) {
    return payload.detail;
  }

  if (Array.isArray(payload.detail) && payload.detail.length > 0) {
    return payload.detail
      .map((item) => {
        const path = Array.isArray(item.loc) ? item.loc.slice(1).join(".") : "";
        if (path && item.msg) {
          return `${path}: ${item.msg}`;
        }
        return item.msg;
      })
      .filter((message): message is string => Boolean(message && message.trim()))
      .join("; ");
  }

  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    credentials: "include",
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({ detail: response.statusText }))) as ApiErrorPayload;
    throw new ApiError(formatErrorMessage(payload, response.statusText || "Request failed"), response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function encodePathSegments(path: string) {
  return path
    .split("/")
    .filter((segment) => segment.length > 0)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

export const api = {
  listAdminUserCompetitions(userId: string, signal?: AbortSignal) {
    return request<Array<{ id: string; title: string }>>(`/admin/users/${encodeURIComponent(userId)}/submission-competitions`, { signal, cache: "no-store" });
  },
  listAdminUserSubmissions(userId: string, page: number, competitionId: string, signal?: AbortSignal) {
    const params = new URLSearchParams({ page: String(page) });
    if (competitionId) params.set("competition_id", competitionId);
    return request<AdminHistoryPage<AdminSubmissionEntry>>(`/admin/users/${encodeURIComponent(userId)}/submissions?${params}`, { signal, cache: "no-store" });
  },
  listAdminSubmissionRuns(userId: string, submissionId: string, page: number, signal?: AbortSignal) {
    return request<AdminHistoryPage<AdminRunEntry>>(`/admin/users/${encodeURIComponent(userId)}/submissions/${encodeURIComponent(submissionId)}/runs?page=${page}`, { signal, cache: "no-store" });
  },
  async downloadAdminUserFile(userId: string, submissionId: string, runId?: string, signal?: AbortSignal) {
    const base = `/admin/users/${encodeURIComponent(userId)}/submissions/${encodeURIComponent(submissionId)}`;
    const response = await fetch(apiUrl(runId ? `${base}/runs/${encodeURIComponent(runId)}/artifacts` : `${base}/agent`), { credentials: "include", cache: "no-store", signal });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText })) as ApiErrorPayload;
      throw new ApiError(formatErrorMessage(error, "Download failed"), response.status);
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const plain = disposition.match(/filename="([^"]+)"/i)?.[1];
    const filename = encoded ? decodeURIComponent(encoded) : plain ?? "download.zip";
    return { blob: await response.blob(), filename };
  },
  listAdminUsers(filters: { page: number; q: string; classroomId: string; leader: "all" | "yes" | "no" }, signal?: AbortSignal) {
    const params = new URLSearchParams({ page: String(filters.page) });
    if (filters.q) params.set("q", filters.q);
    if (filters.classroomId) params.set("classroom_id", filters.classroomId);
    if (filters.leader !== "all") params.set("is_leader", String(filters.leader === "yes"));
    return request<AdminUserPage>(`/admin/users?${params}`, { signal, cache: "no-store" });
  },
  getCurrentUser() {
    return request<AuthResponse>("/auth/me");
  },
  updateProfile(payload: UpdateProfilePayload) {
    return request<AuthResponse>("/auth/profile", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  register(payload: { email: string; username: string; password: string }) {
    return request<AuthStartResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  login(payload: { email: string; password: string }) {
    return request<AuthStartResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  logout() {
    return request<{ detail: string }>("/auth/logout", {
      method: "POST",
    });
  },
  verifyEmail(payload: { challenge_id: string; code: string }) {
    return request<AuthResponse>("/auth/email/verify", { method: "POST", body: JSON.stringify(payload) });
  },
  resendVerificationEmail(challenge_id: string) {
    return request<EmailVerificationChallenge>("/auth/email/resend", {
      method: "POST", body: JSON.stringify({ challenge_id }),
    });
  },
  requestPasswordReset(email: string) {
    return request<EmailVerificationChallenge>("/auth/password-reset/request", {
      method: "POST", body: JSON.stringify({ email }),
    });
  },
  resendPasswordReset(challenge_id: string) {
    return request<EmailVerificationChallenge>("/auth/password-reset/resend", {
      method: "POST", body: JSON.stringify({ challenge_id }),
    });
  },
  verifyPasswordReset(payload: { challenge_id: string; code: string }) {
    return request<PasswordResetGrant>("/auth/password-reset/verify", {
      method: "POST", body: JSON.stringify(payload),
    });
  },
  completePasswordReset(payload: { reset_token: string; password: string; confirm_password: string }) {
    return request<{ detail: string }>("/auth/password-reset/complete", {
      method: "POST", body: JSON.stringify(payload),
    });
  },
  listAdminExecutors(token: string) {
    return request<{ executors: AdminExecutor[] }>("/admin/executors", {
      headers: { Authorization: `Bearer ${token}` },
    });
  },
  getAdminExecutor(executorId: string, token: string) {
    return request<AdminExecutorDetail>(`/admin/executors/${encodeURIComponent(executorId)}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  },
  controlAdminExecutor(executorId: string, action: "pause" | "resume" | "shutdown", token: string) {
    return request<{ executor_id: string; action: string; request_id: string }>(
      `/admin/executors/${encodeURIComponent(executorId)}/control`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: JSON.stringify({ action }),
      },
    );
  },
  listCompetitions() {
    return request<CompetitionSummary[]>("/competitions");
  },
  getCompetitionLeaderboard(competitionId?: string, taskId?: string, fundingSource: "all" | "self_funded" | "official_evaluation" = "all", classroomId?: string, admin = false) {
    const params = new URLSearchParams();
    params.set("track", "all");
    if (classroomId) params.set("classroom_id", classroomId);
    if (fundingSource !== "all") params.set("funding_source", fundingSource);
    if (competitionId) {
      params.set("competition_id", competitionId);
    }
    if (taskId && taskId !== "all") {
      params.set("task_id", taskId);
    }
    return request<CompetitionLeaderboardEntry[]>(`${admin ? "/admin" : "/competitions"}/leaderboard?${params.toString()}`);
  },
  async exportAdminLeaderboard(payload: AdminLeaderboardExportRequest, signal?: AbortSignal) {
    const response = await fetch(apiUrl("/admin/leaderboard/export"), {
      method: "POST", credentials: "include", signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText })) as ApiErrorPayload;
      throw new ApiError(formatErrorMessage(error, "Download failed"), response.status);
    }
    return { blob: await response.blob(), incomplete: response.headers.get("X-ArcBench-Export-Incomplete") === "true" };
  },
  getRequirementTests(requirementId: string, catalog: "playground" | "competition" | "benchmark") {
    return request<RequirementTests>(`/requirements/${encodeURIComponent(requirementId)}/tests?catalog=${catalog}`);
  },
  getCompetition(competitionId: string) {
    return request<CompetitionDetail>(`/competitions/${competitionId}`);
  },
  getCompetitionRegistration(competitionId: string) {
    return request<CompetitionRegistrationStatus>(`/competitions/${encodeURIComponent(competitionId)}/registration`);
  },
  confirmCompetitionRegistration(competitionId: string, payload: { member_emails: string[]; confirmed: boolean }) {
    return request<CompetitionRegistrationStatus>(`/competitions/${encodeURIComponent(competitionId)}/registration`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  listBenchmarks() {
    return request<BenchmarkSummary[]>("/benchmarks");
  },
  getBenchmark(benchmarkId: string) {
    return request<BenchmarkDetail>(`/benchmarks/${benchmarkId}`);
  },
  async getDemoAgentFile() {
    const response = await fetch(apiUrl("/competitions/public/demo-agent"), {
      credentials: "include",
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({ detail: response.statusText }))) as ApiErrorPayload;
      throw new ApiError(formatErrorMessage(payload, response.statusText || "Request failed"), response.status);
    }
    const blob = await response.blob();
    const filename = response.headers.get("Content-Disposition")
      ?.match(/filename=\"?([^"]+)\"?/)
      ?.[1] ?? "demo_agent.zip";
    return new File([blob], filename, { type: "application/zip" });
  },
  listRequirements(catalog: "playground" | "competition" | "benchmark" = "playground") {
    return request<RequirementSummary[]>(`/requirements?catalog=${catalog}`);
  },
  getRequirement(requirementId: string, catalog: "playground" | "competition" | "benchmark" = "playground") {
    return request<RequirementDetail>(`/requirements/${requirementId}?catalog=${catalog}`);
  },
  listSubmissions(requirementId?: string) {
    const query = requirementId ? `?requirement_id=${encodeURIComponent(requirementId)}` : "";
    return request<AgentSubmissionSummary[]>(`/submissions${query}`);
  },
  getMyClassroom() {
    return request<MyClassroomResponse>("/classrooms/me");
  },
  listAllClassrooms() {
    return request<Array<{ id: string; name: string; description: string; member_count: number; team_count: number }>>("/classrooms/all");
  },
  getMyTeam() {
    return request<MyTeamResponse>("/teams/me");
  },
  listApiKeys(signal?: AbortSignal) {
    return request<UserApiKeySummary[]>("/api-keys", { cache: "no-store", signal });
  },
  getApiKeySecret(keyId: string, signal?: AbortSignal) {
    return request<{ api_key: string }>(`/api-keys/${encodeURIComponent(keyId)}/secret`, { cache: "no-store", signal });
  },
  createTeam(payload: { name: string; slogan?: string | null }) {
    return request<MyTeamResponse>("/teams", { method: "POST", body: JSON.stringify(payload) });
  },
  renameTeam(name: string) {
    return request<MyTeamResponse>("/teams/me", { method: "PATCH", body: JSON.stringify({ name }) });
  },
  requestTeamJoin(teamId: string) {
    return request<{ request: TeamJoinRequestSummary }>(`/teams/${encodeURIComponent(teamId)}/join-requests`, { method: "POST" });
  },
  getTeamJoinRequest(requestId: string) {
    return request<TeamJoinRequestSummary>(`/teams/join-requests/${encodeURIComponent(requestId)}`);
  },
  acceptTeamJoinRequest(requestId: string) {
    return request<MyTeamResponse>(`/teams/join-requests/${encodeURIComponent(requestId)}/accept`, { method: "POST" });
  },
  declineTeamJoinRequest(requestId: string) {
    return request<MyTeamResponse>(`/teams/join-requests/${encodeURIComponent(requestId)}/decline`, { method: "POST" });
  },
  leaveTeam() {
    return request<void>("/teams/leave", { method: "POST" });
  },
  removeTeamMember(userId: string) {
    return request<MyTeamResponse>(`/teams/members/${encodeURIComponent(userId)}`, { method: "DELETE" });
  },
  listRuns(requirementId?: string, submissionId?: string, activeOnly = false) {
    const params = new URLSearchParams();
    if (requirementId) params.set("requirement_id", requirementId);
    if (submissionId) params.set("submission_id", submissionId);
    if (activeOnly) params.set("active_only", "true");
    const query = params.toString();
    return request<SubmissionSummary[]>(`/runs${query ? `?${query}` : ""}`);
  },
  listActiveRuns() {
    return request<SubmissionSummary[]>("/runs?active_only=true");
  },
  getCompetitionLeaderboardTasks(competitionId: string) {
    return request<Array<{ id: string; title: string }>>(`/competitions/${encodeURIComponent(competitionId)}/leaderboard-tasks`);
  },
  getCompetitionSubmissionHistory(competitionId: string) {
    return request<CompetitionSubmissionHistoryEntry[]>(`/competitions/${competitionId}/submissions`);
  },
  selectCompetitionLeaderboardSubmission(competitionId: string, submissionId: string | null) {
    return request<{ detail: string }>(`/competitions/${encodeURIComponent(competitionId)}/leaderboard-submission`, {
      method: "PUT",
      body: JSON.stringify({ submission_id: submissionId }),
    });
  },
  getCompetitionTemplateOptions(competitionId: string) {
    return request<CompetitionTemplateOptionsResponse>(`/competitions/${competitionId}/template-options`);
  },
  async downloadCompetitionRequirements(competitionId: string) {
    const response = await fetch(apiUrl(`/competitions/${encodeURIComponent(competitionId)}/requirements-download`), { credentials: "include" });
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({ detail: response.statusText }))) as ApiErrorPayload;
      throw new ApiError(formatErrorMessage(payload, response.statusText || "Request failed"), response.status);
    }
    const blob = await response.blob();
    const filename = response.headers.get("Content-Disposition")?.match(/filename=\"?([^\"]+)\"?/)?.[1] ?? `${competitionId}-requirements.zip`;
    return new File([blob], filename, { type: "application/zip" });
  },
  async downloadCompetitionInitialProjects(competitionId: string) {
    const response = await fetch(apiUrl(`/competitions/${encodeURIComponent(competitionId)}/initial-projects-download`), { credentials: "include" });
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({ detail: response.statusText }))) as ApiErrorPayload;
      throw new ApiError(formatErrorMessage(payload, response.statusText || "Request failed"), response.status);
    }
    const blob = await response.blob();
    const filename = response.headers.get("Content-Disposition")?.match(/filename=\"?([^\"]+)\"?/)?.[1] ?? `${competitionId}-initial-projects.zip`;
    return new File([blob], filename, { type: "application/zip" });
  },
  getSubmission(submissionId: string, options?: { includeStepLogs?: boolean; includeTests?: boolean }) {
    const params = new URLSearchParams();
    if (options?.includeStepLogs !== undefined) params.set("include_step_logs", String(options.includeStepLogs));
    if (options?.includeTests !== undefined) params.set("include_tests", String(options.includeTests));
    return request<SubmissionDetail>(`/runs/${submissionId}${params.size ? `?${params}` : ""}`);
  },
  pauseSubmission(submissionId: string) {
    return request<SubmissionDetail>(`/runs/${submissionId}/pause`, {
      method: "POST",
    });
  },
  resumeSubmission(submissionId: string) {
    return request<SubmissionDetail>(`/runs/${submissionId}/resume`, {
      method: "POST",
    });
  },
  continueSubmission(submissionId: string) {
    return request<SubmissionDetail>(`/runs/${submissionId}/continue`, {
      method: "POST",
    });
  },
  getManualEditCommitPreview(submissionId: string) {
    return request<SubmissionManualEditCommitPreview>(`/runs/${submissionId}/manual-edit/commit-preview`, {
      method: "POST",
    });
  },
  rewindSubmission(submissionId: string, payload: { commit_oid: string }) {
    return request<SubmissionDetail>(`/runs/${submissionId}/rewind`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  rerunDemoTdd(submissionId: string, testId: string) {
    return request<SubmissionDetail>(`/runs/${submissionId}/tdd-rerun`, {
      method: "POST",
      body: JSON.stringify({ test_id: testId }),
    });
  },
  getSubmissionEditableTask(submissionId: string) {
    return request<SubmissionEditableTaskPayload>(`/runs/${submissionId}/editable-task`);
  },
  updateSubmissionEditableTask(submissionId: string, payload: SubmissionEditableTaskPayload) {
    return request<{ detail: string }>(`/runs/${submissionId}/editable-task`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getSubmissionTaskAssets(submissionId: string): SubmissionTaskAssets {
    return {
      assets_base_url: apiUrl(`/runs/${submissionId}/requirements-assets/assets/`),
      references_base_url: apiUrl(`/runs/${submissionId}/requirements-assets/references/`),
    };
  },
  getSubmissionLogs(submissionId: string, cursor?: { logOffset?: number; afterEventId?: string | null }) {
    const params = new URLSearchParams();
    if (cursor?.logOffset !== undefined) params.set("log_offset", String(cursor.logOffset));
    if (cursor?.afterEventId) params.set("after_event_id", cursor.afterEventId);
    const query = params.toString();
    return request<SubmissionLogs>(`/runs/${submissionId}/logs${query ? `?${query}` : ""}`);
  },
  listNotifications() {
    return request<NotificationList>("/notifications");
  },
  markNotificationRead(notificationId: string) {
    return request<NotificationItem>(`/notifications/${notificationId}/read`, { method: "POST" });
  },
  markAllNotificationsRead() {
    return request<NotificationList>("/notifications/read-all", { method: "POST" });
  },
  deleteNotification(notificationId: string) {
    return request<NotificationList>(`/notifications/${notificationId}`, { method: "DELETE" });
  },
  clearNotifications() {
    return request<NotificationList>("/notifications/clear", { method: "DELETE" });
  },
  cancelSubmission(submissionId: string) {
    return request<SubmissionDetail>(`/runs/${submissionId}/cancel`, {
      method: "POST",
    });
  },
  getSubmissionTraceability(submissionId: string, nodeId: string) {
    return request<SubmissionTraceabilityPayload>(
      `/runs/${submissionId}/traceability?node_id=${encodeURIComponent(nodeId)}`,
    );
  },
  getSubmissionAllTraceability(submissionId: string) {
    return request<SubmissionTraceabilityPayload>(
      `/runs/${submissionId}/traceability?node_id=__all__`,
    );
  },
  getSubmissionCommitHistory(submissionId: string) {
    return request<SubmissionCommitHistoryPayload>(`/runs/${submissionId}/commit-history`);
  },
  getSubmissionSource(
    submissionId: string,
    payload: { filePath: string; firstLine?: string | number | null; kind?: "file" | "diff"; commitOid?: string | null },
  ) {
    const params = new URLSearchParams({
      file_path: payload.filePath,
      kind: payload.kind ?? "file",
    });
    if (payload.firstLine !== undefined && payload.firstLine !== null) {
      const normalizedFirstLine = String(payload.firstLine).trim();
      if (normalizedFirstLine) {
        params.set("first_line", normalizedFirstLine);
      }
    }
    if (payload.commitOid && payload.commitOid.trim()) {
      params.set("commit_oid", payload.commitOid.trim());
    }
    return request<SubmissionSourcePayload>(`/runs/${submissionId}/source?${params.toString()}`);
  },
  getSubmissionPreviewStatus(submissionId: string) {
    return request<SubmissionPreviewStatus>(`/runs/${submissionId}/preview/status`);
  },
  connectSubmissionEvents(
    submissionId: string,
    handlers: {
      onEvent: (event: SubmissionSseEvent) => void;
      onError?: () => void;
    },
    options?: { sinceVersion?: number },
  ) {
    const params = new URLSearchParams();
    if (options?.sinceVersion && options.sinceVersion > 0) {
      params.set("since_version", String(options.sinceVersion));
    }
    const query = params.toString();
    const source = new EventSource(
      `${apiUrl(`/runs/${submissionId}/events`)}${query ? `?${query}` : ""}`,
      { withCredentials: true },
    );
    source.addEventListener("submission-update", (rawEvent) => {
      try {
        const payload = JSON.parse((rawEvent as MessageEvent<string>).data) as SubmissionSseEvent;
        handlers.onEvent(payload);
      } catch {
        // Ignore malformed SSE payloads.
      }
    });
    source.onerror = () => {
      handlers.onError?.();
    };
    return source;
  },
  refreshSubmissionPreview(submissionId: string) {
    return request<SubmissionPreviewStatus>(`/runs/${submissionId}/preview/refresh`, {
      method: "POST",
    });
  },
  async createSubmission(payload: {
    requirementId?: string;
    competitionId?: string;
    runtime: string;
    file?: File | null;
    agentSource?: "upload" | "demo_replay" | "builtin_arc_agent";
    taskType?: "web" | "mobile" | "kernel" | "mixed" | "cli";
    displayName?: string;
    baseUrl?: string;
    apiKey?: string;
    model?: string;
    visualModel?: string;
    credentialMode?: "self_funded" | "official_evaluation";
    catalog?: "playground" | "competition" | "benchmark" | "my_tasks";
    templateSelections?: Record<string, string>;
  }) {
    const form = new FormData();
    if (payload.requirementId) {
      form.append("requirement_id", payload.requirementId);
    }
    if (payload.competitionId) {
      form.append("competition_id", payload.competitionId);
    }
    form.append("runtime", payload.runtime);
    form.append("catalog", payload.catalog ?? "playground");
    form.append("agent_source", payload.agentSource ?? "upload");
    form.append("credential_mode", payload.credentialMode ?? "self_funded");
    if (payload.taskType) {
      form.append("task_type", payload.taskType);
    }
    if (payload.displayName && payload.displayName.trim()) {
      form.append("display_name", payload.displayName.trim());
    }
    if (payload.baseUrl && payload.baseUrl.trim()) {
      form.append("base_url", payload.baseUrl.trim());
    }
    if (payload.apiKey && payload.apiKey.trim()) {
      form.append("api_key", payload.apiKey.trim());
    }
    if (payload.model && payload.model.trim()) {
      form.append("model", payload.model.trim());
    }
    if (payload.visualModel && payload.visualModel.trim()) {
      form.append("visual_model", payload.visualModel.trim());
    }
    if (payload.file) {
      form.append("file", payload.file);
    }
    if (payload.templateSelections) {
      form.append("template_selections", JSON.stringify(payload.templateSelections));
    }
    return request<{ submission: AgentSubmissionSummary }>("/submissions", {
      method: "POST",
      body: form,
    });
  },
  createRun(submissionId: string, requirementId?: string) {
    const form = new FormData();
    form.append("submission_id", submissionId);
    if (requirementId) form.append("requirement_id", requirementId);
    return request<{ run: SubmissionSummary }>("/runs", { method: "POST", body: form });
  },
  startSubmission(submissionId: string) {
    return request<SubmissionDetail>(`/runs/${submissionId}/start`, {
      method: "POST",
    });
  },
  rerunSubmission(submissionId: string, requirementId: string) {
    const form = new FormData();
    form.append("requirement_id", requirementId);
    return request<{ run: SubmissionSummary }>(`/runs/${submissionId}/rerun`, {
      method: "POST",
      body: form,
    });
  },
  deleteSubmission(submissionId: string) {
    return request<void>(`/submissions/${submissionId}`, { method: "DELETE" });
  },
  deleteSubmissions(submissionIds: string[]) {
    return request<BulkDeleteResult>("/submissions/batch-delete", {
      method: "POST",
      body: JSON.stringify({ ids: submissionIds }),
    });
  },
  deleteRun(runId: string) {
    return request<void>(`/runs/${runId}`, { method: "DELETE" });
  },
  deleteRuns(runIds: string[]) {
    return request<BulkDeleteResult>("/runs/batch-delete", {
      method: "POST",
      body: JSON.stringify({ ids: runIds }),
    });
  },
  async downloadSubmissionArchive(submissionId: string) {
    const response = await fetch(apiUrl(`/submissions/${submissionId}/archive`), { credentials: "include" });
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({ detail: response.statusText }))) as ApiErrorPayload;
      throw new ApiError(formatErrorMessage(payload, response.statusText || "Request failed"), response.status);
    }
    const blob = await response.blob();
    const filename = response.headers.get("Content-Disposition")?.match(/filename=\"?([^\"]+)\"?/)?.[1] ?? `${submissionId}.zip`;
    return new File([blob], filename, { type: "application/zip" });
  },
  getSubmissionPreviewUrl(submissionId: string) {
    const host = typeof window !== "undefined" ? window.location.hostname : "127.0.0.1";
    return `http://${host}:3000/`;
  },

  getWorkspaceFiles(submissionId: string) {
    return request<WorkspaceFileListPayload>(`/runs/${submissionId}/workspace/files`);
  },

  async downloadSubmissionTemplateBundle(submissionId: string) {
    const response = await fetch(apiUrl(`/runs/${submissionId}/workspace/template-bundle`), {
      credentials: "include",
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({ detail: response.statusText }))) as ApiErrorPayload;
      throw new ApiError(formatErrorMessage(payload, response.statusText || "Request failed"), response.status);
    }
    const blob = await response.blob();
    const filename = response.headers.get("Content-Disposition")
      ?.match(/filename=\"?([^"]+)\"?/)
      ?.[1] ?? `${submissionId}-template.zip`;
    return new File([blob], filename, { type: "application/zip" });
  },

  updateWorkspaceFile(submissionId: string, payload: FileUpdatePayload) {
    return request<{ detail: string }>(`/runs/${submissionId}/workspace/files`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  createTest(submissionId: string, payload: TestCreatePayload) {
    return request<{ detail: string; file_path: string }>(`/runs/${submissionId}/workspace/tests`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  listMyTasks() {
    return request<UserTaskSummary[]>("/my-tasks");
  },
  getMyTask(taskId: string) {
    return request<UserTaskDetail>(`/my-tasks/${taskId}`);
  },
  createMyTask(payload: {
    title: string;
    task_type: "web" | "mobile" | "kernel" | "mixed" | "cli";
    summary: string;
    root_requirement_id: string;
    node_count: number;
    atomic_count: number;
    yaml_content: string;
    markdown_content: string;
    draft_id?: string | null;
  }) {
    return request<UserTaskDetail>("/my-tasks", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateMyTask(taskId: string, payload: {
    title: string;
    task_type: "web" | "mobile" | "kernel" | "mixed" | "cli";
    summary: string;
    root_requirement_id: string;
    node_count: number;
    atomic_count: number;
    yaml_content: string;
    markdown_content: string;
  }) {
    return request<UserTaskDetail>(`/my-tasks/${taskId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  createMyTaskDraft() {
    return request<UserTaskDraft>("/my-tasks/drafts", {
      method: "POST",
    });
  },
  saveMyTaskDraft(draftId: string, payload: {
    title: string;
    task_type: "web" | "mobile" | "kernel" | "mixed" | "cli";
    yaml_content: string;
    markdown_content: string;
  }) {
    return request<UserTaskDraft>(`/my-tasks/drafts/${draftId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  async importMyTaskDraftBundle(file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<UserTaskDraft>("/my-tasks/drafts/import-bundle", {
      method: "POST",
      body: form,
    });
  },
  async downloadMyTaskDraftBundle(draftId: string) {
    const response = await fetch(apiUrl(`/my-tasks/drafts/${draftId}/bundle`), {
      credentials: "include",
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({ detail: response.statusText }))) as ApiErrorPayload;
      throw new ApiError(formatErrorMessage(payload, response.statusText || "Request failed"), response.status);
    }
    const blob = await response.blob();
    const filename = response.headers.get("Content-Disposition")
      ?.match(/filename=\"?([^"]+)\"?/)
      ?.[1] ?? "requirement.zip";
    return new File([blob], filename, { type: "application/zip" });
  },
  async uploadMyTaskDraftReference(draftId: string, file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<{ filename: string; relative_path: string; url: string }>(`/my-tasks/drafts/${draftId}/reference`, {
      method: "POST",
      body: form,
    });
  },
  deleteMyTaskDraftReference(draftId: string, assetPath: string) {
    return request<void>(`/my-tasks/drafts/${draftId}/reference/${encodePathSegments(assetPath)}`, {
      method: "DELETE",
    });
  },
  async uploadMyTaskReference(taskId: string, file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<{ filename: string; relative_path: string; url: string }>(`/my-tasks/${taskId}/reference`, {
      method: "POST",
      body: form,
    });
  },
  deleteMyTaskReference(taskId: string, assetPath: string) {
    return request<void>(`/my-tasks/${taskId}/reference/${encodePathSegments(assetPath)}`, {
      method: "DELETE",
    });
  },
  async downloadMyTaskBundle(taskId: string) {
    const response = await fetch(apiUrl(`/my-tasks/${taskId}/bundle`), {
      credentials: "include",
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({ detail: response.statusText }))) as ApiErrorPayload;
      throw new ApiError(formatErrorMessage(payload, response.statusText || "Request failed"), response.status);
    }
    const blob = await response.blob();
    const filename = response.headers.get("Content-Disposition")
      ?.match(/filename=\"?([^"]+)\"?/)
      ?.[1] ?? `${taskId}.zip`;
    return new File([blob], filename, { type: "application/zip" });
  },
};
