import { describe, expect, it } from "vitest";

import {
  APP_TIME_ZONE,
  apiTimestampMilliseconds,
  appCalendarDateKey,
  appDateTimeFormatter,
  parseApiTimestamp,
} from "@/lib/dateTime";

describe("API timestamp handling", () => {
  it("treats legacy timezone-less API values as UTC", () => {
    expect(parseApiTimestamp("2026-07-30T00:00:00")?.toISOString()).toBe(
      "2026-07-30T00:00:00.000Z",
    );
  });

  it("preserves explicit instants and formats them in the application timezone", () => {
    const date = parseApiTimestamp("2026-07-30T00:00:00Z");
    expect(date?.toISOString()).toBe("2026-07-30T00:00:00.000Z");
    expect(appDateTimeFormatter("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).resolvedOptions().timeZone).toBe(APP_TIME_ZONE);
    expect(appDateTimeFormatter("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(date!)).toBe("08:00");
  });

  it("rejects invalid input instead of rendering an Invalid Date", () => {
    expect(parseApiTimestamp("not-a-date")).toBeNull();
    expect(parseApiTimestamp(null)).toBeNull();
    expect(apiTimestampMilliseconds("not-a-date")).toBeNull();
  });

  it("uses the application calendar day across a UTC midnight boundary", () => {
    const lateUtc = parseApiTimestamp("2026-07-29T23:30:00");
    const sameShanghaiDay = parseApiTimestamp("2026-07-30T15:59:59Z");
    expect(appCalendarDateKey(lateUtc!)).toBe("2026-07-30");
    expect(appCalendarDateKey(sameShanghaiDay!)).toBe("2026-07-30");
    expect(apiTimestampMilliseconds("2026-07-29T23:30:00")).toBe(
      Date.parse("2026-07-29T23:30:00Z"),
    );
  });
});
