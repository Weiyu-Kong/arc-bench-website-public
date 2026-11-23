import { CalendarOutlined, RightOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";

import { useI18n } from "../i18n/I18nContext";
import { historyEvents } from "../lib/historyEvents";

export default function HistoryPage() {
  const { language } = useI18n();
  const zh = language === "zh";

  return (
    <div className="page competition-home-page history-page">
      <div className="competition-container">
        <section className="competition-hero">
          <div className="competition-hero-copy">
            <h1>Historical Events</h1>
          </div>
        </section>

        <section className="history-event-list" aria-label="Finished events">
          {historyEvents.map((event) => (
            <Link key={event.id} to={`/history/${event.id}`} className="history-event-card">
              <div className="history-event-card-copy">
                <span className="history-event-card-eyebrow">{event.eyebrow}</span>
                <h2>{zh ? event.titleZh : event.title}</h2>
                <p>{zh ? event.blurbZh : event.blurb}</p>
                <span className="history-event-card-meta"><CalendarOutlined /> {event.when}</span>
              </div>
              <RightOutlined aria-hidden="true" />
            </Link>
          ))}
        </section>
      </div>
    </div>
  );
}
