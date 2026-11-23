import { DownOutlined, LeftOutlined, RightOutlined, QuestionCircleOutlined, SearchOutlined, StarFilled, TrophyFilled, TrophyOutlined, DownloadOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, Dropdown, Modal, message } from "antd";
import { useAuth } from "../../auth/AuthContext";
import { api, ApiError } from "../../lib/api";
import type { CompetitionLeaderboardEntry } from "../../lib/types";
import { useI18n } from "../../i18n/I18nContext";
import { formatBeijingTime, parseUtcDate } from "../../lib/time";

const leaderboardPageSize = 20;
const leaderboardSkeletonRows = Array.from({ length: leaderboardPageSize }, (_, index) => index);
const minimumSkeletonDurationMs = 600;
type LeaderboardCompetitionOption = {
  id: string;
  label: string;
  sourceId: string | null;
};

const leaderboardCompetitionOptions: LeaderboardCompetitionOption[] = [
  { id: "first-competition", label: "Smoke Competition", sourceId: "first-competition" },
  { id: "arc-bench-web", label: "ARC-Bench-practice", sourceId: "arc-bench-web" },
  { id: "arc-bench-lite", label: "ARC-Bench Lite", sourceId: "arc-bench-lite" },
  { id: "arc-bench-evolution", label: "ARC-Bench evo-practice", sourceId: "arc-bench-evolution" },
  { id: "arc-bench-competition", label: "ARC-Bench-competition", sourceId: null },
  { id: "arc-bench-evo-competition", label: "ARC-Bench evo-competition", sourceId: null },
];

function leaderboardCompetitionOption(id: string) {
  return leaderboardCompetitionOptions.find((option) => option.id === id) ?? null;
}

// Leaderboard task ids come back fully qualified (`arc-bench-web--keep`), while the
// task dropdown and the leaderboard query both use the short trailing segment.
function normalizeTaskId(value: string | null) {
  const raw = (value ?? "").trim();
  if (!raw || raw.toLowerCase() === "all") return "";
  return raw.split("--").pop() ?? raw;
}

async function withMinimumSkeletonDuration<T>(request: Promise<T>): Promise<T> {
  const [result] = await Promise.all([
    request,
    new Promise<void>((resolve) => window.setTimeout(resolve, minimumSkeletonDurationMs)),
  ]);
  return result;
}

function rankDisplay(rank: number) {
  if (rank > 3) return <span className="competition-rank-plain">{rank}</span>;
  return <span className={`competition-rank-badge rank-${rank}`}><span>{rank}</span>{rank === 1 ? <StarFilled /> : <TrophyFilled />}</span>;
}

function formatLeaderboardDate(value?: string | null) {
  if (!value) return "-";
  const date = parseUtcDate(value);
  return Number.isNaN(date.getTime()) ? "-" : formatBeijingTime(date);
}

function paginationItems(current: number, total: number): Array<number | string> {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const pages = Array.from(new Set([1, current - 1, current, current + 1, total]))
    .filter((page) => page >= 1 && page <= total)
    .sort((left, right) => left - right);
  const items: Array<number | string> = [];
  pages.forEach((page, index) => {
    if (index > 0 && page - pages[index - 1] > 1) items.push(`ellipsis-${page}`);
    items.push(page);
  });
  return items;
}

type Props = { admin?: boolean; catalogLoading?: boolean; onAccessDenied?: (status: number) => void };

