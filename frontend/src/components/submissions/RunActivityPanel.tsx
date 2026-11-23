import type { SubmissionLogs } from "../../lib/types";

type RunActivityPanelProps = {
  logs: SubmissionLogs | null;
  refreshing: boolean;
  lastRefreshedAt: string | null;
  onRefresh: () => void;
  compact?: boolean;
};

/** A deliberately plain, shared view of process output. */
export default function RunActivityPanel({
  logs,
  refreshing,
  lastRefreshedAt,
  onRefresh,
  compact = false,
}: RunActivityPanelProps) {
  const output = logs?.stdout ?? "";
  const outputLineCount = output ? output.split(/\r?\n/).length : 0;
  return (
    <div className={`${compact ? "stdio-view" : "space-y-5 p-5"} run-activity-panel`}>
      <div className="run-output-toolbar">
        <div className="run-output-stream-selector">
          <strong>Stdout</strong>
          <span>{outputLineCount > 0 ? `${outputLineCount} lines` : "empty"}</span>
        </div>
        <button
          type="button"
          className="btn-outline"
          disabled={refreshing}
          onClick={onRefresh}
        >
          {refreshing ? "Refreshing…" : "Refresh logs"}
        </button>
      </div>
      <div className="run-output-section">
        <pre className={`${compact ? "stdio-code-view" : "log-panel"} run-output-console run-output-console--stdout ${output ? "has-output" : "is-empty"}`}>
          {output || "No stdout output."}
        </pre>
        {lastRefreshedAt ? <div className="run-output-refreshed">Last refreshed at {lastRefreshedAt}</div> : null}
      </div>
    </div>
  );
}
