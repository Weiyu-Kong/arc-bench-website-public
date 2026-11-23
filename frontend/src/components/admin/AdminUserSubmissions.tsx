import { Button, Drawer, Pagination } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "../../lib/api";
import type { AdminHistoryPage, AdminRunEntry, AdminSubmissionEntry, AdminUserEntry } from "../../lib/api";
import { useI18n } from "../../i18n/I18nContext";
import { formatBeijingTime, parseUtcDate } from "../../lib/time";

function date(value: string | null) {
  return value ? formatBeijingTime(parseUtcDate(value)) : "—";
}

function useHistoryPage<T>(load: (signal: AbortSignal) => Promise<AdminHistoryPage<T>>, onAccessDenied: (status: number) => void) {
  const [data, setData] = useState<AdminHistoryPage<T> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    setLoading(true);
    load(controller.signal).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) onAccessDenied(reason.status);
      else setError(reason instanceof Error ? reason.message : "Unable to load submission history.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [load, onAccessDenied, version]);
  return { data, error, loading, retry: () => setVersion((value) => value + 1) };
}

type AccessProps = { onAccessDenied: (status: number) => void };

function SubmissionRuns({ userId, submissionId, onAccessDenied, pendingDownload, onDownload }: AccessProps & {
  userId: string; submissionId: string; pendingDownload: string;
  onDownload: (submissionId: string, runId?: string) => void;
}) {
  const { t } = useI18n();
  const [page, setPage] = useState(1);
  const load = useCallback((signal: AbortSignal) => api.listAdminSubmissionRuns(userId, submissionId, page, signal), [userId, submissionId, page]);
  const { data, error, loading, retry } = useHistoryPage<AdminRunEntry>(load, onAccessDenied);
  return (
    <section className="admin-submission-runs" aria-label={t("Run records")}>
      {error ? <div className="admin-leaderboard-error" role="alert">{t(error)} <Button onClick={retry}>{t("Retry")}</Button></div> : null}
      <div className="competition-table-wrap">
        <table className="admin-history-runs-table" aria-busy={loading}>
          <thead><tr><th>{t("Run ID")}</th><th>{t("Task")}</th><th>{t("Status")}</th><th>{t("Score")}</th><th>{t("Test pass rate")}</th><th>{t("Cost")}</th><th>{t("Created")}</th><th>{t("Finished")}</th><th>{t("Actions")}</th></tr></thead>
          <tbody>
            {loading ? <tr><td colSpan={9} role="status">{t("Loading runs…")}</td></tr>
              : error ? <tr><td colSpan={9}>{t("Unable to load run records.")}</td></tr>
              : !data?.items.length ? <tr><td colSpan={9}>{t("No run records.")}</td></tr>
              : data.items.map((run) => <tr key={run.id}>
                <td title={run.id}><code>{run.id}</code></td><td>{run.requirement_id}</td><td>{t(run.status)}</td>
                <td>{run.score?.toFixed(2) ?? "—"}</td><td>{run.test_pass_rate == null ? "—" : `${run.test_pass_rate.toFixed(1)}%`}</td>
                <td>{run.token_cost_usd == null ? "—" : `${run.token_cost_currency ?? ""} ${run.token_cost_usd.toFixed(4)}`}</td>
                <td>{date(run.created_at)}</td><td>{date(run.finished_at)}</td>
                <td><Button icon={<DownloadOutlined />} title={t("Download run artifacts")}
                  disabled={Boolean(pendingDownload)} loading={pendingDownload === `${submissionId}:${run.id}`}
                  onClick={() => onDownload(submissionId, run.id)}>{t("Download")}</Button></td>
              </tr>)}
          </tbody>
        </table>
      </div>
      {data && data.total > 20 ? <Pagination current={page} total={data.total} pageSize={20} showSizeChanger={false} onChange={setPage} /> : null}
    </section>
  );
}

