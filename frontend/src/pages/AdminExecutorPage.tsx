import { ReloadOutlined } from "@ant-design/icons";
import { message } from "antd";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { api, ApiError } from "../lib/api";
import type { AdminExecutor, AdminExecutorDetail } from "../lib/api";

const TOKEN_KEY = "arcbench-admin-api-token";

type ExecutorAction = "pause" | "resume" | "shutdown";

function valueText(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function taskLabel(task: Record<string, unknown>) {
  return valueText(task.run_id ?? task.submission_id ?? task.task_id ?? task.id ?? "Task");
}

export default function AdminExecutorPage() {
  const { user, isLoading: authLoading } = useAuth();
  const [tokenInput, setTokenInput] = useState(() => window.sessionStorage.getItem(TOKEN_KEY) ?? "");
  const [token, setToken] = useState(() => window.sessionStorage.getItem(TOKEN_KEY) ?? "");
  const [executors, setExecutors] = useState<AdminExecutor[]>([]);
  const [details, setDetails] = useState<Record<string, AdminExecutorDetail>>({});
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState("");
  const [pendingControl, setPendingControl] = useState("");

  const loadExecutors = useCallback(async (adminToken: string) => {
    if (!adminToken) {
      return;
    }
    setLoading(true);
    setAuthError("");
    try {
      const payload = await api.listAdminExecutors(adminToken);
      setExecutors(payload.executors);
      const entries = await Promise.all(
        payload.executors.map(async (executor) => [
          executor.executor_id,
          await api.getAdminExecutor(executor.executor_id, adminToken),
        ] as const),
      );
      setDetails(Object.fromEntries(entries));
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 503)) {
        window.sessionStorage.removeItem(TOKEN_KEY);
        setToken("");
        setExecutors([]);
        setDetails({});
        setAuthError(error.message);
      } else {
        message.error((error as Error).message);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user && token) {
      void loadExecutors(token);
    }
  }, [loadExecutors, token, user]);

  const unlock = (event: React.FormEvent) => {
    event.preventDefault();
    const nextToken = tokenInput.trim();
    if (!nextToken) {
      setAuthError("Enter the configured administrator API token.");
      return;
    }
    window.sessionStorage.setItem(TOKEN_KEY, nextToken);
    setToken(nextToken);
  };

  const controlExecutor = async (executorId: string, action: ExecutorAction) => {
    if (action === "shutdown" && !window.confirm(
      `Shut down ${executorId}? The executor will stop accepting work and shut down after running tasks finish or the graceful timeout expires.`,
    )) {
      return;
    }
    const key = `${executorId}:${action}`;
    setPendingControl(key);
    try {
      await api.controlAdminExecutor(executorId, action, token);
      message.success(`${action} command sent to ${executorId}.`);
      await loadExecutors(token);
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setPendingControl("");
    }
  };

  if (authLoading) {
    return <div className="page centered"><div className="empty-state">Checking authentication...</div></div>;
  }

  if (!user) {
    return (
      <div className="page centered">
        <div className="admin-access-card">
          <span className="admin-kicker">Restricted operations</span>
          <h1>Authentication required</h1>
          <p>Sign in before opening the ArcBench executor console.</p>
          <Link className="admin-primary-action" to="/login">Go to login</Link>
        </div>
      </div>
    );
  }

  if (!token) {
    return (
      <div className="page centered">
        <form className="admin-access-card" onSubmit={unlock}>
          <span className="admin-kicker">Restricted operations</span>
          <h1>Executor console</h1>
          <p>Enter the server's <code>ARCBENCH_ADMIN_API_TOKEN</code>. It is retained only for this browser tab.</p>
          <label htmlFor="admin-token">Administrator API token</label>
          <input
            id="admin-token"
            className="admin-token-input"
            type="password"
            autoComplete="off"
            value={tokenInput}
            onChange={(event) => setTokenInput(event.target.value)}
          />
          {authError ? <div className="admin-error">{authError}</div> : null}
          <button className="admin-primary-action" type="submit">Open console</button>
        </form>
      </div>
    );
  }

  return (
    <div className="page admin-executor-page">
      <header className="admin-executor-header">
        <div>
          <span className="admin-kicker">ArcBench operations</span>
          <h1>Executor control</h1>
          <p>{executors.length} registered executor{executors.length === 1 ? "" : "s"}. Data is read directly from Redis.</p>
        </div>
        <button className="admin-refresh" type="button" disabled={loading} onClick={() => void loadExecutors(token)}>
          <ReloadOutlined spin={loading} /> Refresh
        </button>
      </header>

      {executors.length === 0 && !loading ? (
        <div className="admin-empty">No executors are currently registered.</div>
      ) : (
        <div className="admin-executor-grid">
          {executors.map((executor) => {
            const detail = details[executor.executor_id] ?? executor;
            const status = valueText(detail.status).toLowerCase();
            const tasks = detail.tasks ?? [];
            const control = detail.control ?? {};
            return (
              <article className="admin-executor-card" key={executor.executor_id}>
                <div className="admin-executor-card-head">
                  <div>
                    <span className={`admin-status admin-status-${status}`}>{status}</span>
                    <h2>{executor.executor_id}</h2>
                  </div>
                  <span className="admin-age">updated {valueText(detail.age_seconds)}s ago</span>
                </div>
                <dl className="admin-executor-facts">
                  <div><dt>Accepting</dt><dd>{valueText(detail.accepting_tasks)}</dd></div>
                  <div><dt>Running</dt><dd>{valueText(detail.running_tasks ?? tasks.length)}</dd></div>
                  <div><dt>Last command</dt><dd>{valueText(control.action)}</dd></div>
                  <div><dt>Acknowledged</dt><dd>{valueText(control.acknowledged_action)}</dd></div>
                </dl>
                <div className="admin-task-list">
                  <strong>Active tasks</strong>
                  {tasks.length ? tasks.map((task, index) => (
                    <div className="admin-task-row" key={valueText(task.task_id ?? task.id ?? index)}>
                      <code>{taskLabel(task)}</code>
                      <span>{valueText(task.status ?? task.state)}</span>
                    </div>
                  )) : <span className="admin-no-tasks">No active tasks</span>}
                </div>
                <div className="admin-executor-actions">
                  <button type="button" disabled={Boolean(pendingControl)} onClick={() => void controlExecutor(executor.executor_id, "pause")}>Pause</button>
                  <button type="button" disabled={Boolean(pendingControl)} onClick={() => void controlExecutor(executor.executor_id, "resume")}>Resume</button>
                  <button className="danger" type="button" disabled={Boolean(pendingControl)} onClick={() => void controlExecutor(executor.executor_id, "shutdown")}>Shutdown</button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
