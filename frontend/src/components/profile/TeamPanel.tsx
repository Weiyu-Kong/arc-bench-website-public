import { CopyOutlined, CrownOutlined, DeleteOutlined, EditOutlined, LogoutOutlined, TeamOutlined, UserAddOutlined, UserDeleteOutlined } from "@ant-design/icons";
import { message, Modal } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "../../auth/AuthContext";
import { useI18n } from "../../i18n/I18nContext";
import { api } from "../../lib/api";
import type { MyTeamResponse } from "../../lib/types";

function normalizedName(value: string) {
  return value.trim().replace(/\s+/g, " ");
}

export default function TeamPanel() {
  const { user } = useAuth();
  return user ? <TeamWorkspace key={user.id} userId={user.id} /> : null;
}

function TeamWorkspace({ userId }: { userId: string }) {
  const { t, language } = useI18n();
  const [teamState, setTeamState] = useState<MyTeamResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [teamName, setTeamName] = useState("");
  const [teamSlogan, setTeamSlogan] = useState("");
  const [teamIdToJoin, setTeamIdToJoin] = useState("");
  const [teamNameEdit, setTeamNameEdit] = useState("");
  const [teamNameEditing, setTeamNameEditing] = useState(false);
  const [modal, setModal] = useState<"create" | "join" | null>(null);
  const [submitting, setSubmitting] = useState<string | null>(null);
  const active = useRef(true);
  const loadVersion = useRef(0);
  const mutationLocked = useRef(false);

  const team = teamState?.team;
  const managed = Boolean(teamState?.source_managed);
  const isLeader = Boolean(team && team.leader_user_id === userId);
  const busy = loading || submitting !== null;
  const pending = teamState?.pending_request;

  const refreshTeam = useCallback(async () => {
    const version = ++loadVersion.current;
    setLoading(true);
    setLoadFailed(false);
    try {
      const next = await api.getMyTeam();
      if (active.current && version === loadVersion.current) setTeamState(next);
    } catch {
      if (active.current && version === loadVersion.current) setLoadFailed(true);
    } finally {
      if (active.current && version === loadVersion.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    active.current = true;
    void refreshTeam();
    return () => {
      active.current = false;
      loadVersion.current += 1;
    };
  }, [refreshTeam]);

  useEffect(() => {
    setTeamNameEdit(team?.name ?? "");
    setTeamNameEditing(false);
  }, [team?.id, team?.name]);

  const beginMutation = (action: string) => {
    if (!active.current || busy || mutationLocked.current) return false;
    mutationLocked.current = true;
    setSubmitting(action);
    return true;
  };
  const endMutation = () => {
    mutationLocked.current = false;
    if (active.current) setSubmitting(null);
  };

  const createTeam = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = normalizedName(teamName);
    if (team || managed || name.length < 2 || name.length > 120 || !beginMutation("create")) return;
    try {
      const next = await api.createTeam({ name, slogan: teamSlogan.trim() || null });
      if (!active.current) return;
      setTeamState(next);
      setTeamName("");
      setTeamSlogan("");
      setModal(null);
      message.success(t("Team created. You are the team leader."));
    } catch (error) {
      if (active.current) message.error(t((error as Error).message));
    } finally { endMutation(); }
  };

  const requestTeamJoin = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (team || managed || pending || !teamIdToJoin.trim() || !beginMutation("join")) return;
    try {
      await api.requestTeamJoin(teamIdToJoin.trim());
      if (!active.current) return;
      setTeamIdToJoin("");
      setModal(null);
      message.success(t("Join request sent to the team leader."));
      await refreshTeam();
    } catch (error) {
      if (active.current) message.error(t((error as Error).message));
    } finally { endMutation(); }
  };

  const leaveTeam = async () => {
    if (!team || managed || busy || mutationLocked.current) return;
    const confirmation = isLeader
      ? t("Disband this team? This removes all team memberships. Historical submissions and runs are preserved.")
      : t("Leave this team? You will lose access to its workspace.");
    if (!window.confirm(confirmation) || !beginMutation("leave")) return;
    try {
      await api.leaveTeam();
      if (!active.current) return;
      setTeamState(null);
      message.success(t(isLeader ? "Team disbanded." : "You left the team."));
      await refreshTeam();
    } catch (error) {
      if (active.current) message.error(t((error as Error).message));
    } finally { endMutation(); }
  };

  const removeMember = async (memberUserId: string, memberName: string) => {
    if (!isLeader || managed || memberUserId === userId || busy || mutationLocked.current) return;
    const confirmation = language === "zh"
      ? `确定移除 ${memberName}？该成员将失去团队工作区的访问权限。`
      : `Remove ${memberName}? They will lose access to the team workspace.`;
    if (!window.confirm(confirmation) || !beginMutation(`remove:${memberUserId}`)) return;
    try {
      const next = await api.removeTeamMember(memberUserId);
      if (!active.current) return;
      setTeamState(next);
      message.success(language === "zh" ? `已从团队中移除 ${memberName}。` : `${memberName} was removed from the team.`);
    } catch (error) {
      if (active.current) message.error(t((error as Error).message));
    } finally { endMutation(); }
  };

  const copyTeamId = async () => {
    if (!team) return;
    try {
      await navigator.clipboard.writeText(team.id);
      if (active.current) message.success(t("Team ID copied."));
    } catch {
      if (active.current) message.error(t("Could not copy the team ID. Copy it manually instead."));
    }
  };

  const renameTeam = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = normalizedName(teamNameEdit);
    if (!isLeader || name.length < 2 || name.length > 120 || !beginMutation("rename")) return;
    try {
      const next = await api.renameTeam(name);
      if (!active.current) return;
      setTeamState(next);
      setTeamNameEditing(false);
      message.success(t("Team name updated."));
    } catch (error) {
      if (active.current) message.error(t((error as Error).message));
    } finally { endMutation(); }
  };

  if (loading && !teamState) {
    return <section className="team-workspace team-workspace-status" data-i18n-skip role="status">{t("Loading team workspace...")}</section>;
  }
  if (loadFailed || !teamState) {
    return <section className="team-workspace team-workspace-status" data-i18n-skip role="alert">
      <p>{t("Could not load team workspace.")}</p>
      <button type="button" className="team-secondary-action" disabled={loading} onClick={() => void refreshTeam()}>{t("Retry")}</button>
    </section>;
  }

  const joinDisabledReason = managed
    ? "Class teams are managed by course staff."
    : team
      ? isLeader ? "Disband your current team before requesting to join another team." : "Leave your current team before requesting to join another team."
      : null;

  return (
    <>
      <section className="team-workspace" data-i18n-skip aria-labelledby="team-workspace-title" aria-busy={busy}>
        <header className="team-workspace-header">
          <span className="team-workspace-kicker" id="team-workspace-title">{t("Team workspace")}</span>
          {teamNameEditing && team ? (
            <form className="team-name-edit" onSubmit={renameTeam}>
              <input autoFocus className="text-input" value={teamNameEdit} disabled={busy} onChange={(event) => setTeamNameEdit(event.target.value)} minLength={2} maxLength={120} aria-label={t("New team name")} required />
              <button type="submit" className="team-secondary-action" disabled={busy || normalizedName(teamNameEdit).length < 2}>{t(submitting === "rename" ? "Saving..." : "Save")}</button>
              <button type="button" className="team-neutral-action" disabled={busy} onClick={() => { setTeamNameEdit(team.name); setTeamNameEditing(false); }}>{t("Cancel")}</button>
            </form>
          ) : (
            <div className="team-title-row">
              <h2>{team?.name || t(managed ? "Class team assignment pending" : "Build together")}</h2>
              {isLeader ? <button type="button" className="team-secondary-action" disabled={busy} onClick={() => setTeamNameEditing(true)}><EditOutlined /> {t("Rename")}</button> : null}
            </div>
          )}
          {team?.slogan ? <p className="team-workspace-slogan">{team.slogan}</p> : null}
          {!team ? <p className="team-workspace-description">{t(managed ? "Your class team will be assigned by course staff." : "Create a team or request access using a team ID.")}</p> : null}
          {team ? <div className="team-id-row"><span>{t("Team ID")}</span><code>{team.id}</code><button type="button" onClick={() => void copyTeamId()} aria-label={t("Copy team ID")} title={t("Copy team ID")}><CopyOutlined /></button></div> : null}
        </header>

        {team ? <section className="team-members-section" aria-labelledby="team-members-title">
          <div className="team-members-heading"><h3 id="team-members-title">{t("Members")}</h3><span>{teamState.members.length}</span></div>
          <table className="team-members-table">
            <thead><tr>{["Member", "Email", "Real name", "Student ID", "Role", "Actions"].map((label) => <th key={label} scope="col">{t(label)}</th>)}</tr></thead>
            <tbody>{teamState.members.map((member) => (
              <tr key={member.user_id}>
                <td data-label={t("Member")}><div className="team-member-person"><div className="team-member-avatar" aria-hidden="true">{(member.display_name || member.username).slice(0, 2).toUpperCase()}</div><div className="team-member-identity"><strong>{member.display_name || member.username}</strong><span>@{member.username}</span></div></div></td>
                <td data-label={t("Email")}>{member.email}</td>
                <td data-label={t("Real name")}>{member.real_name || t("Not set")}</td>
                <td data-label={t("Student ID")}>{member.student_id || t("Not set")}</td>
                <td data-label={t("Role")}><span className={`team-member-role ${member.role}`}>{member.role === "leader" ? <><CrownOutlined /> {t("Leader")}</> : t("Member")}</span></td>
                <td data-label={t("Actions")}>{isLeader && !managed && member.user_id !== userId
                  ? <button type="button" className="team-member-remove" disabled={busy} onClick={() => void removeMember(member.user_id, member.display_name || member.username)}><UserDeleteOutlined /> {t(submitting === `remove:${member.user_id}` ? "Removing..." : "Remove")}</button>
                  : <span className="team-no-action">—</span>}</td>
              </tr>
            ))}</tbody>
          </table>
        </section> : <div className="team-empty-state"><TeamOutlined aria-hidden="true" /><span>{t(managed ? "Class team assignment pending" : "You are not currently in a team.")}</span></div>}

        <footer className="team-operations" aria-labelledby="team-operations-title">
          <h3 id="team-operations-title">{t("Actions")}</h3>
          {joinDisabledReason ? <p className="team-operation-note" id="team-join-disabled-reason">{t(joinDisabledReason)}</p> : null}
          {!managed && pending ? <p className="team-operation-note" role="status">{t("Your request to")} <strong>{pending.team_name}</strong> {t("is awaiting a decision.")}</p> : null}
          <div className="team-operation-buttons">
            <div className="team-operation-primary">
              {!team ? <button type="button" className="team-secondary-action" disabled={busy || managed} onClick={() => setModal("create")}><CrownOutlined /> {t("Create team")}</button> : null}
              <button type="button" className="team-secondary-action" disabled={busy || Boolean(joinDisabledReason) || Boolean(pending)} aria-describedby={joinDisabledReason ? "team-join-disabled-reason" : undefined} onClick={() => setModal("join")}><UserAddOutlined /> {t("Request to join")}</button>
            </div>
            {team ? <button type="button" className="team-leave-action" disabled={busy || managed} aria-describedby={managed ? "team-join-disabled-reason" : undefined} onClick={() => void leaveTeam()}>{isLeader ? <DeleteOutlined /> : <LogoutOutlined />} {t(submitting === "leave" ? "Updating..." : isLeader ? "Disband team" : "Leave team")}</button> : null}
          </div>
        </footer>
      </section>

      <Modal className="team-management-modal" title={t("Create a team")} open={modal === "create"} onCancel={() => { if (!busy) setModal(null); }} closable={!busy} maskClosable={!busy} keyboard={!busy} footer={null} destroyOnHidden>
        <form className="team-modal-form" data-i18n-skip onSubmit={createTeam}>
          <p>{t("You become the leader and receive a private team ID.")}</p>
          <label htmlFor="team-name">{t("Team name")}</label>
          <input id="team-name" className="text-input" value={teamName} disabled={busy} onChange={(event) => setTeamName(event.target.value)} minLength={2} maxLength={120} placeholder={t("Compiler Crew")} required />
          <label htmlFor="team-slogan">{t("Team statement")} <span>{t("Optional")}</span></label>
          <textarea id="team-slogan" className="text-input" value={teamSlogan} disabled={busy} onChange={(event) => setTeamSlogan(event.target.value)} maxLength={240} placeholder={t("What brings your team together?")} />
          <div className="team-field-count">{teamSlogan.length}/240</div>
          <div className="team-modal-actions"><button type="button" className="team-neutral-action" disabled={busy} onClick={() => setModal(null)}>{t("Cancel")}</button><button className="team-secondary-action" type="submit" disabled={busy || managed || Boolean(team) || normalizedName(teamName).length < 2}>{t(submitting === "create" ? "Creating..." : "Create team")}</button></div>
        </form>
      </Modal>
      <Modal className="team-management-modal" title={t("Join a team")} open={modal === "join"} onCancel={() => { if (!busy) setModal(null); }} closable={!busy} maskClosable={!busy} keyboard={!busy} footer={null} destroyOnHidden>
        <form className="team-modal-form" data-i18n-skip onSubmit={requestTeamJoin}>
          <p>{t("Ask the team leader to review your profile and membership request.")}</p>
          <label htmlFor="team-id">{t("Team ID")}</label>
          <input id="team-id" className="text-input team-id-input" value={teamIdToJoin} disabled={busy} onChange={(event) => setTeamIdToJoin(event.target.value)} placeholder={t("Paste the 32-character team ID")} autoComplete="off" required />
          <div className="team-modal-actions"><button type="button" className="team-neutral-action" disabled={busy} onClick={() => setModal(null)}>{t("Cancel")}</button><button className="team-secondary-action" type="submit" disabled={busy || managed || Boolean(team) || Boolean(pending) || !teamIdToJoin.trim()}>{t(submitting === "join" ? "Sending..." : "Request to join")}</button></div>
        </form>
      </Modal>
    </>
  );
}
