import { useState } from "react";
import { api } from "../../lib/api";
import { extractTestCases } from "../../lib/testSource";
import type { RequirementTestFile, SubmissionDetail } from "../../lib/types";

type TestResult = SubmissionDetail["tests"][number];
const isPassed = (test: TestResult) => test.passed ?? ["passed", "expected"].includes(test.status);

function TestDetails({ submission }: { submission: SubmissionDetail }) {
  const [filter, setFilter] = useState("all");
  const [files, setFiles] = useState<RequirementTestFile[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const loadSource = async () => {
    if (files || loading) return;
    setLoading(true);
    setError("");
    try {
      const catalog = submission.catalog === "competition" ? "competition" : submission.catalog === "benchmark" ? "benchmark" : "playground";
      const result = await api.getRequirementTests(submission.requirement_id, catalog);
      setFiles(result.files);
    } catch {
      setError("Unable to load test source.");
    } finally {
      setLoading(false);
    }
  };
  const tests = submission.tests.filter(test => filter === "all" || (filter === "passed" ? isPassed(test) : !isPassed(test)));
  return (
    <div className="result-cases">
      <div className="result-cases-filters" role="group" aria-label="Filter test results">
        {([['all', 'All'], ['passed', 'Passed'], ['failed', 'Not passed']] as const).map(([value, label]) => (
          <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>
        ))}
      </div>
      {tests.length === 0 && <p className="result-cases-note">No tests in this filter.</p>}
      {tests.map((test, index) => (
        <details className="result-case" key={`${test.file}:${test.name}:${index}`}>
          <summary>
            <span className={`result-case-status ${isPassed(test) ? 'is-passed' : 'is-failed'}`}>{isPassed(test) ? '✓' : '×'}</span>
            <span className="result-case-title">{test.name}</span>
            <span className="result-case-duration">{(test.duration_ms / 1000).toFixed(1)}s</span>
          </summary>
          <div className="result-case-body">
            <div className="result-case-meta">{isPassed(test) ? 'Passed' : `Not passed · ${test.status}`}{test.file ? ` · ${test.file}` : ''}</div>
            {test.error && <pre className="result-case-error">{test.error.replace(/\u001b\[[0-9;]*m/g, '')}</pre>}
            <details className="result-case-source" onToggle={event => { if (event.currentTarget.open) void loadSource(); }}>
              <summary>Test source</summary>
              {loading && <p className="result-cases-note">Loading…</p>}
              {error && <p className="result-cases-note">{error} <button type="button" onClick={() => void loadSource()}>Retry</button></p>}
              {files && <TestSource files={files} test={test} />}
            </details>
          </div>
        </details>
      ))}
    </div>
  );
}

function TestSource({ files, test }: { files: RequirementTestFile[]; test: TestResult }) {
  const normalized = (test.file || '').replace(/\\/g, '/');
  const matched = files.filter(file => normalized === file.path || normalized.endsWith(`/${file.path}`));
  const candidates = (matched.length ? matched : normalized ? [] : files)
    .flatMap(file => extractTestCases(file.content).filter(item => item.name === test.name));
  if (candidates.length !== 1) return <p className="result-cases-note">Source for this test is unavailable in the current task version.</p>;
  return <pre className="result-case-code"><code>{candidates[0].code}</code></pre>;
}

export default function SubmissionResultCard({ submission }: { submission: SubmissionDetail }) {
  const total = submission.passed_count + submission.failed_count;
  const score = total ? (submission.passed_count / total) * 100 : 0;

  if (total === 0) {
    return (
      <div className="results-empty results-empty--quiet">
        <div>
          <div className="results-empty-title">
            {submission.status === "PENDING" ? "Tests are queued" : submission.status === "RUNNING" ? "Tests are not running yet" : "No test results"}
          </div>
          <div className="results-empty-copy">
            {submission.status === "PENDING" || submission.status === "RUNNING"
              ? "Results will appear here when the runner reaches the test stage."
              : "This run finished without a test result to display."}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="results-panel">
      <div className="results-summary">
        <div className="results-summary-count">
          <div className="results-summary-label">TEST PASS RATE</div>
          <div className="results-count">
            <span className="pass-num">{submission.passed_count}</span>
            <span className="total-num">/{total}</span>
          </div>
        </div>
        <div className="results-summary-meter">
          <div className="results-summary-meter-head">
            <span>Pass rate</span>
            <strong>{score.toFixed(1)}%</strong>
          </div>
          <div className="results-bar" aria-hidden="true">
            <div className="results-bar-fill" style={{ width: `${Math.min(100, Math.max(0, score))}%` }} />
          </div>
        </div>
      </div>
      {submission.tests.length > 0 && <TestDetails key={`${submission.id}:${submission.requirement_id}`} submission={submission} />}
    </div>
  );
}
