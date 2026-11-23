import { Button, Pagination } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";

import { api, ApiError } from "../../lib/api";
import type { AdminUserEntry, AdminUserPage } from "../../lib/api";
import AdminUserSubmissions from "./AdminUserSubmissions";
import { useI18n } from "../../i18n/I18nContext";
import { formatBeijingTime, parseUtcDate } from "../../lib/time";

export default function AdminUsers({ onAccessDenied }: { onAccessDenied: (status: number) => void }) {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState({ page: 1, q: "", classroomId: "", leader: "all" as "all" | "yes" | "no" });
  const [data, setData] = useState<AdminUserPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [classrooms, setClassrooms] = useState<Array<{ id: string; name: string }>>([]);
  const [classroomError, setClassroomError] = useState("");
  const [selectedUser, setSelectedUser] = useState<AdminUserEntry | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.listAllClassrooms().then((items) => {
      if (!cancelled) setClassrooms(items.map(({ id, name }) => ({ id, name })));
    }).catch((reason: unknown) => {
      if (cancelled) return;
      if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) {
        setData(null);
        onAccessDenied(reason.status);
      } else setClassroomError(reason instanceof Error ? reason.message : "Unable to load classrooms");
    });
    return () => { cancelled = true; };
  }, [onAccessDenied]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    // Keep only this page. No prefetching, all-user endpoint, or client-side filtering.
    setData(null);
    api.listAdminUsers(query, controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      const lastPage = Math.max(1, Math.ceil(page.total / 20));
      if (query.page > lastPage) {
        setQuery((current) => ({ ...current, page: lastPage }));
        return;
      }
      setData(page);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) onAccessDenied(reason.status);
      else setError(reason instanceof Error ? reason.message : "Unable to load users");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, onAccessDenied]);

  return (
    <section className="admin-users" data-i18n-skip aria-label={t("User directory")}>
      <form className="admin-users-toolbar" onSubmit={(event) => {
        event.preventDefault();
        setQuery((current) => ({ ...current, q: search.trim(), page: 1 }));
      }}>
        <label className="competition-search-field admin-users-search">
          <SearchOutlined aria-hidden="true" />
          <span className="visually-hidden">{t("Search username or email")}</span>
          <input value={search} maxLength={255} onChange={(event) => setSearch(event.target.value)} placeholder={t("Search username or email…")} />
        </label>
        <Button htmlType="submit">{t("Search")}</Button>
        <label className="admin-users-filter">
          <span>{t("Classroom")}</span>
          <select value={query.classroomId} onChange={(event) => setQuery((current) => ({ ...current, classroomId: event.target.value, page: 1 }))}>
            <option value="">{t("All classrooms")}</option>
            {classrooms.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label className="admin-users-filter">
          <span>{t("Team leader")}</span>
          <select value={query.leader} onChange={(event) => setQuery((current) => ({ ...current, leader: event.target.value as "all" | "yes" | "no", page: 1 }))}>
            <option value="all">{t("All users")}</option>
            <option value="yes">{t("Leaders")}</option>
            <option value="no">{t("Non-leaders")}</option>
          </select>
        </label>
      </form>
      {classroomError ? <p role="alert">{t("Unable to load classroom filters: ")}{t(classroomError)}</p> : null}
      {error ? <div className="admin-leaderboard-error" role="alert">{t(error)} <Button onClick={() => setQuery((current) => ({ ...current }))}>{t("Retry")}</Button></div> : null}
      <div className="competition-leaderboard">
        <div className="competition-table-wrap">
          <table className="admin-users-table" aria-busy={loading}>
            <thead><tr><th>{t("Username")}</th><th>{t("Email")}</th><th>{t("Classroom")}</th><th>{t("Leader")}</th><th>{t("Registered")}</th><th>{t("Actions")}</th></tr></thead>
            <tbody>
              {loading ? <tr><td colSpan={6} role="status">{t("Loading users…")}</td></tr>
                : error ? <tr><td colSpan={6}>{t("Unable to load users")}</td></tr>
                : !data?.items.length ? <tr><td colSpan={6}>{t("No matching users")}</td></tr>
                : data.items.map((item) => <tr key={item.id}>
                  <td>{item.username}</td><td>{item.email}</td><td>{item.classroom_name ?? t("Unassigned")}</td>
                  <td>{t(item.is_leader ? "Yes" : "No")}</td>
                  <td>{formatBeijingTime(parseUtcDate(item.created_at))}</td>
                  <td><Button icon={<SearchOutlined />} onClick={() => setSelectedUser(item)}>{t("View submissions")}</Button></td>
                </tr>)}
            </tbody>
          </table>
        </div>
        {data ? <footer className="competition-pagination admin-users-pagination">
          <span>{t("{count} users · 20 per page").replace("{count}", String(data.total))}</span>
          <Pagination current={query.page} total={data.total} pageSize={20} showSizeChanger={false} disabled={loading}
            onChange={(page) => setQuery((current) => ({ ...current, page }))} />
        </footer> : null}
      </div>
      {selectedUser ? <AdminUserSubmissions key={selectedUser.id} user={selectedUser} onClose={() => setSelectedUser(null)} onAccessDenied={onAccessDenied} /> : null}
    </section>
  );
}
