import { useI18n } from "../../i18n/I18nContext";
import type { CompetitionLeaderboardEntry } from "../../lib/types";

export default function FundingSourceBadge({ source }: { source: CompetitionLeaderboardEntry["funding_source"] }) {
  const { language } = useI18n();
  const labels = {
    self_funded: language === "zh" ? "自有 Key" : "Own API key",
    official_evaluation: language === "zh" ? "历史评测" : "Legacy evaluation",
    mixed: language === "zh" ? "混合额度" : "Mixed credits",
  };
  const label = source ? labels[source] : language === "zh" ? "来源未记录" : "Source unavailable";
  return <span className="competition-funding-badge" data-funding-source={source ?? "unknown"}>{label}</span>;
}
