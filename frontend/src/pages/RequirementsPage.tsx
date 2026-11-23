import {
  CalendarOutlined,
  CloudUploadOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  GithubOutlined,
  PlayCircleOutlined,
  RightOutlined,
  SearchOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import CompetitionLeaderboard from "../components/competition/CompetitionLeaderboard";
import { api } from "../lib/api";

import type { CompetitionSummary } from "../lib/types";

const workflowNodes = [
  { label: "Upload agent", copy: "Add your runnable agent package.", icon: <CloudUploadOutlined /> },
  { label: "Compile requirements", copy: "Turn the brief into runnable modules.", icon: <DeploymentUnitOutlined /> },
  { label: "Run mixed tasks", copy: "Evaluate against both task packs.", icon: <ExperimentOutlined /> },
  { label: "Inspect output", copy: "Review evidence and results.", icon: <PlayCircleOutlined /> },
];

const competitionSkeletonCards = Array.from({ length: 6 }, (_, index) => index);
const minimumSkeletonDurationMs = 600;
// The full catalog stays in this page so those entries can be placed elsewhere later.
const showFullCompetitionCatalog = false;
const benchmarkHighlightIds = ["first-competition", "arc-bench-lite", "arc-bench-web", "arc-bench-evolution"];
const hiddenCompetitionIds = new Set(["smoke", "smoke-evolution"]);

function displayCompetitionTitle(competition: { id: string; title: string }) {
  return competition.id === "first-competition" ? "Smoke Competition" : competition.title;
}

async function withMinimumSkeletonDuration<T>(request: Promise<T>): Promise<T> {
  const [result] = await Promise.all([
    request,
    new Promise<void>((resolve) => window.setTimeout(resolve, minimumSkeletonDurationMs)),
  ]);
  return result;
}

function formatCompetitionRange(start?: string | null, end?: string | null) {
  if (!start || !end) return "TBD";
  const startDate = new Date(`${start}T00:00:00+08:00`);
  const endDate = new Date(`${end}T00:00:00+08:00`);
  if (Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime())) return "TBD";
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai", month: "short",
    day: "numeric",
    year: "numeric",
  });
  return `${formatter.format(startDate)} - ${formatter.format(endDate)}`;
}

function statusLabel(status: string) {
  if (status === "open") return "OPEN";
  if (status === "upcoming") return "UPCOMING";
  if (status === "closed" || status === "ended") return "ENDED";
  return status.toUpperCase();
}

function featuredArt(competition: CompetitionSummary) {
  if (competition.id === "first-competition") {
    return { title: "Smoke Competition", detail: <>A small ticket-booking task.<br />Smoke test before the formal experiment.</> };
  }
  if (competition.id === "hackathon-evolution") {
    return { title: "Hackathon Evolution", detail: <>Invitation only.<br />Evolve the winning work.</> };
  }
  if (competition.id === "arc-bench-evolution") {
    return { title: "Evolution", detail: <>Incremental changes.<br />Existing behavior preserved.</> };
  }
  if (competition.type === "official") {
    return { title: "Official Hackathon", detail: <>Confirmed teams.<br />One official arena.</> };
  }
  return { title: "Web Application", detail: <>Real-world web applications.<br />Real capabilities.</> };
}

function FeaturedCompetitionBanner({ competition }: { competition: CompetitionSummary }) {
  const art = featuredArt(competition);
  const titleId = `featured-competition-title-${competition.id}`;
  const unavailable = competition.status === "upcoming";

  return (
    <section className="competition-featured" aria-labelledby={titleId}>
      <div className="competition-featured-art" aria-hidden="true">
        <span>ARC–Bench</span>
        <strong>{art.title}</strong>
        <small>{art.detail}</small>
      </div>
      <div className="competition-featured-copy">
        <div className="competition-featured-title-row">
          <h2 id={titleId}>{displayCompetitionTitle(competition)}</h2>
          <div className="competition-featured-title-actions">
            <span className={`competition-status-pill ${competition.status}`}>{statusLabel(competition.status)}</span>
            <a
              className="competition-featured-github"
              href="https://github.com/code-philia/arc-bench"
              target="_blank"
              rel="noreferrer"
              aria-label="Open ARC-Bench on GitHub"
              onClick={(event) => event.stopPropagation()}
            >
              <span>References:</span>
              <GithubOutlined aria-hidden="true" />
              <span>GitHub</span>
            </a>
          </div>
        </div>
        <p>{competition.summary}</p>
        <div className="competition-featured-meta">
          <span><CalendarOutlined /> {formatCompetitionRange(competition.starts_at, competition.ends_at)}</span>
          <span><UnorderedListOutlined /> {competition.task_count} tasks</span>
          <span><FileTextOutlined /> {competition.total_tests} tests</span>
        </div>
      </div>
      <Link
        className={`competition-featured-action${unavailable ? " unavailable" : ""}`}
        to={`/competitions/${competition.id}`}
        aria-disabled={unavailable}
        tabIndex={unavailable ? -1 : undefined}
        onClick={unavailable ? (event) => event.preventDefault() : undefined}
      >{unavailable ? "Not started" : "View competition"} {unavailable ? null : <RightOutlined />}</Link>
    </section>
  );
}

