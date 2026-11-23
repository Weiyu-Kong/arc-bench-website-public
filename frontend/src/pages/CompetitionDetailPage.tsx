import { formatBeijingTime } from "../lib/time";
import { CalendarOutlined, CloseOutlined, DeleteOutlined, DownloadOutlined, FileZipOutlined, MoreOutlined, PlusOutlined, RocketOutlined, SafetyCertificateOutlined, TeamOutlined, TrophyOutlined, RightOutlined } from "@ant-design/icons";
import { Drawer, Dropdown, message } from "antd";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import FundingSourceBadge from "../components/submissions/FundingSourceBadge";
import AgentTemplateDialog from "../components/requirements/AgentTemplateDialog";
import ModelConnectionNotice from "../components/submissions/ModelConnectionNotice";
import MarkdownDocument from "../components/requirements/MarkdownDocument";
import { useI18n } from "../i18n/I18nContext";
import { competitionCostNotice } from "../lib/competitionCostNotice";
import { api, apiUrl } from "../lib/api";
import { DEFAULT_OPENAI_BASE_URL } from "../lib/models";
import type { CompetitionDetail, CompetitionRegistrationStatus, CompetitionSubmissionHistoryEntry } from "../lib/types";

const officialRegistrationCopy = {
  en: {
    official: "Official competition",
    signInTitle: "Confirm your team before entering",
    signInDescription: "Sign in with your ARC-Bench account. The team leader can then confirm the roster and create the official competition team.",
    signInAction: "Sign in to continue",
    back: "Back to competitions",
    confirmTitle: "Confirm your competition team",
    confirmDescription: "This is a one-time confirmation. Your account becomes the team leader and your username becomes the team name.",
    teamIdentity: "Team identity",
    teamLeader: "Team leader",
    leaderEmail: "Leader email",
    roster: "Team roster",
    addByEmail: "Add registered members by email",
    rosterHint: "Optional. Every email must already belong to an ARC-Bench account and must not belong to another team.",
    addMember: "Add member",
    member: "Member",
    removeMember: "Remove member",
    confirmRoster: "I confirm this roster and enter the official competition.",
    confirmDetails: "A team is created immediately and listed members are added automatically. Run agents with your own API key; usage and costs are recorded.",
    cancel: "Cancel",
    creating: "Creating team...",
    confirmAction: "Confirm team and enter",
    blockedTitle: "Current team needs attention",
    blockedMessage: "Leave your current unregistered team before confirming a new official competition team.",
    openTeam: "Open team workspace",
    confirmedTeam: "Confirmed team",
    teamWorkspace: "Team workspace",
    mustConfirm: "Confirm the official competition rules before entering.",
    success: "Team confirmed. Your official competition entry is ready.",
  },
  zh: {
    official: "正式赛",
    signInTitle: "确认队伍后进入比赛",
    signInDescription: "请先登录 ARC-Bench 账号。队长随后可以确认参赛名单并创建正式赛队伍。",
    signInAction: "登录后继续",
    back: "返回竞赛首页",
    confirmTitle: "确认正式赛队伍",
    confirmDescription: "该确认仅需完成一次。当前账号将成为队长，账号用户名将作为队伍名称。",
    teamIdentity: "队伍信息",
    teamLeader: "队长",
    leaderEmail: "队长邮箱",
    roster: "参赛名单",
    addByEmail: "通过邮箱添加已注册成员",
    rosterHint: "此项可选。每个邮箱必须已注册 ARC-Bench，并且尚未加入其他队伍。",
    addMember: "添加队员",
    member: "队员",
    removeMember: "移除队员",
    confirmRoster: "我确认以上参赛名单，并参加本次正式赛。",
    confirmDetails: "确认后立即创建队伍并自动加入所列成员。使用自己的 API Key 运行，网站记录用量与费用。",
    cancel: "取消",
    creating: "正在创建队伍…",
    confirmAction: "确认队伍并进入比赛",
    blockedTitle: "当前队伍需要处理",
    blockedMessage: "请先退出当前尚未报名正式赛的队伍，再创建新的正式赛队伍。",
    openTeam: "打开队伍界面",
    confirmedTeam: "已确认队伍",
    teamWorkspace: "队伍界面",
    mustConfirm: "请先确认正式赛规则和参赛名单。",
    success: "队伍确认成功，正式赛入口已经开放。",
  },
};

function registrationErrorMessage(error: unknown, language: "en" | "zh") {
  const text = error instanceof Error ? error.message : String(error);
  if (language !== "zh") return text;
  const prefixes: Array<[string, string]> = [
    ["No ARC-Bench account exists for: ", "以下邮箱尚未注册 ARC-Bench 账号："],
    ["These accounts already belong to a team: ", "以下账号已经加入其他队伍："],
  ];
  const matched = prefixes.find(([prefix]) => text.startsWith(prefix));
  if (matched) return `${matched[1]}${text.slice(matched[0].length)}`;
  const exact: Record<string, string> = {
    "Confirm the official competition rules before entering": "请先确认正式赛规则和参赛名单",
    "Leave your current unregistered team before confirming a new official competition team.": "请先退出当前尚未报名正式赛的队伍，再创建新的正式赛队伍。",
  };
  return exact[text] ?? text;
}

function download(file: File) {
  const url = URL.createObjectURL(file);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = file.name;
  anchor.click();
  URL.revokeObjectURL(url);
}

function formatCompetitionRange(start?: string | null, end?: string | null) {
  if (!start || !end) return "Dates to be announced";
  const formatter = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Shanghai", month: "long", day: "numeric", year: "numeric" });
  return `${formatter.format(new Date(`${start}T00:00:00+08:00`))}\u2013${formatter.format(new Date(`${end}T00:00:00+08:00`))}`;
}