export default function AdminUserSubmissions({ user, onClose, onAccessDenied }: AccessProps & { user: AdminUserEntry; onClose: () => void }) {
  const { t } = useI18n();
  const [query, setQuery] = useState({ page: 1, competitionId: "" });
  const [competitions, setCompetitions] = useState<Array<{ id: string; title: string }>>([]);
  const [competitionError, setCompetitionError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pendingDownload, setPendingDownload] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const downloadRequest = useRef<AbortController | null>(null);
  useEffect(() => () => downloadRequest.current?.abort(), []);
  const load = useCallback((signal: AbortSignal) => api.listAdminUserSubmissions(user.id, query.page, query.competitionId, signal), [user.id, query]);
  const { data, error, loading, retry } = useHistoryPage<AdminSubmissionEntry>(load, onAccessDenied);
  useEffect(() => {
    const controller = new AbortController();
    api.listAdminUserCompetitions(user.id, controller.signal).then((items) => {
      if (!controller.signal.aborted) setCompetitions(items);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) onAccessDenied(reason.status);
      else setCompetitionError(reason instanceof Error ? reason.message : "Unable to load competitions.");
    });
    return () => controller.abort();
  }, [user.id, onAccessDenied]);

  const download = async (submissionId: string, runId?: string) => {
    if (downloadRequest.current) return;
    const controller = new AbortController();
    downloadRequest.current = controller;
    setPendingDownload(`${submissionId}:${runId ?? "agent"}`);
    setDownloadError("");
    try {
      const result = await api.downloadAdminUserFile(user.id, submissionId, runId, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(result.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = result.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (reason) {
      if (controller.signal.aborted) return;
      if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) onAccessDenied(reason.status);
      else setDownloadError(reason instanceof Error ? reason.message : "Download failed");
    } finally {
      if (downloadRequest.current === controller) downloadRequest.current = null;
      if (!controller.signal.aborted) setPendingDownload("");
    }
  };

  return (
    <Drawer open onClose={onClose} width="min(1200px, 100vw)" title={<span data-i18n-skip>{t("Submissions")} · {user.username}</span>}>
      <div className="admin-user-submissions" data-i18n-skip>
        <p>{user.email}</p>
        <label className="admin-users-filter">
          <span>{t("Competition")}</span>
          <select value={query.competitionId} onChange={(event) => {
            setExpanded(null);
            setQuery({ competitionId: event.target.value, page: 1 });
          }}>
            <option value="">{t("All Competitions")}</option>
            {competitions.map((item) => <option key={item.id} value={item.id}>{t(item.title)}</option>)}
          </select>
        </label>
        {competitionError ? <p role="alert">{t(competitionError)}</p> : null}
        {downloadError ? <p role="alert">{t(downloadError)}</p> : null}
        {error ? <div className="admin-leaderboard-error" role="alert">{t(error)} <Button onClick={retry}>{t("Retry")}</Button></div> : null}
        {loading ? <p role="status">{t("Loading submissions…")}</p> : null}
        {!loading && !error && !data?.items.length ? <p>{t("No submissions yet.")}</p> : null}
        {data?.items.map((submission) => <article className="admin-submission-card" key={submission.id}>
          <header>
            <div><strong>{submission.display_name || submission.original_filename}</strong><p>{t(competitions.find((item) => item.id === submission.competition_id)?.title ?? submission.competition_id ?? "Competition")}</p></div>
            <Button icon={<DownloadOutlined />} title={t("Download submitted agent")}
              loading={pendingDownload === `${submission.id}:agent`} disabled={Boolean(pendingDownload)}
              onClick={() => void download(submission.id)}>{t("Download")}</Button>
          </header>
          <dl className="admin-submission-details">
            <div><dt>{t("Submission ID")}</dt><dd>{submission.id}</dd></div>
            <div><dt>{t("Original filename")}</dt><dd>{submission.original_filename}</dd></div>
            <div><dt>{t("Model")}</dt><dd>{submission.model_name ?? "—"}</dd></div>
            <div><dt>{t("Agent runtime")}</dt><dd>{submission.runtime}</dd></div>
            <div><dt>{t("Created")}</dt><dd>{date(submission.created_at)}</dd></div>
          </dl>
          {submission.deprecated_at ? <p>{t("Deprecated submission")}</p> : null}
          <Button aria-expanded={expanded === submission.id} disabled={!submission.run_count}
            onClick={() => setExpanded((current) => current === submission.id ? null : submission.id)}>
            {t(expanded === submission.id ? "Hide runs" : "View runs")} ({submission.run_count})
          </Button>
          {expanded === submission.id ? <SubmissionRuns key={submission.id} userId={user.id} submissionId={submission.id}
            onAccessDenied={onAccessDenied} pendingDownload={pendingDownload} onDownload={(sid, rid) => void download(sid, rid)} /> : null}
        </article>)}
        {data ? <Pagination current={query.page} total={data.total} pageSize={20} showSizeChanger={false}
          onChange={(page) => { setExpanded(null); setQuery((current) => ({ ...current, page })); }} /> : null}
      </div>
    </Drawer>
  );
}