export default function CompetitionLeaderboard({ admin = false, catalogLoading = false, onAccessDenied }: Props) {
  const { language, t } = useI18n();
  const { user, classroom } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialCompetitionId = searchParams.get("competition")?.trim() ?? "";
  // A task only makes sense alongside the competition it belongs to.
  const initialTaskId = initialCompetitionId ? normalizeTaskId(searchParams.get("task")) : "";
  const [leaderboard, setLeaderboard] = useState<CompetitionLeaderboardEntry[]>([]);
  const loading = catalogLoading;
  const [leaderboardLoading, setLeaderboardLoading] = useState(true);
  const [activeCompetitionId, setActiveCompetitionId] = useState(leaderboardCompetitionOption(initialCompetitionId)?.id ?? "first-competition");
  const [activeTaskId, setActiveTaskId] = useState(initialTaskId || "all");
  // Switching competitions clears the task filter, but arriving with both in
  // the URL must not. Tracking the competition the filter was last cleared for
  // keeps a deep link intact and stays correct under StrictMode's double render.
  const clearedTaskForCompetitionId = useRef(initialCompetitionId);
  // The leaderboard sits below three other sections, so a deep link has to take
  // the reader there rather than dropping them at the top of the page.
  const leaderboardSectionRef = useRef<HTMLElement>(null);
  const pendingLeaderboardScroll = useRef(Boolean(initialCompetitionId));
  const [competitionTasks, setCompetitionTasks] = useState<Array<{ id: string; title: string }>>([]);
  const [competitionMenuOpen, setCompetitionMenuOpen] = useState(false);
  const [taskMenuOpen, setTaskMenuOpen] = useState(false);
  const [leaderboardSearch, setLeaderboardSearch] = useState("");
  const [trackChoice, setTrackChoice] = useState<"public" | "classroom" | null>(searchParams.has("classroom") ? "classroom" : null);
  const [selectedClassroomId, setSelectedClassroomId] = useState(searchParams.get("classroom") ?? "");
  const [availableClassrooms, setAvailableClassrooms] = useState<Array<{ id: string; name: string }>>([]);
  const [leaderboardPage, setLeaderboardPage] = useState(1);
  const [scoringModalOpen, setScoringModalOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement>(null);
  const classroomId = classroom?.classroom?.id;
  const sourceCompetitionId = leaderboardCompetitionOption(activeCompetitionId)?.sourceId;
  const activeClassroomId = user?.is_admin ? selectedClassroomId : classroomId;
  const canShowClassroom = Boolean(user && activeClassroomId && (user.is_admin || classroom?.tracks.some((track) => track.id === sourceCompetitionId)));
  const activeTrack = canShowClassroom && trackChoice !== "public" ? "classroom" : "public";
  const scopeClassrooms = user?.is_admin
    ? availableClassrooms
    : canShowClassroom && classroom?.classroom ? [classroom.classroom] : [];

  const [loadError, setLoadError] = useState("");
  const [reloadVersion, setReloadVersion] = useState(0);
  const [pendingDownload, setPendingDownload] = useState("");
  const downloadRequest = useRef<AbortController | null>(null);
  useEffect(() => () => downloadRequest.current?.abort(), []);

  const download = async (row: CompetitionLeaderboardEntry) => {
    if (downloadRequest.current || !sourceCompetitionId || !row.source_run_ids?.length) return;
    const controller = new AbortController();
    downloadRequest.current = controller;
    setPendingDownload(row.source_run_ids.join(","));
    try {
      const result = await api.exportAdminLeaderboard({
        competition_id: sourceCompetitionId, task_id: activeTaskId, track: "all", funding_source: "all",
        classroom_id: activeTrack === "classroom" ? activeClassroomId : undefined,
        source_run_ids: row.source_run_ids,
      }, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(result.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "leaderboard-export.zip";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
      if (result.incomplete) message.warning(t("Some saved files are missing or damaged. See manifest.json."));
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setLeaderboard([]);
        onAccessDenied?.(error.status);
      } else {
        message.error(t(error instanceof Error ? error.message : "Download failed"));
        if (error instanceof ApiError && error.status === 409) setReloadVersion((value) => value + 1);
      }
    } finally {
      if (downloadRequest.current === controller) downloadRequest.current = null;
      if (!controller.signal.aborted) setPendingDownload("");
    }
  };

  useEffect(() => {
    if (!user?.is_admin) {
      setAvailableClassrooms([]);
      return;
    }
    let cancelled = false;
    api.listAllClassrooms().then((items) => {
      if (cancelled) return;
      setAvailableClassrooms(items.map(({ id, name }) => ({ id, name })));
      setSelectedClassroomId((current) => items.some((item) => item.id === current) ? current : classroomId || "");
    }).catch(() => { if (!cancelled) setAvailableClassrooms([]); });
    return () => { cancelled = true; };
  }, [user?.id, user?.is_admin, classroomId]);

  useEffect(() => {
    if (!user?.is_admin && classroomId && !selectedClassroomId) setSelectedClassroomId(classroomId);
  }, [user?.is_admin, classroomId, selectedClassroomId]);

  useEffect(() => {
    if (!activeCompetitionId) return;
    if (clearedTaskForCompetitionId.current !== activeCompetitionId) {
      clearedTaskForCompetitionId.current = activeCompetitionId;
      setActiveTaskId("all");
    }
    const sourceId = leaderboardCompetitionOption(activeCompetitionId)?.sourceId;
    if (!sourceId) {
      setCompetitionTasks([]);
      return;
    }
    let cancelled = false;
    api.getCompetitionLeaderboardTasks(sourceId)
      .then((items) => { if (!cancelled) setCompetitionTasks(items); })
      .catch(() => { if (!cancelled) setCompetitionTasks([]); });
    return () => { cancelled = true; };
  }, [activeCompetitionId]);

  useEffect(() => {
    if (!pendingLeaderboardScroll.current || loading) return;
    pendingLeaderboardScroll.current = false;
    leaderboardSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [loading]);

  // Keep the URL in step with the filters so the leaderboard stays linkable.
  useEffect(() => {
    if (!activeCompetitionId) return;
    const next = new URLSearchParams(searchParams);
    next.set("competition", activeCompetitionId);
    if (activeTaskId && activeTaskId !== "all") {
      next.set("task", activeTaskId);
    } else {
      next.delete("task");
    }
    next.delete("funding_source");
    if (activeTrack === "classroom" && activeClassroomId) next.set("classroom", activeClassroomId);
    else next.delete("classroom");
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
  }, [activeCompetitionId, activeTaskId, activeTrack, activeClassroomId, searchParams, setSearchParams]);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!filterRef.current?.contains(event.target as Node)) {
        setCompetitionMenuOpen(false);
        setTaskMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  useEffect(() => {
    const sourceId = leaderboardCompetitionOption(activeCompetitionId)?.sourceId;
    if (!activeCompetitionId || !sourceId) {
      setLeaderboard([]);
      setLeaderboardLoading(false);
      return;
    }

    let cancelled = false;
    setLeaderboardLoading(true);
    setLoadError("");
    withMinimumSkeletonDuration(api.getCompetitionLeaderboard(sourceId, activeTaskId, "all", activeTrack === "classroom" ? activeClassroomId : undefined, admin))
      .then((items) => { if (!cancelled) setLeaderboard(items); })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLeaderboard([]);
        setLoadError(error instanceof Error ? error.message : "Unable to load leaderboard");
        if (admin && error instanceof ApiError && (error.status === 401 || error.status === 403)) onAccessDenied?.(error.status);
      })
      .finally(() => { if (!cancelled) setLeaderboardLoading(false); });
    return () => { cancelled = true; };
  }, [activeCompetitionId, activeTaskId, activeTrack, activeClassroomId, admin, onAccessDenied, reloadVersion, user?.id, user?.is_admin]);

  useEffect(() => {
    setLeaderboardPage(1);
  }, [activeCompetitionId, activeTaskId, leaderboardSearch, activeTrack, activeClassroomId]);

  useEffect(() => {
    setLeaderboardSearch("");
  }, [activeCompetitionId, activeTaskId]);

  const selectedLeaderboardOption = leaderboardCompetitionOption(activeCompetitionId) ?? leaderboardCompetitionOptions[0];
  const trackLeaderboard = leaderboard;
  const filteredLeaderboard = useMemo(() => {
    const query = leaderboardSearch.trim().toLowerCase();
    if (!query) return trackLeaderboard;
    return trackLeaderboard.filter((row) => row.username.toLowerCase().includes(query));
  }, [leaderboardSearch, trackLeaderboard]);
  const currentLeader = trackLeaderboard[0];
  const leaderboardPageCount = Math.max(1, Math.ceil(filteredLeaderboard.length / leaderboardPageSize));
  useEffect(() => {
    setLeaderboardPage((page) => Math.min(page, leaderboardPageCount));
  }, [leaderboardPageCount]);
  const visibleLeaderboard = filteredLeaderboard.slice(
    (leaderboardPage - 1) * leaderboardPageSize,
    leaderboardPage * leaderboardPageSize,
  );
  const visibleLeaderboardPages = paginationItems(leaderboardPage, leaderboardPageCount);

  return (
        <section className="competition-leaderboard-section" ref={leaderboardSectionRef}>
          <div className="competition-section-heading competition-ranking-heading">
            <div>
              <div className="competition-leaderboard-title-row">
                <h2>Leaderboard</h2>
                {user ? (
                  <Dropdown
                    trigger={["click"]}
                    overlayClassName="competition-scope-menu"
                    menu={{
                      selectable: true,
                      selectedKeys: [activeTrack === "classroom" ? activeClassroomId ?? "public" : "public"],
                      items: [
                        { key: "public", label: t("Public (all)") },
                        ...scopeClassrooms.map((item) => ({ key: item.id, label: item.name })),
                      ],
                      onClick: ({ key }) => {
                        setTrackChoice(key === "public" ? "public" : "classroom");
                        if (key !== "public") setSelectedClassroomId(key);
                      },
                    }}
                  >
                    <button type="button" className="competition-scope-select" aria-label={t("Leaderboard scope")} data-i18n-skip>
                      <span>{activeTrack === "classroom"
                        ? scopeClassrooms.find((item) => item.id === activeClassroomId)?.name
                        : t("Public (all)")}</span>
                      <DownOutlined />
                    </button>
                  </Dropdown>
                ) : null}
                <button
                  type="button"
                  className="competition-scoring-help"
                  aria-label={t("View scoring rules")}
                  data-i18n-skip
                  onClick={() => setScoringModalOpen(true)}
                >
                  <QuestionCircleOutlined />
                </button>
              </div>
              <p className="competition-leaderboard-subtitle">
                See the latest results for the selected competition.
                <span className="competition-leaderboard-latest-note">Latest submission per team</span>
              </p>
            </div>
            <div className="competition-leader-summary">
              <TrophyFilled aria-hidden="true" />
              <span>Current leader</span>
              <strong className="competition-leaderboard-team-name" title={currentLeader?.username}>{currentLeader?.username ?? "—"}</strong>
              <span>{currentLeader?.score != null ? `${currentLeader.score.toFixed(2)} score` : "No data yet"}</span>
            </div>
          </div>

          {loadError ? <div className="admin-leaderboard-error" role="alert">{loadError} <Button onClick={() => setReloadVersion((value) => value + 1)}>{t("Retry")}</Button></div> : null}
          <div className="competition-leaderboard-toolbar" ref={filterRef}>
            <div className="competition-filter-group">
              <div className="competition-select-shell">
                <span>Competition</span>
                <button type="button" className="competition-select-button" aria-expanded={competitionMenuOpen} onClick={() => { setCompetitionMenuOpen((open) => !open); setTaskMenuOpen(false); }}>
                  {selectedLeaderboardOption.label}<DownOutlined aria-hidden="true" />
                </button>
                {competitionMenuOpen ? <div className="competition-select-menu" role="listbox">
                  {leaderboardCompetitionOptions.map((option) => <button type="button" role="option" aria-selected={option.id === activeCompetitionId} key={option.id} onClick={() => { setActiveCompetitionId(option.id); setCompetitionMenuOpen(false); }}>{option.label}</button>)}
                </div> : null}
              </div>
              <div className="competition-select-shell">
                <span>Task</span>
                <button type="button" className="competition-select-button" aria-expanded={taskMenuOpen} onClick={() => { setTaskMenuOpen((open) => !open); setCompetitionMenuOpen(false); }} disabled={!selectedLeaderboardOption.sourceId}>
                  {activeTaskId === "all" ? "All tasks" : competitionTasks.find((task) => task.id === activeTaskId)?.title ?? activeTaskId}<DownOutlined aria-hidden="true" />
                </button>
                {taskMenuOpen ? <div className="competition-select-menu" role="listbox">
                  <button type="button" role="option" aria-selected={activeTaskId === "all"} onClick={() => { setActiveTaskId("all"); setTaskMenuOpen(false); }}>All tasks</button>
                  {competitionTasks.map((task) => <button type="button" role="option" aria-selected={task.id === activeTaskId} key={task.id} onClick={() => { setActiveTaskId(task.id); setTaskMenuOpen(false); }}>{task.title}</button>)}
                </div> : null}
              </div>
            </div>
            <label className="competition-search-field competition-leaderboard-search">
              <SearchOutlined aria-hidden="true" />
              <span className="visually-hidden">Search users or teams</span>
              <input value={leaderboardSearch} onChange={(event) => setLeaderboardSearch(event.target.value)} placeholder="Search users or teams..." />
            </label>
          </div>

          <div className="competition-leaderboard competition-leaderboard-main">
            <div className="competition-table-wrap">
              <table className="competition-ranking-table" aria-busy={loading || leaderboardLoading}>
                <caption className="visually-hidden">{loading || leaderboardLoading ? "Loading leaderboard" : "Competition leaderboard"}</caption>
                <thead>
                  <tr>
                    <th>Rank</th><th>{t("Team")}</th>
                    <th>Score</th><th>Test pass rate</th><th>Cost (￥)</th><th>Submitted</th>{admin ? <th>{t("Actions")}</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {loading || leaderboardLoading ? leaderboardSkeletonRows.map((row) => (
                    <tr key={row} className="competition-leaderboard-skeleton-row" aria-hidden="true">
                      <td><span className="competition-skeleton competition-skeleton-rank" /></td><td><span className="competition-skeleton competition-skeleton-line skeleton-user" /></td><td><span className="competition-skeleton competition-skeleton-line skeleton-rate" /></td><td><span className="competition-skeleton competition-skeleton-line skeleton-efficiency" /></td><td><span className="competition-skeleton competition-skeleton-line skeleton-token" /></td><td><span className="competition-skeleton competition-skeleton-line skeleton-runtime" /></td>
                      {admin ? <td><span className="competition-skeleton competition-skeleton-line skeleton-user" /></td> : null}
                    </tr>
                  )) : filteredLeaderboard.length === 0 ? (
                    <tr className="competition-leaderboard-empty-row"><td colSpan={admin ? 7 : 6}><div className="competition-leaderboard-empty" role="status"><span className="competition-leaderboard-empty-icon" aria-hidden="true"><TrophyOutlined /></span><strong>{leaderboard.length > 0 ? "No leaderboard entries match these filters" : activeTaskId === "all" ? "No complete submissions yet" : "No submissions for this task yet"}</strong><span>{leaderboard.length > 0 ? "Try another search term." : activeTaskId === "all" ? "Complete the required tasks to appear here." : "Scored runs for this task will appear here."}</span></div></td></tr>
                  ) : visibleLeaderboard.map((row) => {
                    const rank = row.rank;
                    return (
                      <tr key={`${row.username}-${row.model_name ?? "model"}-${rank}`}>
                        <td>{rankDisplay(rank)}</td>
                        <td>
                          <div className="competition-leaderboard-team-cell">
                            <span className="competition-user-entry">
                              <strong className="competition-leaderboard-team-name" title={row.username}>{row.username}</strong>
                              {row.is_low_cost ? (
                                <span
                                  className="competition-low-cost-warning"
                                  title="Cost below ￥0.01."
                                  aria-label="Cost below one cent"
                                >
                                  <QuestionCircleOutlined />
                                </span>
                              ) : null}
                            </span>
                          </div>
                        </td>
                        <td>{row.score_available && row.score != null ? <span className="competition-score-cell" title={row.scoring_route ? `GitHub route: ${row.scoring_route === "stages" ? "progressive stages" : row.scoring_route}. Latest valid evaluations are used.` : undefined}><strong>{row.score.toFixed(2)}</strong>{row.scoring_route === "stages" ? <small> Stages</small> : null}</span> : <span className="competition-score-unavailable">Cost data unavailable</span>}</td>
                        <td>{row.pass_rate.toFixed(1)}%</td>
                        <td title={row.total_cost == null && row.score_available ? t("Cost unavailable; scored as infinite cost") : undefined} data-i18n-skip>{row.total_cost == null ? (row.score_available ? "∞" : "-") : `￥${row.total_cost.toFixed(4)}`}</td>
                        <td>{formatLeaderboardDate(row.submitted_at)}</td>
                        {admin ? <td><Button
                          icon={<DownloadOutlined />}
                          loading={pendingDownload === row.source_run_ids?.join(",")}
                          disabled={Boolean(pendingDownload) || !row.source_run_ids?.length}
                          title={!row.source_run_ids?.length ? t("No source runs available") : undefined}
                          data-i18n-skip
                          onClick={() => void download(row)}
                        >{t("Download")}</Button></td> : null}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {!loading && !leaderboardLoading && filteredLeaderboard.length > 0 ? (
              <footer className="competition-pagination">
                <span>Showing {(leaderboardPage - 1) * leaderboardPageSize + 1}–{Math.min(leaderboardPage * leaderboardPageSize, filteredLeaderboard.length)} of {filteredLeaderboard.length} results</span>
                <nav aria-label="Leaderboard pages">
                  <button type="button" aria-label="Previous page" disabled={leaderboardPage === 1} onClick={() => setLeaderboardPage((page) => Math.max(1, page - 1))}><LeftOutlined /></button>
                  {visibleLeaderboardPages.map((item) => typeof item === "number" ? <button key={item} type="button" className={item === leaderboardPage ? "active" : ""} aria-current={item === leaderboardPage ? "page" : undefined} onClick={() => setLeaderboardPage(item)}>{item}</button> : <span key={item}>…</span>)}
                  <button type="button" aria-label="Next page" disabled={leaderboardPage === leaderboardPageCount} onClick={() => setLeaderboardPage((page) => Math.min(leaderboardPageCount, page + 1))}><RightOutlined /></button>
                </nav>
              </footer>
            ) : null}
          </div>
          <Modal
            open={scoringModalOpen}
            onCancel={() => setScoringModalOpen(false)}
            footer={null}
            centered
            width={620}
            title={t("Leaderboard scoring")}
            className="competition-scoring-modal"
          >
            {language === "zh" ? (
              <div className="competition-scoring-copy">
                <p>排名同时考虑测试通过率和模型开销。设 <strong>p</strong> 为通过率百分数，<strong>b</strong> 为人民币开销，<strong>N</strong> 为测试总数。</p>
                <div className="competition-scoring-formula"><span>合理开销</span><strong>0.4 × N × p ÷ 100</strong></div>
                <div className="competition-scoring-formula"><span>最终得分</span><strong>S = p ÷ (b ÷ 合理开销)<sup>γ</sup></strong></div>
                <p>实际开销不高于合理开销时，γ = 0.1，获得低成本奖励；高于合理开销时，γ = 0.2，受到成本惩罚。开销最低按 ￥0.10 计算，开销比率限制在 0.01–100。</p>
                {activeCompetitionId === "hackathon" ? <p>黑客松按各任务最新有效评测计分。GitHub 取原题最新有效评测与三个阶段最新有效评测累计得分的较高者；阶段汇总通过数与开销，未完成阶段计零通过、零开销。总榜只计一次 GitHub，未评测的其他任务也计零通过。</p> : null}
                <ul><li>使用自己的 API Key 运行，网站按统一价格统计用量与费用。仅累计已记录的有效费用，缺失或异常部分跳过，没有记录时按零计算。</li><li>综合榜：同一份提交必须完成全部任务，汇总通过数、测试数和开销后计算一次得分。</li><li>单任务榜：取每位用户或团队在该任务上的最高得分运行。</li><li>依次按得分、通过率、较低开销、较早提交时间排名。</li></ul>
              </div>
            ) : (
              <div className="competition-scoring-copy">
                <p>Rankings balance test pass rate with model cost. Let <strong>p</strong> be the pass-rate percentage, <strong>b</strong> the cost in CNY, and <strong>N</strong> the total number of tests.</p>
                <div className="competition-scoring-formula"><span>Expected cost</span><strong>0.4 × N × p ÷ 100</strong></div>
                <div className="competition-scoring-formula"><span>Final score</span><strong>S = p ÷ (b ÷ expected cost)<sup>γ</sup></strong></div>
                <p>When actual cost is at or below the expected cost, γ = 0.1 rewards efficiency. Above it, γ = 0.2 applies a cost penalty. Cost has a ￥0.10 floor and the cost ratio is clamped to 0.01–100.</p>
                {activeCompetitionId === "hackathon" ? <p>Hackathon uses the latest valid evaluations. GitHub takes the higher score of the original task and the combined three-stage route. Stages aggregate passed tests and cost; missing stages contribute zero passes and zero cost. GitHub counts once overall, and other unevaluated tasks count as zero passes.</p> : null}
                <ul><li>Run with your own API key. Usage is priced at website rates. Valid recorded costs are summed, skipping missing or invalid values; no records means zero cost.</li><li>Overall leaderboard: one submission must complete every task. Passed tests, test totals, and costs are aggregated before scoring once.</li><li>Task leaderboard: each user or team contributes its highest-scoring run for that task.</li><li>Rows are ordered by score, pass rate, lower cost, then earlier submission time.</li></ul>
              </div>
            )}
          </Modal>
        </section>
  );
}
