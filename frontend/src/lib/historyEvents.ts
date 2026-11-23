import historyFinalStandings from "./historyFinalStandings.json";
import historyPrelimStandings from "./historyPrelimStandings.json";

export type HistoryTrack = {
  id: "hackathon" | "hackathon-evolution";
  kind: "preliminary" | "final";
  title: string;
  artTitle: string;
  artDetail: string;
  summary: string;
  when: string;
  meta: string;
};

export type HistoryStanding = {
  rank: number;
  team: string;
  repo?: string;
  score: number;
  passRate: number;
  cost: number;
};

export type HistoryEvent = {
  id: string;
  title: string;
  titleZh: string;
  eyebrow: string;
  when: string;
  blurb: string;
  blurbZh: string;
  intro: string[];
  introZh: string[];
  tracks: HistoryTrack[];
  finalStandings: HistoryStanding[];
  preliminaryStandings: HistoryStanding[];
};

export const historyEvents: HistoryEvent[] = [
  {
    id: "agentic-software-factory",
    title: "2026 Agentic Software Factory Hackathon",
    titleZh: "2026 国际智能体软件工厂黑客松",
    eyebrow: "Finished event",
    when: "Sep 2026 – Oct 2026",
    blurb: "An open agent software-factory contest with a GitHub + Spreadsheets preliminary round and a harder enterprise finals brief.",
    blurbZh: "开放的智能体软件工厂赛事：初赛复刻 GitHub 与电子表格，决赛面向更复杂的企业需求。",
    intro: [
      "Teams used one software-building agent to compile complex requirements into a running system. The preliminary round rebuilt GitHub-style collaboration and spreadsheets. The final round kept that behavior and applied incremental requirements.",
      "Bootcamp ran September 7–20, the preliminary round September 24–30, and the grand challenge October 5–7. Awards were presented on October 17 at GOSIM Shenzhen 2026. The prize pool was $24,000.",
    ],
    introZh: [
      "参赛队伍用同一个软件开发智能体，把复杂需求编译成可运行的系统。初赛复刻 GitHub 风格协作和电子表格；决赛在保留原有行为的基础上完成增量需求。",
      "研习营 9 月 7 日至 20 日，初赛 9 月 24 日至 30 日，大奖赛 10 月 5 日至 7 日，10 月 17 日在深圳 GOSIM 2026 颁奖。总奖金池 24,000 美元。",
    ],
    tracks: [
      {
        id: "hackathon",
        kind: "preliminary",
        title: "Agentic Software Factory Hackathon",
        artTitle: "Official Hackathon",
        artDetail: "GitHub + Spreadsheets feature reproduction.",
        summary: "Build a software factory that compiles large requirements into reliable GitHub-style collaboration and spreadsheet applications.",
        when: "Sep 1, 2026 – Oct 3, 2026",
        meta: "5 tasks · 300 tests",
      },
      {
        id: "hackathon-evolution",
        kind: "final",
        title: "Agentic Software Factory Hackathon Evolution",
        artTitle: "Hackathon Evolution",
        artDetail: "A harder brief drawn from real, complex enterprise requirements.",
        summary: "Extend the applications produced by the Official Hackathon while preserving their existing behavior. Each task starts from the application selected for the participant's latest leaderboard submission, and falls back to the default template when no eligible source result is available.",
        when: "Oct 5, 2026 – Oct 23, 2026",
        meta: "2 tasks · 60 tests",
      },
    ],
    finalStandings: historyFinalStandings as HistoryStanding[],
    preliminaryStandings: historyPrelimStandings as HistoryStanding[],
  },
];

export function historyEventById(id: string) {
  return historyEvents.find((event) => event.id === id) ?? null;
}
