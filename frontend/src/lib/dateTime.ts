export const APP_TIME_ZONE = "Asia/Shanghai";

const EXPLICIT_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i;
const ISO_WITHOUT_ZONE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/;

/**
 * API timestamps are UTC. Older SQLite-backed responses could omit their
 * timezone suffix, so normalize that legacy shape before constructing Date.
 */
export function parseApiTimestamp(value: string | null | undefined): Date | null {
  const raw = value?.trim();
  if (!raw) return null;
  const normalized = ISO_WITHOUT_ZONE.test(raw) && !EXPLICIT_ZONE.test(raw)
    ? `${raw}Z`
    : raw;
  const date = new Date(normalized);
  return Number.isFinite(date.getTime()) ? date : null;
}

export function appDateTimeFormatter(
  locale: string,
  options: Intl.DateTimeFormatOptions,
): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat(locale, {
    ...options,
    timeZone: APP_TIME_ZONE,
  });
}

export function apiTimestampMilliseconds(
  value: string | null | undefined,
): number | null {
  return parseApiTimestamp(value)?.getTime() ?? null;
}

export function appCalendarDateKey(date: Date): string {
  const parts = appDateTimeFormatter("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(
    parts
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return `${values.year}-${values.month}-${values.day}`;
}
