import { describe, expect, it } from "vitest";

import { hasPreviousEpisodeScript } from "@/components/modules/PreviousEpisodeSummary";

describe("hasPreviousEpisodeScript", () => {
  it("preserves compatibility with legacy payloads that omitted script_available", () => {
    expect(
      hasPreviousEpisodeScript({
        raw_snippet: "A saved ending",
        ai_summary: null,
      }),
    ).toBe(true);
    expect(
      hasPreviousEpisodeScript({
        raw_snippet: "",
        ai_summary: "A saved recap",
      }),
    ).toBe(true);
  });

  it("honors an explicit script availability flag", () => {
    expect(
      hasPreviousEpisodeScript({
        script_available: false,
        raw_snippet: "legacy content",
        ai_summary: "legacy recap",
      }),
    ).toBe(false);
    expect(
      hasPreviousEpisodeScript({
        script_available: true,
        raw_snippet: "",
        ai_summary: null,
      }),
    ).toBe(true);
  });
});