function formatDuration(seconds: number | null) {
  if (seconds == null) return "--";
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes > 0 ? `${minutes}m ${remainder}s` : `${remainder}s`;
}

function formatTokenCount(count: number | null) {
  if (count == null) return "--";
  return count >= 1_000_000 ? `${(count / 1_000_000).toFixed(3)}M` : count.toLocaleString();
}

function formatMeterCost(cost: number | null, currency: string | null, infiniteIfMissing = false) {
  if (cost == null) return infiniteIfMissing ? "∞" : "--";
  return `${cost.toFixed(4)} ${currency || ""}`.trim();
}

const practiceTaskCompetitionIds = new Set(["arc-bench-web", "arc-bench-evolution", "arc-bench-lite"]);
const upcomingBenchmarkTasks = ["Competition task 1", "Competition task 2", "Competition task 3"];

function taskOverview(task: { id: string; title: string; summary: string; module_count: number; total_tests: number }) {
  const taskId = task.id.toLowerCase();
  const catalogTask = {
    label: task.title,
    copy: task.summary || `Implement and test the ${task.title} application according to its published requirements.`,
    modules: `${task.module_count} requirement modules · ${task.total_tests} executable tests`,
  };
  if (taskId.includes("arc-bench-")) return catalogTask;
  if (taskId.includes("smoke") && taskId.includes("counter")) {
    return {
      label: "ARC smoke test · Counter",
      copy: "A minimal behavior-only task for verifying that ARC can compile and run an application through the complete metered evaluation pipeline.",
      modules: "Count display, increment action, decrement action",
    };
  }

  if (taskId.includes("github")) {
    return {
      label: "Software engineering · GitHub-style ERP",
      copy: "A simulated GitHub-style website for software-engineering workflows and ERP-like team operations. Build the collaboration system behind a development organization.",
      modules: "Accounts, organizations, teams, repositories, branches, issues, pull requests, permissions",
    };
  }

  if (taskId.includes("smoke") && taskId.includes("dice")) {
    return {
      label: "ARC smoke test · Dice",
      copy: "A minimal dice application task for verifying the complete competition submission and evaluation pipeline.",
      modules: "Dice value display, roll action",
    };
  }

  return taskId.includes("github") || taskId.includes("sheet") ? {
    label: "Data workspace · Google Sheets-style",
    copy: "A simulated Google Sheets-style website for data storage, presentation, and processing. Build a persistent workspace where people can organize and analyse tabular information.",
    modules: "Workbooks, worksheets, cells, formulas, data operations, filters, validation, pivot summaries",
  } : catalogTask;
}

function competitionStatusLabel(status: string, language: string) {
  if (status === "open") return "Open for submissions";
  if (status === "ended") return language === "zh" ? "已结束" : "Ended";
  return "Upcoming";
}

