export const BEIJING_TIME_ZONE = "Asia/Shanghai";

// The API stores UTC timestamps, including legacy values without a suffix.
export function parseUtcDate(value: string): Date {
  return new Date(/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`);
}

export function formatBeijingTime(value: string | Date = new Date()): string {
  const date = typeof value === "string" ? parseUtcDate(value) : value;
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString("zh-CN", {
    timeZone: BEIJING_TIME_ZONE,
    hour12: false,
  });
}

export function formatBeijingDate(value: string, options: Intl.DateTimeFormatOptions = {}): string {
  const date = parseUtcDate(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleDateString("zh-CN", {
    ...options,
    timeZone: BEIJING_TIME_ZONE,
  });
}
