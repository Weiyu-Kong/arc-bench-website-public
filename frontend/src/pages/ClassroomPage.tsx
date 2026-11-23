import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { TeamOutlined, TrophyOutlined } from "@ant-design/icons";

import { useAuth } from "../auth/AuthContext";
import { useI18n } from "../i18n/I18nContext";

export default function ClassroomPage() {
  const { user, isLoading, classroom, classroomLoading } = useAuth();
  const { language } = useI18n();
  const zh = language === "zh";
  const [allClassrooms, setAllClassrooms] = useState<Array<{ id: string; name: string; description: string; member_count: number; team_count: number }> | null>(null);
  useEffect(() => {
    if (user?.is_admin) void import("../lib/api").then(({ api }) => api.listAllClassrooms().then(setAllClassrooms).catch(() => setAllClassrooms([])));
  }, [user?.is_admin]);
  if (isLoading || classroomLoading) return <div className="page classroom-page">{zh ? "正在加载班级…" : "Loading class…"}</div>;
  if (!user) return <div className="page classroom-page"><h1>{zh ? "我的班级" : "My class"}</h1><Link to="/login">{zh ? "登录后查看班级" : "Sign in to view your class"}</Link></div>;
  if (user.is_admin && allClassrooms) return <div className="page classroom-page"><Link to="/playground">← {zh ? "返回工作台" : "Back to Playground"}</Link><header className="classroom-header"><span><TeamOutlined /> {zh ? "全部班级" : "All classes"}</span><h1>{zh ? "管理员班级视图" : "Administrator class view"}</h1></header><div className="classroom-grid">{allClassrooms.map((item) => <article key={item.id}><h2>{item.name}</h2><p>{item.description}</p><p>{item.member_count} {zh ? "名成员 ·" : "members ·"} {item.team_count} {zh ? "支队伍" : "teams"}</p><div className="classroom-actions"><Link to={`/competition?competition=first-competition&classroom=${encodeURIComponent(item.id)}`}>{zh ? "Smoke 排行榜" : "Smoke leaderboard"}</Link><Link to={`/competition?competition=arc-bench-web&classroom=${encodeURIComponent(item.id)}`}>{zh ? "Web 排行榜" : "Web leaderboard"}</Link><Link to={`/competition?competition=arc-bench-evolution&classroom=${encodeURIComponent(item.id)}`}>{zh ? "Evolution 排行榜" : "Evolution leaderboard"}</Link></div></article>)}</div></div>;
  if (!classroom) return <div className="page classroom-page"><h1>{zh ? "我的班级" : "My class"}</h1><p>{zh ? "班级信息加载失败，请刷新页面重试。" : "Could not load your class. Refresh to retry."}</p></div>;
  const info = classroom.classroom;
  if (!info) return <div className="page classroom-page"><h1>{zh ? "我的班级" : "My class"}</h1><p>{zh ? "尚未分配班级。请使用课程名单中的注册邮箱，并联系教师确认名单。" : "No class is assigned. Use your course roster email and contact the course staff."}</p></div>;
  return (
    <div className="page classroom-page">
      <Link to="/playground">← {zh ? "返回工作台" : "Back to Playground"}</Link>
      <header className="classroom-header"><span><TeamOutlined /> {zh ? "我的班级" : "My class"}</span><h1>{info.name}</h1><p>{info.description}</p></header>
      <div className="classroom-stats"><article><strong>{info.member_count}</strong><span>{zh ? "已注册成员" : "Registered members"}</span></article><article><strong>{info.team_count}</strong><span>{zh ? "已组建队伍" : "Assigned teams"}</span></article><article><strong>{classroom.tracks.length}</strong><span>{zh ? "可参加 Track" : "Available tracks"}</span></article></div>
      <section className="classroom-section"><h2>{zh ? "我的队伍" : "My team"}</h2>{classroom.team ? <><h3>{classroom.team.name} · {classroom.is_team_leader ? (zh ? "队长" : "Leader") : (zh ? "队员" : "Member")}</h3><p>{classroom.team.members.map((member) => `${member.name}${member.is_leader ? (zh ? "（队长）" : " (leader)") : ""}`).join("、")}</p></> : <p>{zh ? "队伍待分配，或名单指定的队长尚未注册。请联系教师。" : "Team assignment is pending, or the designated leader has not registered. Contact course staff."}</p>}<p className="classroom-notice">{zh ? "队伍成员与队长由课程名单统一分配，不能自行离队或变更组员。Smoke Competition 每个人都可以独立提交、运行和以个人身份上榜。其他班级竞赛仅队长可以提交智能体、启动运行和选择上榜提交；队员可查看共享提交和结果，以队伍名上榜，队长可在个人主页的队伍面板修改队名。" : "Course staff assign membership and leaders. Members cannot leave or change the roster. Everyone can submit, run and rank individually in Smoke Competition. Other class competitions require the leader to submit, run and select leaderboard submissions. Members can view shared results under the team name; leaders can rename it in their profile."}</p></section>
      <section className="classroom-section"><h2><TrophyOutlined /> {zh ? "可参加的 Track" : "Available tracks"}</h2><div className="classroom-grid">{classroom.tracks.map((track) => <article key={track.id}><h3>{track.id === "first-competition" ? "Smoke Competition" : track.title}</h3><p>{track.status}</p><div className="classroom-actions"><Link to={`/competitions/${track.id}`}>{zh ? "进入竞赛" : "Open competition"}</Link><Link to={`/competition?competition=${encodeURIComponent(track.id)}&classroom=${encodeURIComponent(info.id)}`}>{zh ? "班级排行榜" : "Class leaderboard"}</Link></div></article>)}</div></section>
      <section className="classroom-section"><h2>{zh ? "班级队伍" : "Class teams"}</h2><div className="classroom-grid">{classroom.teams.map((team) => <article key={team.id}><h3>{team.name}</h3><ul>{team.members.map((member) => <li key={member.user_id}>{member.name} {member.is_leader ? (zh ? "· 队长" : "· Leader") : ""}</li>)}</ul></article>)}</div></section>
    </div>
  );
}
