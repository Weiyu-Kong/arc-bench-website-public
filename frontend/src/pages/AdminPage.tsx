import { Tabs } from "antd";
import { useCallback, useRef, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import CompetitionLeaderboard from "../components/competition/CompetitionLeaderboard";
import AdminUsers from "../components/admin/AdminUsers";
import { useI18n } from "../i18n/I18nContext";

export default function AdminPage() {
  const { user, isLoading, refresh } = useAuth();
  const { t } = useI18n();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") === "leaderboard" ? "leaderboard" : "user";
  const [denied, setDenied] = useState(false);
  const returnTo = location.pathname + location.search;
  const returnToRef = useRef(returnTo);
  returnToRef.current = returnTo;
  const onAccessDenied = useCallback((status: number) => {
    setDenied(true);
    if (status === 401) navigate("/login", { replace: true, state: { from: returnToRef.current } });
    void refresh();
  }, [navigate, refresh]);

  if (isLoading) return <div className="page centered" role="status">{t("Checking access…")}</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: returnTo }} />;
  if (!user.is_admin || denied) return (
    <div className="page centered">
      <div className="admin-access-card" role="alert">
        <h1>{t("Access denied")}</h1>
        <p>{t("This page is available to administrators only.")}</p>
        <Link to="/competition">{t("Back to Competition")}</Link>
      </div>
    </div>
  );
  return (
    <div className="page competition-home-page admin-leaderboard-page">
      <div className="competition-container">
        <h1>{t("Administrator dashboard")}</h1>
        <Tabs className="admin-dashboard-tabs" activeKey={activeTab} onChange={(tab) => {
          setSearchParams((current) => { const next = new URLSearchParams(current); next.set("tab", tab); return next; }, { replace: true });
        }} items={[
          { key: "user", label: t("User"), children: <AdminUsers onAccessDenied={onAccessDenied} /> },
          { key: "leaderboard", label: t("Leaderboard"), children: <CompetitionLeaderboard admin onAccessDenied={onAccessDenied} /> },
        ]} />
      </div>
    </div>
  );
}
