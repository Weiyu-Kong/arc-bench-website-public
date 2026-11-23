import { useMemo, useState } from "react";
import { CalendarOutlined, GithubOutlined, LeftOutlined, RightOutlined, StarFilled, TrophyFilled, UnorderedListOutlined } from "@ant-design/icons";
import { Link, Navigate, useParams } from "react-router-dom";

import { useI18n } from "../i18n/I18nContext";
import { historyEventById, type HistoryStanding, type HistoryTrack } from "../lib/historyEvents";

const standingsPageSize = 20;

function rankDisplay(rank: number) {
  if (rank > 3) return <span className="competition-rank-plain">{rank}</span>;
  return (
    <span className={`competition-rank-badge rank-${rank}`}>
      <span>{rank}</span>
      {rank === 1 ? <StarFilled /> : <TrophyFilled />}
    </span>
  );
}

function repoLabel(repo: string) {
  const [owner, name] = repo.split("/");
  return name ? `${owner}/${name}` : repo;
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

function TrackBrief({ track, zh }: { track: HistoryTrack; zh: boolean }) {
  const kindLabel = track.kind === "final"
    ? (zh ? "决赛" : "Final")
    : (zh ? "初赛" : "Preliminary");

  return (
    <section className="competition-featured history-track-brief" aria-labelledby={`history-track-${track.id}`}>
      <div className="competition-featured-art" aria-hidden="true">
        <span>ARC–Bench</span>
        <strong>{track.artTitle}</strong>
        <small>{track.artDetail}</small>
      </div>
      <div className="competition-featured-copy">
        <div className="competition-featured-title-row">
          <h2 id={`history-track-${track.id}`}>{track.title}</h2>
          <div className="competition-featured-title-actions">
            <span className="competition-status-pill ended">{kindLabel}</span>
            <span className="competition-status-pill ended">ENDED</span>
          </div>
        </div>
        <p>{track.summary}</p>
        <div className="competition-featured-meta">
          <span><CalendarOutlined /> {track.when}</span>
          <span><UnorderedListOutlined /> {track.meta}</span>
        </div>
      </div>
    </section>
  );
}

function StandingsTable({ rows, zh, showGithub }: { rows: HistoryStanding[]; zh: boolean; showGithub: boolean }) {
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(rows.length / standingsPageSize));
  const currentPage = Math.min(page, pageCount);
  const visibleRows = useMemo(
    () => rows.slice((currentPage - 1) * standingsPageSize, currentPage * standingsPageSize),
    [currentPage, rows],
  );
  const pages = paginationItems(currentPage, pageCount);
  const from = rows.length === 0 ? 0 : (currentPage - 1) * standingsPageSize + 1;
  const to = Math.min(currentPage * standingsPageSize, rows.length);

  return (
    <div className="competition-leaderboard competition-leaderboard-main">
      <div className="competition-table-wrap">
        <table className={`competition-ranking-table history-static-ranking${showGithub ? "" : " history-static-ranking-no-github"}`}>
          <thead>
            <tr>
              <th>Rank</th>
              <th>{zh ? "队伍" : "Team"}</th>
              <th>Score</th>
              <th>Test pass rate</th>
              <th>Cost (￥)</th>
              {showGithub ? <th>GitHub</th> : null}
            </tr>
          </thead>
          <tbody data-i18n-skip="true">
            {visibleRows.map((row) => (
              <tr key={`${row.rank}-${row.team}`}>
                <td>{rankDisplay(row.rank)}</td>
                <td>
                  <strong className="competition-leaderboard-team-name" title={row.team}>{row.team}</strong>
                </td>
                <td><span className="competition-score-cell"><strong>{row.score.toFixed(2)}</strong></span></td>
                <td>{row.passRate.toFixed(1)}%</td>
                <td>￥{row.cost.toFixed(4)}</td>
                {showGithub ? (
                  <td>
                    {row.repo ? (
                      <a href={`https://github.com/${row.repo}`} target="_blank" rel="noreferrer" className="history-github-link">
                        <GithubOutlined aria-hidden="true" />
                        {repoLabel(row.repo)}
                      </a>
                    ) : "—"}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > standingsPageSize ? (
        <footer className="competition-pagination">
          <span>Showing {from}–{to} of {rows.length} results</span>
          <nav aria-label="Standings pages">
            <button type="button" aria-label="Previous page" disabled={currentPage === 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>
              <LeftOutlined />
            </button>
            {pages.map((item) => typeof item === "number" ? (
              <button key={item} type="button" className={item === currentPage ? "active" : ""} aria-current={item === currentPage ? "page" : undefined} onClick={() => setPage(item)}>
                {item}
              </button>
            ) : (
              <span key={item}>…</span>
            ))}
            <button type="button" aria-label="Next page" disabled={currentPage === pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>
              <RightOutlined />
            </button>
          </nav>
        </footer>
      ) : null}
    </div>
  );
}

export default function HistoryEventPage() {
  const { eventId = "" } = useParams();
  const { language } = useI18n();
  const zh = language === "zh";
  const event = historyEventById(eventId);

  if (!event) return <Navigate to="/history" replace />;

  return (
    <div className="page competition-home-page history-page">
      <div className="competition-container">
        <div className="history-back-row">
          <Link to="/history" className="history-back-link">
            <LeftOutlined aria-hidden="true" />
            Historical Events
          </Link>
        </div>

        <section className="history-event">
          <div className="history-event-copy">
            <h1>{zh ? event.titleZh : event.title}</h1>
            {(zh ? event.introZh : event.intro).map((paragraph) => (
              <p key={paragraph}>{paragraph}</p>
            ))}
          </div>

          <div className="competition-benchmark-list">
            {event.tracks.map((track) => (
              <TrackBrief key={track.id} track={track} zh={zh} />
            ))}
          </div>

          <section className="competition-leaderboard-section history-event-leaderboard">
            <div className="competition-section-heading">
              <div>
                <h2>{zh ? "初赛成绩排行" : "Preliminary standings"}</h2>
                <p>
                  {zh
                    ? "初赛公开成绩排名。"
                    : "Public ranking from the preliminary round."}
                </p>
              </div>
            </div>
            <StandingsTable rows={event.preliminaryStandings} zh={zh} showGithub={false} />
          </section>

          <section className="competition-leaderboard-section history-event-leaderboard">
            <div className="competition-section-heading">
              <div>
                <h2>{zh ? "决赛成绩排行" : "Final standings"}</h2>
                <p>
                  {zh
                    ? "20 支决赛队伍的官方名次。仓库链接由队伍提供。"
                    : "Official order of the 20 finalist teams. Repository links were provided by the teams."}
                </p>
              </div>
            </div>
            <StandingsTable rows={event.finalStandings} zh={zh} showGithub />
          </section>
        </section>
      </div>
    </div>
  );
}
