/** Show the full-competition estimate before starting billable agent work. */
export function competitionCostNotice(competitionId: string | null | undefined, language: string): string | null {
  if (!competitionId?.startsWith("arc-bench")) return null;
  return language === "zh"
    ? "费用提醒：使用 DeepSeek 跑完整个 ARC-Bench 竞赛预计消耗约 ¥300。此金额为整个竞赛的估算，并非本次任务的固定费用；实际费用取决于模型、重试次数和运行用量。请确认自己的 API 账户余额充足，并注意实际费用。"
    : "Cost reminder: completing the full ARC-Bench competition with DeepSeek is estimated to cost approximately ¥300. This is a full-competition estimate, not a fixed fee for this task. Actual costs depend on the model, retries and usage. Please check your own API account balance and monitor spending.";
}

export function confirmCompetitionRunCost(competitionId: string | null | undefined, language: string): boolean {
  const notice = competitionCostNotice(competitionId, language);
  return !notice || window.confirm(`${notice}\n\n${language === "zh" ? "确认继续运行？" : "Continue running?"}`);
}
