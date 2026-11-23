import { formatBeijingTime, parseUtcDate } from "../lib/time";
import { ClockCircleOutlined, LoadingOutlined, StopOutlined } from "@ant-design/icons";
import { message } from "antd";
import { useCallback, useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { api } from "../lib/api";
import type { SubmissionSummary } from "../lib/types";

function formatTime(value: string | null) {
  if (!value) return "Waiting for an executor";
  const timestamp = parseUtcDate(value);
  return Number.isNaN(timestamp.getTime()) ? "Time unavailable" : formatBeijingTime(timestamp);
}

function runLabel(run: SubmissionSummary) {
  if (run.catalog === "competition") {
    return `${run.competition_id || "Competition"} · ${run.requirement_id}`;
  }
  return run.requirement_id;
}

export default function ActiveRunsPage() {
  const { user, isLoading: authLoading } = useAuth();
  const [runs, setRuns] = useState<SubmissionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [stoppingId, setStoppingId] = useState<string | null>(null);

  const refresh = useCallback(async (showLoading = false) => {
    if (!user) return;
    if (showLoading) setLoading(true);
    try {
      setRuns(await api.listActiveRuns());
    } catch (error) {
      if (showLoading) message.error((error as Error).message);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (!user) return;
    void refresh(true);
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [refresh, user]);

  const stopRun = async (run: SubmissionSummary) => {
    setStoppingId(run.id);
    try {
      await api.cancelSubmission(run.id);
      setRuns((current) => current.filter((item) => item.id !== run.id));
      message.success("Run stopped. Its executor container is being removed.");
    } catch (error) {
      message.error((error as Error).message);
      await refresh();
    } finally {
      setStoppingId(null);
    }
  };

  if (authLoading) return <div className="page centered"><div className="loading-state">Loading active runs...</div></div>;
  if (!user) return <Navigate to="/login" state={{ from: "/running" }} replace />;

  return (
    <div className="page active-runs-page">
      <header className="active-runs-header">
        <div>
          <span className="eyebrow">Execution control</span>
          <h1>Running tasks</h1>
          <p>View and stop your queued or running executor tasks.</p>
        </div>
        <div className="active-runs-capacity" aria-label={`${runs.length} active runs`}>
          <strong>{runs.length}</strong>
          <span>active</span>
        </div>
      </header>

      {loading ? <div className="loading-state"><LoadingOutlined /> Loading active runs...</div>
        : runs.length === 0 ? <section className="active-runs-empty"><ClockCircleOutlined /><h2>No runs are currently active.</h2><p>Queued and running tasks will appear here automatically.</p></section>
          : <section className="active-runs-list" aria-live="polite">
            {runs.map((run) => <article className="active-run-card" key={run.id}>
              <div className="active-run-main">
                <div className="active-run-title-row">
                  <span className={`active-run-status active-run-status--${run.status.toLowerCase()}`}>{run.status.replaceAll("_", " ")}</span>
                  <span className="active-run-runtime">{run.runtime}</span>
                </div>
                <h2><Link to={`/runs/${run.id}`}>{run.display_name || run.id}</Link></h2>
                <p>{runLabel(run)}</p>
                <div className="active-run-meta">
                  <span>Run {run.id}</span>
                  <span>Started {formatTime(run.started_at)}</span>
                  {run.model_name ? <span>Model {run.model_name}</span> : null}
                </div>
              </div>
              <div className="active-run-actions">
                <Link className="btn-outline" to={`/runs/${run.id}`}>Open</Link>
                <button className="active-run-stop" type="button" disabled={stoppingId !== null} onClick={() => void stopRun(run)}>
                  {stoppingId === run.id ? <LoadingOutlined /> : <StopOutlined />}
                  {stoppingId === run.id ? "Stopping..." : "Stop"}
                </button>
              </div>
            </article>)}
          </section>}
    </div>
  );
}