export default function CompetitionDetailPage() {
  const { competitionId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, classroom, classroomLoading } = useAuth();
  const { language } = useI18n();
  const registrationCopy = officialRegistrationCopy[language];
  const [competition, setCompetition] = useState<CompetitionDetail | null>(null);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [submissions, setSubmissions] = useState<CompetitionSubmissionHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const tab: "create" | "history" = searchParams.get("tab") === "history" ? "history" : "create";
  const [runtime, setRuntime] = useState("python");
  const [file, setFile] = useState<File | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState(DEFAULT_OPENAI_BASE_URL);
  const [apiKey, setApiKey] = useState("");
  const [creating, setCreating] = useState(false);
  const [selectedSubmissionIds, setSelectedSubmissionIds] = useState<string[]>([]);
  const [submissionSelectionMode, setSubmissionSelectionMode] = useState(false);
  const [detailSubmissionId, setDetailSubmissionId] = useState<string | null>(null);
  const detailSubmission = submissions.find((item) => item.id === detailSubmissionId);
  const [runningAllFor, setRunningAllFor] = useState<string | null>(null);
  const [runsByTask, setRunsByTask] = useState<Map<string, { id: string; status: string }>>(new Map());
  const [runAllNotice, setRunAllNotice] = useState<{ submissionId: string; failed: boolean; text: string } | null>(null);
  const [registrationStatus, setRegistrationStatus] = useState<CompetitionRegistrationStatus | null>(null);
  const [memberEmails, setMemberEmails] = useState<string[]>([""]);
  const [registrationConfirmed, setRegistrationConfirmed] = useState(false);
  const [registrationError, setRegistrationError] = useState<string | null>(null);
  const [confirmingRegistration, setConfirmingRegistration] = useState(false);
  const [downloadingInitialProjects, setDownloadingInitialProjects] = useState(false);
  const [savingLeaderboardSelection, setSavingLeaderboardSelection] = useState(false);
  const hasClassroom = Boolean(classroom?.classroom);
  const isPersonalRegistration = competitionId === "first-competition" && !hasClassroom;
  const requiresOfficialRegistration = !hasClassroom && competitionId === "hackathon";

  const refresh = async () => {
    const detail = await api.getCompetition(competitionId);
    setCompetition(detail);
    if (!user) {
      setSubmissions([]);
      setRunsByTask(new Map());
      return;
    }
    const [history, runs] = await Promise.all([
      api.getCompetitionSubmissionHistory(competitionId),
      api.listRuns(),
    ]);
    setSubmissions(history);
    // Submission history only reports completed runs, so a run that is still
    // queued leaves its task looking untouched. Track every run the user owns
    // so a task already under way is neither started twice nor shown as unrun.
    setRunsByTask(new Map(runs.map((run) => [
      `${run.submission_id}|${run.requirement_id}`,
      { id: run.id, status: run.status },
    ])));
  };

  const selectLeaderboardSubmission = async (submissionId: string | null) => {
    if (savingLeaderboardSelection) return;
    setSavingLeaderboardSelection(true);
    try {
      await api.selectCompetitionLeaderboardSubmission(competitionId, submissionId);
      await refresh();
      message.success(language === "zh" ? "上榜提交已更新。" : "Leaderboard submission updated.");
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setSavingLeaderboardSelection(false);
    }
  };

  useEffect(() => {
    setDetailSubmissionId(null);
    setLoading(true);
    const load = async () => {
      setAccessError(null);
      if (requiresOfficialRegistration) {
        if (!user) {
          setRegistrationStatus(null);
          setCompetition(null);
          return;
        }
        const status = await api.getCompetitionRegistration(competitionId);
        setRegistrationStatus(status);
        if (!status.registered) {
          setCompetition(null);
          setSubmissions([]);
          setRunsByTask(new Map());
          return;
        }
      } else {
        setRegistrationStatus(null);
      }
      await refresh();
    };
    load().catch((error) => {
      setCompetition(null);
      const detail = (error as Error).message;
      setAccessError(detail);
      message.error(detail);
    }).finally(() => setLoading(false));
  }, [competitionId, user, hasClassroom]);

  const confirmOfficialRegistration = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!registrationConfirmed && !isPersonalRegistration) {
      message.error(registrationCopy.mustConfirm);
      return;
    }
    setConfirmingRegistration(true);
    setRegistrationError(null);
    try {
      const status = await api.confirmCompetitionRegistration(competitionId, {
        member_emails: isPersonalRegistration ? [] : memberEmails.map((email) => email.trim()).filter(Boolean),
        confirmed: true,
      });
      setRegistrationStatus(status);
      message.success(isPersonalRegistration ? (language === "zh" ? "参赛确认成功，请使用自己的 API Key 运行。" : "Entry confirmed. Run with your own API key.") : registrationCopy.success);
      await refresh();
    } catch (error) {
      const detail = registrationErrorMessage(error, language);
      setRegistrationError(detail);
      message.error(detail);
    } finally {
      setConfirmingRegistration(false);
    }
  };

  const canManageOfficialSubmission = competitionId === "first-competition" ? Boolean(user) : !classroomLoading && (!user || classroom !== null) && (hasClassroom
    ? classroom?.is_team_leader === true && classroom.tracks.some((track) => track.id === competitionId)
    : isPersonalRegistration ? registrationStatus?.registered === true : !requiresOfficialRegistration || registrationStatus?.is_team_leader === true);
  const canCreate = Boolean(file) && canManageOfficialSubmission && competition?.status !== "ended";
  const templateUrl = apiUrl(`/competitions/${competitionId}/starter-agent`);
  const taskCards = useMemo(
    () => competition?.tasks.map((task) => ({
      task,
      overview: taskOverview(task),
    })) ?? [],
    [competition?.tasks],
  );

  const setTab = (nextTab: "create" | "history") => {
    if (nextTab !== "history") {
      setSubmissionSelectionMode(false);
      setSelectedSubmissionIds([]);
      setDetailSubmissionId(null);
    }
    const nextParams = new URLSearchParams(searchParams);
    if (nextTab === "history") {
      nextParams.set("tab", "history");
    } else {
      nextParams.delete("tab");
    }
    setSearchParams(nextParams, { replace: true });
  };

  const createSubmission = async () => {
    if (!user) {
      navigate("/login", { state: { from: `/competitions/${competitionId}` } });
      return;
    }
    if (competition?.status === "ended") {
      message.error(language === "zh" ? "竞赛已结束，不能提交或运行。" : "This competition has ended; submissions and runs are closed.");
      return;
    }
    if (!canCreate) {
      message.error("Choose an agent ZIP before saving the submission.");
      return;
    }
    if (!baseUrl.trim()) {
      message.error("Base URL is required.");
      return;
    }
    if (!apiKey.trim()) {
      message.error("API key is required.");
      return;
    }
    try {
      setCreating(true);
      await api.createSubmission({
        competitionId,
        runtime,
        file,
        agentSource: "upload",
        displayName,
        baseUrl,
        apiKey,
        credentialMode: "self_funded",
        catalog: "competition",
      });
      setFile(null);
      setDisplayName("");
      setApiKey("");
      setTab("history");
      message.success("Agent submission saved. Open a task to run it.");
      await refresh();
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const downloadInitialProjects = async () => {
    try {
      setDownloadingInitialProjects(true);
      download(await api.downloadCompetitionInitialProjects(competitionId));
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setDownloadingInitialProjects(false);
    }
  };

  const deleteSelectedSubmissions = async () => {
    if (selectedSubmissionIds.length === 0) return;
    const count = selectedSubmissionIds.length;
    if (!window.confirm(`Delete ${count} selected submission${count === 1 ? "" : "s"}? The stored agent archives will be permanently removed; existing task runs remain available.`)) return;
    try {
      const result = await api.deleteSubmissions(selectedSubmissionIds);
      setSelectedSubmissionIds(result.skipped.map((item) => item.id));
      setSubmissionSelectionMode(result.skipped.length > 0);
      await refresh();
      if (result.deleted_ids.length > 0) message.success(`${result.deleted_ids.length} submission${result.deleted_ids.length === 1 ? "" : "s"} deleted.`);
      if (result.skipped.length > 0) message.warning(`${result.skipped.length} submission${result.skipped.length === 1 ? "" : "s"} could not be deleted: ${result.skipped[0].reason}`);
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  const toggleSubmissionSelection = (submissionId: string) => {
    if (!submissions.find((submission) => submission.id === submissionId)?.can_delete) return;
    setSelectedSubmissionIds((current) => current.includes(submissionId) ? current.filter((id) => id !== submissionId) : [...current, submissionId]);
  };

  // A submission is only ranked once it has completed every task in the
  // competition, and tasks without a run count as zero. Starting the missing
  // ones from here saves opening each task page in turn.
  const pendingTasks = (submission: CompetitionSubmissionHistoryEntry) =>
    submission.task_scores.filter((taskScore) =>
      !taskScore.run_id && !runsByTask.has(`${submission.id}|${taskScore.task_id}`));

  // The API only accepts runs from the newest saved snapshot, so offering the
  // button on an older one would fire requests that are certain to be refused.
  const latestSubmissionId = useMemo(
    () => submissions.reduce<CompetitionSubmissionHistoryEntry | null>(
      (latest, entry) => (!latest || entry.created_at > latest.created_at ? entry : latest),
      null,
    )?.id ?? null,
    [submissions],
  );

  const runPendingTasks = async (submission: CompetitionSubmissionHistoryEntry) => {
    const pending = pendingTasks(submission);
    if (competition?.status === "ended" || pending.length === 0 || submission.id !== latestSubmissionId) return;
    const targets = pending;
    const label = submission.display_name || submission.id;
    if (!window.confirm(
      `Start ${targets.length} run${targets.length === 1 ? "" : "s"} for "${label}"? `
      + "Each run may incur charges on your API account."
      + (competitionCostNotice(competitionId, language) ? `\n\n${competitionCostNotice(competitionId, language)}` : ""),
    )) return;

    setRunningAllFor(submission.id);
    setRunAllNotice(null);
    const failures: string[] = [];
    let started = 0;
    // Sequential on purpose: the runs go through the same queue either way, and
    // stopping on the first rejection would hide which tasks did start.
    for (const taskScore of targets) {
      try {
        const created = await api.createRun(submission.id, taskScore.task_id);
        await api.startSubmission(created.run.id);
        started += 1;
      } catch (error) {
        failures.push(`${taskScore.task_title}: ${(error as Error).message}`);
      }
    }
    setRunningAllFor(null);

    const startedText = `Started ${started} run${started === 1 ? "" : "s"}.`;
    const failedText = `${failures.length} task${failures.length === 1 ? "" : "s"} could not start.\n${failures.join("\n")}`.trim();
    if (started > 0) message.success(startedText);
    if (failures.length > 0) window.alert(`${startedText}\n\n${failedText}`);
    // This action spends model credits, so its outcome is reported in the panel
    // as well and does not rely on a toast being seen.
    setRunAllNotice({
      submissionId: submission.id,
      failed: failures.length > 0,
      text: failures.length > 0 ? `${started > 0 ? `${startedText} ` : ""}${failedText}` : startedText,
    });
    try {
      await refresh();
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  if (loading) return <div className="page centered"><div className="loading-state">Loading competition...</div></div>;
  if (accessError) return (
    <div className="page official-registration-page">
      <section className="official-registration-shell official-registration-auth">
        <div className="official-registration-mark"><SafetyCertificateOutlined /></div>
        <span className="official-registration-kicker">Restricted competition</span>
        <h1>Competition access unavailable</h1>
        <p>{accessError}</p>
        {!user ? <Link className="btn-primary official-registration-primary" to="/login" state={{ from: `/competitions/${competitionId}` }}>Sign in to continue</Link> : null}
        <Link className="official-registration-back" to="/requirements">Back to competitions</Link>
      </section>
    </div>
  );
  if (requiresOfficialRegistration && !user) return (
    <div className="page official-registration-page">
      <section className="official-registration-shell official-registration-auth">
        <div className="official-registration-mark"><SafetyCertificateOutlined /></div>
        <span className="official-registration-kicker">{registrationCopy.official}</span>
        <h1>{registrationCopy.signInTitle}</h1>
        <p>{isPersonalRegistration ? (language === "zh" ? "使用普通 ARC-Bench 账号登录即可确认参赛。" : "Sign in with any ARC-Bench account to confirm your entry.") : registrationCopy.signInDescription}</p>
        <Link className="btn-primary official-registration-primary" to="/login" state={{ from: `/competitions/${competitionId}` }}>{registrationCopy.signInAction}</Link>
        <Link className="official-registration-back" to="/requirements">{registrationCopy.back}</Link>
      </section>
    </div>
  );
  if (requiresOfficialRegistration && registrationStatus && !registrationStatus.registered) return (
    <div className="page official-registration-page">
      <div className="official-registration-shell">
        <header className="official-registration-header">
          <div>
            <span className="official-registration-kicker"><SafetyCertificateOutlined /> {registrationCopy.official}</span>
            <h1>{isPersonalRegistration ? (language === "zh" ? "确认参加 First Competition 正式赛" : "Confirm your First Competition entry") : registrationCopy.confirmTitle}</h1>
            <p>{isPersonalRegistration ? (language === "zh" ? "点击确认即可参赛，无需创建队伍。使用自己的 API Key 运行，网站记录用量与费用。" : "Confirm to participate without a team. Use your own API key; the website records usage and costs.") : registrationCopy.confirmDescription}</p>
          </div>
        </header>

        {registrationStatus.can_confirm ? (
          <form className="official-registration-form" onSubmit={confirmOfficialRegistration}>
            {!isPersonalRegistration && <section className="official-registration-team-card">
              <div className="official-registration-section-title"><TeamOutlined /><div><span>{registrationCopy.teamIdentity}</span><h2>{user?.username}</h2></div></div>
              <dl><div><dt>{registrationCopy.teamLeader}</dt><dd>{user?.username}</dd></div><div><dt>{registrationCopy.leaderEmail}</dt><dd>{user?.email}</dd></div></dl>
            </section>}

            {!isPersonalRegistration && <section className="official-registration-members">
              <div className="official-registration-members-heading"><div><span>{registrationCopy.roster}</span><h2>{registrationCopy.addByEmail}</h2><p>{registrationCopy.rosterHint}</p></div><button type="button" onClick={() => setMemberEmails((emails) => [...emails, ""])} disabled={memberEmails.length >= 20}><PlusOutlined /> {registrationCopy.addMember}</button></div>
              <div className="official-registration-email-list">
                {memberEmails.map((email, index) => (
                  <label key={index}><span>{registrationCopy.member} {index + 1}</span><div><input type="email" value={email} onChange={(event) => setMemberEmails((emails) => emails.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} placeholder="teammate@example.com" /><button type="button" aria-label={`${registrationCopy.removeMember} ${index + 1}`} onClick={() => setMemberEmails((emails) => emails.length === 1 ? [""] : emails.filter((_, itemIndex) => itemIndex !== index))}><CloseOutlined /></button></div></label>
                ))}
              </div>
            </section>}

            {!isPersonalRegistration && <label className="official-registration-confirm"><input type="checkbox" checked={registrationConfirmed} onChange={(event) => setRegistrationConfirmed(event.target.checked)} /><span><strong>{registrationCopy.confirmRoster}</strong><small>{registrationCopy.confirmDetails}</small></span></label>}
            {registrationError ? <div className="official-registration-error" role="alert">{registrationError}</div> : null}
            <div className="official-registration-actions"><Link to="/requirements">{registrationCopy.cancel}</Link><button className="btn-primary" type="submit" disabled={(!registrationConfirmed && !isPersonalRegistration) || confirmingRegistration}>{confirmingRegistration ? registrationCopy.creating : isPersonalRegistration ? (language === "zh" ? "确认参赛" : "Confirm entry") : registrationCopy.confirmAction}</button></div>
          </form>
        ) : (
          <section className="official-registration-blocked">
            <TeamOutlined />
            <h2>{registrationCopy.blockedTitle}</h2>
            <p>{language === "zh" ? registrationCopy.blockedMessage : registrationStatus.message}</p>
            <Link className="btn-primary" to="/profile?view=team">{registrationCopy.openTeam}</Link>
          </section>
        )}
      </div>
    </div>
  );
  if (!competition) return <div className="page centered"><div className="empty-state">Competition not found.</div></div>;
  const showsPracticeTasks = practiceTaskCompetitionIds.has(competition.id);

  return (
    <div className="page competition-detail-page">
      <div className="competition-container">
        <div className="competition-breadcrumb">
          <Link to="/requirements">Competition</Link><RightOutlined /><span>{competition.id === "first-competition" ? "Smoke Competition" : competition.title}</span>
        </div>

        <section className="competition-detail-heading">
          <div className="competition-meta-row">
            <span className="competition-stage"><TrophyOutlined /> {competitionStatusLabel(competition.status, language)}</span>
            {competition.type === "official" ? <span className="competition-official-pill"><SafetyCertificateOutlined /> {registrationCopy.official}</span> : null}
            <span>{competition.id.toUpperCase()}</span>
          </div>
          <h1>{competition.id === "first-competition" ? "Smoke Competition" : competition.title}</h1>
          <p className="competition-detail-date"><CalendarOutlined /> {formatCompetitionRange(competition.starts_at, competition.ends_at)}</p>
        </section>

        {competition.status === "ended" ? <div className="inline-alert">{language === "zh" ? "竞赛已结束，提交和运行已关闭，仍可查看历史结果与排行榜。" : "This competition has ended. Submissions and runs are closed; historical results and leaderboards remain available."}</div> : null}
        <details className="competition-model-guide">
          <summary>
            <span><strong>{language === "zh" ? "模型调用与计费" : "Model access & accounting"}</strong><small>Base URL → OPENAI_BASE_URL · API Key → OPENAI_API_KEY</small></span>
            <span className="competition-model-guide-action">{language === "zh" ? "查看说明" : "Read guide"}<RightOutlined /></span>
          </summary>
          <ModelConnectionNotice />
        </details>
        <div className="competition-reading-layout competition-hackathon-layout competition-overview-layout">
          <main className="competition-description competition-description-scroll">
            <section className="competition-story-block competition-markdown-description">
              {competition.description_markdown ? <MarkdownDocument markdown={competition.description_markdown} assetsBaseUrl="" referencesBaseUrl="" /> : <p>{competition.summary}</p>}
            </section>

            <section className="competition-task-highlights" aria-labelledby="task-highlights-title">
              <div className="competition-section-heading">
                <div>
                  <h2 id="task-highlights-title">{showsPracticeTasks ? (language === "zh" ? "练习任务" : "Practice tasks") : (language === "zh" ? "任务" : "Tasks")}</h2>
                </div>
                <span>{competition.task_count} {language === "zh" ? "个任务" : "tasks"}</span>
              </div>

              <div className="competition-task-briefs">
                {taskCards.map(({ task, overview }) => (
                  <Link key={task.id} to={`/competitions/${competition.id}/tasks/${task.id}`} className="competition-task-brief competition-task-card-link">
                    <div className="competition-task-brief-heading"><h3 title={overview.label}>{overview.label}</h3>{showsPracticeTasks ? <span className="competition-task-badge practice">{language === "zh" ? "练习" : "Practice"}</span> : null}</div>
                    <p className="competition-task-card-description" title={overview.copy}>{overview.copy}</p>
                    <div className="competition-task-card-meta">
                      <p className="competition-task-brief-modules">{overview.modules}</p>
                      {task.stage_number ? <p className="competition-task-brief-modules">{language === "zh" ? "阶段" : "Stage"} {task.stage_number}</p> : null}
                    </div>
                    <span className="competition-task-card-action">{language === "zh" ? "查看任务" : "Open task"}<RightOutlined /></span>
                  </Link>
                ))}
              </div>
            </section>

            {showsPracticeTasks ? <section className="competition-upcoming-tasks" aria-labelledby="competition-upcoming-title">
              <div className="competition-section-heading"><h2 id="competition-upcoming-title">{language === "zh" ? "竞赛任务" : "Competition tasks"}</h2><span>{language === "zh" ? "待开放" : "Coming soon"}</span></div>
              <div className="competition-upcoming-grid">{upcomingBenchmarkTasks.map((title) => (
                <article key={title} className="competition-upcoming-card">
                  <span className="competition-task-badge competition">{language === "zh" ? "竞赛" : "Competition"}</span>
                  <h3>{title}</h3>
                  <p>{language === "zh" ? "任务发布后开放" : "Available when published"}</p>
                </article>
              ))}</div>
            </section> : null}

          </main>

          <aside className="competition-sidebar">
            <section className="competition-panel competition-submission-panel">
              <div className="competition-panel-tabs">
                <button type="button" className={tab === "create" ? "active" : ""} onClick={() => setTab("create")}>New submission</button>
                <button type="button" className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>History ({submissions.length})</button>
              </div>
              {tab === "create" ? (
                !canManageOfficialSubmission ? (
                  <div className="competition-empty">Only the team leader can create or replace a competition submission. Team members can view shared submissions and results.</div>
                ) : (
                <div className="competition-submission-form">
                  <div><h2>Save an agent snapshot</h2><p>This snapshot is shared by all competition tasks. Choose a task afterwards to run it.</p></div>
                  <div className="competition-field-label">Agent package</div>
                  <AgentTemplateDialog href={templateUrl} runtime={runtime} />
                  {competition.template_required ? (
                    <div>
                      <p className="upload-hint">
                        {competition.template_source_competition_id === "hackathon"
                          ? "ARC-Bench automatically uses the application selected by your latest Official Hackathon leaderboard submission for each matching task. Tasks without an eligible source result start from the blank application template."
                          : `ARC-Bench automatically uses your highest-scoring application from ${competition.template_source_competition_id} for each matching task. Tasks without a previous run start from the blank application template.`}
                      </p>
                      {competition.template_source_competition_id === "hackathon" ? <button type="button" className="btn-outline w-full competition-initial-project-download" disabled={downloadingInitialProjects} onClick={() => void downloadInitialProjects()}><DownloadOutlined /> {downloadingInitialProjects ? "Preparing projects..." : "Download GitHub and Sheet projects"}</button> : null}
                    </div>
                  ) : null}
                  <label className={`upload-zone${file ? " has-file" : ""}`}><input className="visually-hidden" type="file" accept=".zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><div className="upload-icon"><FileZipOutlined /></div><div className="upload-text">{file ? "Upload successful" : "Drop your agent code here"}</div>{file ? <div className="upload-filename">{file.name}</div> : null}<div className="upload-hint">{file ? "Click to choose a different ZIP file." : "Keep the runtime entrypoint at the ZIP root."}</div></label>
                  <div className="env-selector">{["python", "javascript", "typescript"].map((item) => <button type="button" key={item} className={`env-option${runtime === item ? " active" : ""}`} onClick={() => setRuntime(item)}>{item === "python" ? "Python" : item === "javascript" ? "JavaScript" : "TypeScript"}</button>)}</div>
                  <label className="field-stack"><span>Submission name</span><input className="text-input" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="submission" /></label>
                  <label className="field-stack"><span>Base URL</span><input className="text-input" type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
                  <label className="field-stack"><span>API Key</span><input className="text-input" type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="Required" /></label>
                  <button type="button" onClick={() => void createSubmission()} disabled={creating || !canCreate} className="btn-primary w-full">{creating ? "Saving..." : "Save submission"}</button>
                </div>
                )
              ) : (
                <div className="competition-submission-history">
                  {!user ? <div className="competition-empty">Sign in to manage your saved agent submissions.</div>
                    : submissions.length === 0 ? <div className="competition-empty">No saved agent submissions yet.</div>
                      : <>
                        <div className="competition-history-header">
                          <div>
                            <span className="competition-history-eyebrow">{language === "zh" ? "排行榜提交" : "Leaderboard submission"}</span>
                            <strong>{submissions.some((item) => item.leaderboard_selection_mode === "manual")
                              ? language === "zh" ? "手动指定" : "Manually selected"
                              : language === "zh" ? "自动使用最新提交" : "Latest submission"}</strong>
                          </div>
                          {submissions.some((item) => item.can_select_for_leaderboard && item.leaderboard_selection_mode === "manual") ? <button type="button" className="competition-history-text-button" disabled={savingLeaderboardSelection} onClick={() => void selectLeaderboardSubmission(null)}>
                            {language === "zh" ? "恢复自动" : "Use latest"}
                          </button> : null}
                        </div>
                        <details className="competition-history-help">
                          <summary>{language === "zh" ? "计分与上榜规则" : "Scoring & leaderboard rules"}</summary>
                          <p>{language === "zh" ? "每个任务使用该提交的最佳有效运行。使用自己的 API Key，网站按统一价格累计已记录的有效费用，缺失部分跳过。默认使用最新提交，也可手动指定。" : "Each task uses this submission’s best valid run. Use your own API key; recorded usage is priced at website rates, skipping missing values. The latest submission is used unless selected manually."}</p>
                          {competition.id === "hackathon" ? <p>{language === "zh" ? "GitHub 取最新有效原始评测与三个阶段最新有效评测组合中的较高分。缺失阶段按零通过数、零开销处理；后续阶段使用上一阶段最新应用，无可用结果时使用空白模板。" : "GitHub uses the higher score of the latest valid original evaluation and the combined latest valid evaluations of its three stages. Missing stages count as zero passes and zero cost. Later stages use the latest previous-stage application, or a blank template when unavailable."}</p> : null}
                        </details>
                        {canManageOfficialSubmission ? <div className="submission-history-toolbar competition-history-toolbar">
                          {submissionSelectionMode ? <>
                            <label><input type="checkbox" disabled={!submissions.some((submission) => submission.can_delete)} checked={submissions.some((submission) => submission.can_delete) && submissions.filter((submission) => submission.can_delete).every((submission) => selectedSubmissionIds.includes(submission.id))} onChange={(event) => setSelectedSubmissionIds(event.target.checked ? submissions.filter((submission) => submission.can_delete).map((submission) => submission.id) : [])} />{language === "zh" ? "全选可删除项" : "Select deletable"}</label>
                            <div className="history-selection-actions">
                              <button type="button" className="history-select-button" onClick={() => { setSubmissionSelectionMode(false); setSelectedSubmissionIds([]); }}>{language === "zh" ? "取消" : "Cancel"}</button>
                              <button type="button" className="history-bulk-delete" disabled={selectedSubmissionIds.length === 0} onClick={() => void deleteSelectedSubmissions()}><DeleteOutlined /> {language === "zh" ? "删除所选" : "Delete selected"}{selectedSubmissionIds.length ? ` (${selectedSubmissionIds.length})` : ""}</button>
                            </div>
                          </> : <button type="button" className="history-select-button" onClick={() => setSubmissionSelectionMode(true)}>{language === "zh" ? "批量管理" : "Manage submissions"}</button>}
                        </div> : null}
                        <ul className="competition-history-list">
                          {submissions.map((submission) => {
                            const pending = pendingTasks(submission);
                            const busy = runningAllFor === submission.id;
                            const superseded = submission.id !== latestSubmissionId;
                            return <li key={submission.id} className={`competition-history-item${submission.is_selected_score ? " is-selected" : ""}`}>
                              {submissionSelectionMode ? <input type="checkbox" disabled={!submission.can_delete} title={submission.delete_block_reason ?? undefined} checked={selectedSubmissionIds.includes(submission.id)} onChange={() => toggleSubmissionSelection(submission.id)} aria-label={`${language === "zh" ? "选择" : "Select"} ${submission.display_name || submission.id}`} /> : null}
                              <div className="competition-history-item-main">
                                <div className="competition-history-item-title">
                                  <h3 title={submission.display_name || submission.id}>{submission.display_name || submission.id}</h3>
                                  {submission.is_selected_score ? <span className="competition-history-selected">{language === "zh" ? "上榜中" : "On leaderboard"}</span> : null}
                                </div>
                                <div className="competition-history-item-meta"><span title={submission.model_name || ""}>{submission.model_name || "—"}</span><time dateTime={submission.created_at}>{formatBeijingTime(submission.created_at)}</time></div>
                              </div>
                              <div className="competition-history-item-score"><strong>{submission.score_available && submission.score != null ? submission.score.toFixed(2) : "—"}</strong><span>{language === "zh" ? "得分" : "Score"}</span></div>
                              <div className="competition-history-item-actions">
                                <button type="button" className="competition-history-detail-button" onClick={() => setDetailSubmissionId(submission.id)}>{language === "zh" ? "详情" : "Details"}</button>
                                <Dropdown trigger={["click"]} placement="bottomRight" menu={{ items: [
                                  ...(submission.can_select_for_leaderboard ? [{ key: "leaderboard", label: language === "zh" ? "选择此提交上榜" : "Use on leaderboard", icon: <TrophyOutlined />, disabled: savingLeaderboardSelection || submission.is_selected_score && submission.leaderboard_selection_mode === "manual", onClick: () => void selectLeaderboardSubmission(submission.id) }] : []),
                                  ...(canManageOfficialSubmission ? [{ key: "run", label: busy ? language === "zh" ? "正在启动…" : "Starting…" : superseded ? language === "zh" ? "仅最新提交可运行" : "Newer submission available" : pending.length === 0 ? language === "zh" ? "所有任务均已运行" : "All tasks have a run" : language === "zh" ? `运行剩余 ${pending.length} 个任务` : `Run ${pending.length} remaining tasks`, icon: <RocketOutlined />, disabled: competition.status === "ended" || superseded || pending.length === 0 || runningAllFor !== null, onClick: () => void runPendingTasks(submission) }] : []),
                                  { key: "download", label: language === "zh" ? "下载代码" : "Download code", icon: <DownloadOutlined />, onClick: () => { void api.downloadSubmissionArchive(submission.id).then(download).catch((error) => message.error(error.message)); } },
                                ] }}>
                                  <button type="button" className="competition-history-more-button" aria-label={`${language === "zh" ? "更多操作：" : "More actions for "}${submission.display_name || submission.id}`}><MoreOutlined /></button>
                                </Dropdown>
                              </div>
                              {runAllNotice?.submissionId === submission.id ? <p className={`competition-history-item-notice${runAllNotice.failed ? " is-error" : ""}`} role="status">{runAllNotice.text}</p> : null}
                            </li>;
                          })}
                        </ul>
                      </>}
                </div>
              )}
            </section>
          </aside>
        </div>
        <Drawer
          title={language === "zh" ? "提交详情" : "Submission details"}
          placement="right"
          width="min(600px, 100vw)"
          open={Boolean(detailSubmission)}
          onClose={() => setDetailSubmissionId(null)}
          rootClassName="competition-submission-drawer"
        >
          {detailSubmission ? <div className="competition-submission-detail">
            <header className="competition-submission-detail-heading">
              {detailSubmission.is_selected_score ? <span className="competition-history-selected">{language === "zh" ? "当前上榜提交" : "Leaderboard submission"}</span> : null}
              <h2>{detailSubmission.display_name || detailSubmission.id}</h2>
              <p>{formatBeijingTime(detailSubmission.created_at)}</p>
            </header>
            <dl className="competition-submission-detail-meta">
              <div><dt>{language === "zh" ? "模型" : "Model"}</dt><dd>{detailSubmission.model_name || "—"}</dd></div>
              <div><dt>{language === "zh" ? "代码包" : "Archive"}</dt><dd>{detailSubmission.original_filename}</dd></div>
              <div><dt>{language === "zh" ? "运行环境" : "Runtime"}</dt><dd>{detailSubmission.runtime}</dd></div>
              <div><dt>{language === "zh" ? "计费来源" : "Billing source"}</dt><dd><FundingSourceBadge source={detailSubmission.credential_mode === "self_funded" || detailSubmission.credential_mode === "official_evaluation" ? detailSubmission.credential_mode : null} /></dd></div>
            </dl>
            <section aria-label={language === "zh" ? "提交汇总" : "Submission summary"}>
              <div className="competition-submission-detail-metrics">
                <div className="is-primary"><span>{language === "zh" ? "得分" : "Score"}</span><strong>{detailSubmission.score_available && detailSubmission.score != null ? detailSubmission.score.toFixed(2) : "—"}</strong></div>
                <div><span>{language === "zh" ? "测试通过率" : "Test pass rate"}</span><strong>{detailSubmission.pass_rate.toFixed(1)}%</strong><small>{detailSubmission.passed_count} / {detailSubmission.test_total}</small></div>
                <div><span>{language === "zh" ? "模型开销" : "Model cost"}</span><strong>{formatMeterCost(detailSubmission.total_cost, detailSubmission.token_cost_currency, true)}</strong></div>
                <div><span>{language === "zh" ? "总运行时间" : "Total runtime"}</span><strong>{formatDuration(detailSubmission.total_run_duration_seconds)}</strong></div>
                <div><span>Tokens</span><strong>{formatTokenCount(detailSubmission.total_token_count)}</strong></div>
              </div>
            </section>
            <section className="competition-submission-detail-tasks">
              <h3>{language === "zh" ? "任务结果" : "Task results"}<span>{detailSubmission.task_scores.length}</span></h3>
              {detailSubmission.task_scores.map((taskScore) => {
                const inFlight = runsByTask.get(`${detailSubmission.id}|${taskScore.task_id}`);
                const runId = taskScore.run_id || inFlight?.id;
                return <article className="competition-submission-detail-task" key={taskScore.task_id}>
                  <header><h4>{taskScore.run_id ? <span className="competition-task-run-dot" aria-label={language === "zh" ? "已完成" : "Completed"} /> : null}{taskScore.task_title}</h4>{runId ? <Link to={`/runs/${runId}`}>{taskScore.run_id ? language === "zh" ? "查看运行" : "Open run" : inFlight?.status.toLowerCase()} <RightOutlined /></Link> : <span>{language === "zh" ? "未运行" : "Not run"}</span>}</header>
                  <dl>
                    <div><dt>{language === "zh" ? "得分" : "Score"}</dt><dd>{taskScore.score?.toFixed(2) ?? "—"}</dd></div>
                    <div><dt>{language === "zh" ? "测试通过率" : "Tests"}</dt><dd>{taskScore.test_pass_rate == null ? "—" : `${taskScore.test_pass_rate.toFixed(1)}%`}</dd></div>
                    <div><dt>{language === "zh" ? "功能完成率" : "Features"}</dt><dd>{taskScore.feature_implementation_rate == null ? "—" : `${taskScore.feature_implementation_rate.toFixed(1)}%`}</dd></div>
                    <div><dt>{language === "zh" ? "运行时间" : "Runtime"}</dt><dd>{formatDuration(taskScore.run_duration_seconds)}</dd></div>
                    <div><dt>Tokens</dt><dd>{formatTokenCount(taskScore.token_count)}</dd></div>
                    <div><dt>{language === "zh" ? "开销" : "Cost"}</dt><dd>{formatMeterCost(taskScore.token_cost_usd, taskScore.token_cost_currency, taskScore.run_id != null && taskScore.score_regime === "unavailable")}</dd></div>
                  </dl>
                </article>;
              })}
            </section>
          </div> : null}
        </Drawer>
      </div>
    </div>
  );
}