export default function RequirementsPage() {
  const [competitions, setCompetitions] = useState<CompetitionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [competitionSearch, setCompetitionSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "open" | "upcoming" | "finished">("all");
  useEffect(() => {
    withMinimumSkeletonDuration(api.listCompetitions()).then(setCompetitions)
      .catch(() => setCompetitions([])).finally(() => setLoading(false));
  }, []);
  const benchmarkHighlights = benchmarkHighlightIds
    .map((id) => competitions.find((item) => item.id === id))
    .filter((item): item is CompetitionSummary => Boolean(item));
  const mainCompetition = competitions.find((item) => item.id === "arc-bench-web");
  const filteredCompetitions = useMemo(() => {
    const query = competitionSearch.trim().toLowerCase();
    return competitions.filter((competition) => {
      const normalizedStatus = competition.status === "closed" || competition.status === "ended" ? "finished" : competition.status;
      const matchesStatus = statusFilter === "all" || normalizedStatus === statusFilter;
      const matchesSearch = !query || `${displayCompetitionTitle(competition)} ${competition.summary}`.toLowerCase().includes(query);
      if (hiddenCompetitionIds.has(competition.id)) return false;
      return matchesStatus && matchesSearch;
    }).sort((left, right) => Number(right.id === "arc-bench-web") - Number(left.id === "arc-bench-web"));
  }, [competitionSearch, competitions, statusFilter]);
  return (
    <div className="page competition-home-page">
      <div className="competition-container">
        <section className="competition-hero">
          <div className="competition-hero-copy">
            <h1>Competitions</h1>
            <p>Join realistic web application challenges, evaluate your agent, and compete with the community.</p>
          </div>
          <div className="competition-workflow-shell">
            <strong>How it works</strong>
            <div className="competition-workflow-diagram" aria-label="Agent competition workflow">
              {workflowNodes.map((node, index) => (
                <div key={node.label} className="competition-workflow-step">
                  <span className="competition-workflow-icon">{node.icon}</span>
                  <div>
                    <em>{String(index + 1).padStart(2, "0")}</em>
                    <strong>{node.label}</strong>
                    <small>{node.copy}</small>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="competition-bank">
          <div className="competition-section-heading competition-catalog-heading">
            <div>
              <h2>Benchmarks</h2>
            </div>
            {showFullCompetitionCatalog ? <div className="competition-catalog-tools">
              <label className="competition-search-field">
                <SearchOutlined aria-hidden="true" />
                <span className="visually-hidden">Search competitions</span>
                <input value={competitionSearch} onChange={(event) => setCompetitionSearch(event.target.value)} placeholder="Search competitions..." />
              </label>
              <div className="competition-status-filter" aria-label="Filter competitions by status">
                {(["all", "open", "upcoming", "finished"] as const).map((status) => (
                  <button key={status} type="button" className={statusFilter === status ? "active" : ""} onClick={() => setStatusFilter(status)}>{status[0].toUpperCase() + status.slice(1)}</button>
                ))}
              </div>
            </div> : null}
          </div>
          {mainCompetition ? (
            <div className="competition-benchmark-list">
              <FeaturedCompetitionBanner competition={mainCompetition} />
            </div>
          ) : null}
          <div className="competition-card-grid competition-practice-cards">
            {benchmarkHighlights.map((competition) => (
              <Link key={competition.id} to={`/competitions/${competition.id}`} className="competition-card">
                <div className="competition-card-topline">
                  <span className={`competition-status-pill ${competition.status}`}>{statusLabel(competition.status)}</span>
                </div>
                <div className="competition-card-title-row"><h3>{displayCompetitionTitle(competition)}</h3><RightOutlined aria-hidden="true" /></div>
                <p>{competition.summary}</p>
                <div className="competition-card-footer">
                  <span><UnorderedListOutlined /> {competition.task_count} tasks</span>
                  <span><FileTextOutlined /> {competition.total_tests} tests</span>
                </div>
              </Link>
            ))}
          </div>
          {showFullCompetitionCatalog ? (
            loading ? (
              <div className="competition-card-grid competition-card-grid--loading" role="status" aria-label="Loading competitions">
                {competitionSkeletonCards.map((card) => (
                  <div key={card} className="competition-card competition-card--skeleton" aria-hidden="true">
                    <span className="competition-skeleton competition-skeleton-pill" />
                    <span className="competition-skeleton competition-skeleton-line skeleton-card-title" />
                    <span className="competition-skeleton competition-skeleton-line skeleton-card-copy" />
                    <span className="competition-skeleton competition-skeleton-line skeleton-card-copy-short" />
                    <span className="competition-skeleton competition-skeleton-line skeleton-card-date" />
                  </div>
                ))}
              </div>
            ) : filteredCompetitions.length === 0 ? (
              <div className="competition-empty">No competitions match this search.</div>
            ) : (
              <div className="competition-card-grid">
                {filteredCompetitions.map((competition) => (
                  <Link
                    key={competition.id}
                    to={`/competitions/${competition.id}`}
                    className={`competition-card${competition.status === "upcoming" ? " unavailable" : ""}`}
                    aria-disabled={competition.status === "upcoming"}
                    tabIndex={competition.status === "upcoming" ? -1 : undefined}
                    onClick={competition.status === "upcoming" ? (event) => event.preventDefault() : undefined}
                  >
                    <div className="competition-card-topline">
                      <span className={`competition-status-pill ${competition.status}`}>{statusLabel(competition.status)}</span>
                      {competition.type === "official" ? <span className="competition-official-pill">Official · 正式赛</span> : null}
                    </div>
                    <div className="competition-card-title-row"><h3>{competition.title}</h3>{competition.status === "upcoming" ? null : <RightOutlined aria-hidden="true" />}</div>
                    <p>{competition.summary}</p>
                    <div className="competition-card-footer">
                      <span><CalendarOutlined /> {formatCompetitionRange(competition.starts_at, competition.ends_at)}</span>
                      <span><UnorderedListOutlined /> {competition.task_count} task{competition.task_count === 1 ? "" : "s"}</span>
                      <span><FileTextOutlined /> {competition.total_tests} tests</span>
                    </div>
                  </Link>
                ))}
              </div>
            )
          ) : null}
        </section>

        <CompetitionLeaderboard catalogLoading={loading} />
      </div>
    </div>
  );
}
