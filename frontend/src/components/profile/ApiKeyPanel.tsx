import { CheckOutlined, CopyOutlined, KeyOutlined, LoadingOutlined } from "@ant-design/icons";
import { message } from "antd";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { useI18n } from "../../i18n/I18nContext";
import { api } from "../../lib/api";
import { formatBeijingTime } from "../../lib/time";
import type { UserApiKeySummary } from "../../lib/types";

type CopyState = "requesting" | "loading" | "copied";

export default function ApiKeyPanel() {
  const { t } = useI18n();
  const [items, setItems] = useState<UserApiKeySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [copyStates, setCopyStates] = useState<Partial<Record<string, CopyState>>>({});
  const active = useRef(false);
  const controllers = useRef(new Set<AbortController>());
  const copying = useRef(new Set<string>());
  const feedbackTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  // The parent keys this panel by user ID. Invalidate pending work synchronously
  // when switching accounts or tabs, before any old response can be used.
  useLayoutEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      for (const controller of controllers.current) controller.abort();
      controllers.current.clear();
      for (const timer of feedbackTimers.current.values()) clearTimeout(timer);
      feedbackTimers.current.clear();
      copying.current.clear();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    controllers.current.add(controller);
    setLoading(true);
    setFailed(false);
    api.listApiKeys(controller.signal)
      .then((records) => {
        if (active.current && !controller.signal.aborted) setItems(records);
      })
      .catch(() => {
        if (active.current && !controller.signal.aborted) setFailed(true);
      })
      .finally(() => {
        controllers.current.delete(controller);
        if (active.current && !controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      controllers.current.delete(controller);
    };
  }, [attempt]);

  const copyKey = async (id: string) => {
    if (!active.current || copying.current.has(id)) return;
    if (!navigator.clipboard?.writeText) {
      message.error(t("Could not copy API key. Please try again."));
      return;
    }
    const controller = new AbortController();
    controllers.current.add(controller);
    copying.current.add(id);
    setCopyStates((previous) => ({ ...previous, [id]: "requesting" }));
    feedbackTimers.current.set(id, setTimeout(() => {
      if (active.current && !controller.signal.aborted) {
        setCopyStates((previous) => ({ ...previous, [id]: "loading" }));
      }
    }, 200));
    let copied = false;
    try {
      // Keep the full secret local to this action; never put it in component state.
      const secret = await api.getApiKeySecret(id, controller.signal);
      if (!active.current || controller.signal.aborted) return;
      await navigator.clipboard.writeText(secret.api_key);
      if (active.current && !controller.signal.aborted) {
        copied = true;
        clearTimeout(feedbackTimers.current.get(id));
        setCopyStates((previous) => ({ ...previous, [id]: "copied" }));
        feedbackTimers.current.set(id, setTimeout(() => {
          feedbackTimers.current.delete(id);
          copying.current.delete(id);
          if (active.current) {
            setCopyStates((previous) => {
              const next = { ...previous };
              delete next[id];
              return next;
            });
          }
        }, 1500));
      }
    } catch {
      if (active.current && !controller.signal.aborted) {
        message.error(t("Could not copy API key. Please try again."));
      }
    } finally {
      controllers.current.delete(controller);
      if (!copied) {
        clearTimeout(feedbackTimers.current.get(id));
        feedbackTimers.current.delete(id);
        copying.current.delete(id);
        if (active.current && !controller.signal.aborted) {
          setCopyStates((previous) => {
            const next = { ...previous };
            delete next[id];
            return next;
          });
        }
      }
    }
  };

  return (
    <section className="profile-records-card profile-api-keys" data-i18n-skip aria-labelledby="api-keys-title">
      <header className="profile-records-header">
        <div><h2 id="api-keys-title"><KeyOutlined /> {t("API Key")}</h2><p>{t("View your assigned API keys and copy a key when needed.")}</p></div>
      </header>
      {loading ? <div className="profile-records-empty" role="status">{t("Loading API keys...")}</div>
        : failed ? <div className="profile-records-empty" role="alert"><p>{t("Could not load API keys.")}</p><button className="profile-api-key-copy" type="button" onClick={() => setAttempt((value) => value + 1)}>{t("Retry")}</button></div>
          : items.length === 0 ? <div className="profile-records-empty">{t("No API keys assigned yet.")}</div>
            : <ul className="profile-api-key-list">{items.map((item) => {
              const state = copyStates[item.id];
              const visibleLabel = state === "copied" ? "Copied" : state === "loading" ? "Copying..." : "Copy API key";
              return (
                <li className="profile-api-key-row" key={item.id}>
                  <div className="profile-api-key-details">
                    <strong>{item.name || t("Unnamed API key")}</strong>
                    <code>{item.masked_key}</code>
                    <span>{t("Assigned on")} <time>{formatBeijingTime(item.created_at)}</time></span>
                  </div>
                  <button
                    type="button"
                    className={`profile-api-key-copy${state === "copied" ? " is-copied" : ""}`}
                    aria-disabled={Boolean(state)}
                    aria-busy={state === "requesting" || state === "loading"}
                    aria-label={t(state === "copied" ? "API key copied." : "Copy API key")}
                    aria-live="polite"
                    aria-atomic="true"
                    onClick={() => void copyKey(item.id)}
                  >
                    <span className="profile-api-key-copy-icon" aria-hidden="true">{state === "copied" ? <CheckOutlined /> : state === "loading" ? <LoadingOutlined spin /> : <CopyOutlined />}</span>
                    <span className="profile-api-key-copy-label" aria-hidden="true">{["Copy API key", "Copying...", "Copied"].map((label) => (
                      <span key={label} className={visibleLabel === label ? "is-visible" : undefined}>{t(label)}</span>
                    ))}</span>
                  </button>
                </li>
              );
            })}</ul>}
    </section>
  );
}
