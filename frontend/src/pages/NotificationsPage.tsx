import { formatBeijingTime } from "../lib/time";
import { BellOutlined, CheckCircleOutlined, DeleteOutlined, TeamOutlined } from "@ant-design/icons";
import { message, Modal } from "antd";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { api } from "../lib/api";
import { publishUnreadNotificationCount } from "../lib/notificationEvents";
import type { NotificationItem, TeamJoinRequestSummary } from "../lib/types";

export default function NotificationsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeCategory, setActiveCategory] = useState<"runs" | "teams">("runs");
  const [teamRequest, setTeamRequest] = useState<TeamJoinRequestSummary | null>(null);
  const [resolvingRequest, setResolvingRequest] = useState(false);

  const applyItems = useCallback((nextItems: NotificationItem[], unreadCount?: number) => {
    setItems(nextItems);
    publishUnreadNotificationCount(unreadCount ?? nextItems.filter((item) => !item.is_read).length);
  }, []);

  useEffect(() => {
    if (!user) {
      setLoading(false);
      return;
    }
    api.listNotifications()
      .then((payload) => applyItems(payload.items, payload.unread_count))
      .finally(() => setLoading(false));
  }, [applyItems, user]);

  const openNotification = async (item: NotificationItem) => {
    if (!item.is_read) {
      const updated = await api.markNotificationRead(item.id);
      setItems((current) => {
        const nextItems = current.map((entry) => entry.id === updated.id ? updated : entry);
        publishUnreadNotificationCount(nextItems.filter((entry) => !entry.is_read).length);
        return nextItems;
      });
    }
    if (item.run_id) {
      navigate(`/runs/${item.run_id}`);
    } else if (item.team_join_request_id) {
      try {
        setTeamRequest(await api.getTeamJoinRequest(item.team_join_request_id));
      } catch (error) {
        message.error((error as Error).message);
      }
    }
  };

  const resolveTeamRequest = async (accept: boolean) => {
    if (!teamRequest) return;
    setResolvingRequest(true);
    try {
      if (accept) await api.acceptTeamJoinRequest(teamRequest.id);
      else await api.declineTeamJoinRequest(teamRequest.id);
      setTeamRequest({ ...teamRequest, status: accept ? "ACCEPTED" : "DECLINED" });
      message.success(accept ? "Join request accepted." : "Join request declined.");
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setResolvingRequest(false);
    }
  };

  const markAllRead = async () => {
    try {
      const payload = await api.markAllNotificationsRead();
      applyItems(payload.items, payload.unread_count);
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  const deleteNotification = async (notificationId: string) => {
    try {
      const payload = await api.deleteNotification(notificationId);
      applyItems(payload.items, payload.unread_count);
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  const clearNotifications = async () => {
    try {
      const payload = await api.clearNotifications();
      applyItems(payload.items, payload.unread_count);
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  if (!user) {
    return <div className="page centered"><div className="empty-state">Login is required to view notifications.</div></div>;
  }

  const isTeamNotification = (item: NotificationItem) => item.kind.startsWith("team_");
  const visibleItems = items.filter((item) => activeCategory === "teams" ? isTeamNotification(item) : !isTeamNotification(item));
  const runUnread = items.filter((item) => !isTeamNotification(item) && !item.is_read).length;
  const teamUnread = items.filter((item) => isTeamNotification(item) && !item.is_read).length;

  return (
    <div className="page notification-page">
      <section className="notification-center">
        <header className="notification-center-header">
          <div className="notification-category-tabs" role="tablist" aria-label="Notification category">
            <button type="button" role="tab" aria-selected={activeCategory === "runs"} className={activeCategory === "runs" ? "active" : ""} onClick={() => setActiveCategory("runs")}><BellOutlined /> Run messages {runUnread > 0 ? <span>{runUnread}</span> : null}</button>
            <button type="button" role="tab" aria-selected={activeCategory === "teams"} className={activeCategory === "teams" ? "active" : ""} onClick={() => setActiveCategory("teams")}><TeamOutlined /> Team requests {teamUnread > 0 ? <span>{teamUnread}</span> : null}</button>
          </div>
          {items.length > 0 ? <div className="notification-list-actions">
            <button type="button" className="notification-list-action" onClick={() => void markAllRead()} disabled={!items.some((item) => !item.is_read)}><CheckCircleOutlined /> Mark all read</button>
            <button type="button" className="notification-list-action danger" onClick={() => void clearNotifications()}>Clear all</button>
          </div> : null}
        </header>
        {loading ? <div className="notification-list-loading">Loading notifications...</div> : visibleItems.length === 0 ? (
          <div className="notification-list-empty"><strong>{activeCategory === "runs" ? "No run messages" : "No team requests"}</strong><span>{activeCategory === "runs" ? "Run completion and failure messages will appear here." : "Team applications and decisions will appear here."}</span></div>
        ) : (
          <div className="notification-list">
            {visibleItems.map((item) => (
              <div key={item.id} className="notification-list-item">
                <button type="button" className="notification-list-open" onClick={() => void openNotification(item)}>
                  <div className="flex items-center justify-between gap-4">
                    <span className="notification-list-item-title">{item.title}</span>
                    <span className="notification-list-item-time">{formatBeijingTime(item.created_at)}{!item.is_read ? <span className="notification-item-unread-dot" aria-label="Unread" /> : null}</span>
                  </div>
                  <div className="notification-list-item-body">{item.body}</div>
                </button>
                <button type="button" className="notification-delete-button" aria-label={`Delete ${item.title}`} title="Delete message" onClick={() => void deleteNotification(item.id)}>
                  <DeleteOutlined />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
      <Modal title="Team join request" open={Boolean(teamRequest)} onCancel={() => setTeamRequest(null)} footer={null} destroyOnHidden>
        {teamRequest ? <div className="notification-team-request-detail">
          <div className="notification-team-request-avatar">{(teamRequest.display_name || teamRequest.username).slice(0, 2).toUpperCase()}</div>
          <div className="notification-team-request-person"><strong>{teamRequest.display_name || teamRequest.username}</strong><span>@{teamRequest.username}</span></div>
          <span className={`notification-team-request-status ${teamRequest.status.toLowerCase()}`}>{teamRequest.status.toLowerCase()}</span>
          <dl><div><dt>Team</dt><dd>{teamRequest.team_name}</dd></div><div><dt>Real name</dt><dd>{teamRequest.real_name || "Not provided"}</dd></div><div><dt>Student ID</dt><dd>{teamRequest.student_id || "Not provided"}</dd></div><div><dt>Applied</dt><dd>{formatBeijingTime(teamRequest.created_at)}</dd></div></dl>
          {teamRequest.status === "PENDING" ? <div className="notification-team-request-actions"><button type="button" onClick={() => void resolveTeamRequest(false)} disabled={resolvingRequest}>Decline</button><button type="button" onClick={() => void resolveTeamRequest(true)} disabled={resolvingRequest}>Accept member</button></div> : null}
        </div> : null}
      </Modal>
    </div>
  );
}
