import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { getBundledAssetUrl } from "@/lib/bundledAssetUrl";

describe("bundled public asset URLs", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("prefixes public assets for the production static export", () => {
    vi.stubEnv("NODE_ENV", "production");

    expect(getBundledAssetUrl("/assets/styles/example.png"))
      .toBe("/static/assets/styles/example.png");
  });

  it("keeps development and non-bundled URLs unchanged", () => {
    vi.stubEnv("NODE_ENV", "development");

    expect(getBundledAssetUrl("/assets/styles/example.png"))
      .toBe("/assets/styles/example.png");
    expect(getBundledAssetUrl("/files/generated/example.png"))
      .toBe("/files/generated/example.png");
    expect(getBundledAssetUrl("https://cdn.example.com/example.png"))
      .toBe("https://cdn.example.com/example.png");
  });

  it("does not double-prefix an already exported asset", () => {
    vi.stubEnv("NODE_ENV", "production");

    expect(getBundledAssetUrl("/static/assets/styles/example.png"))
      .toBe("/static/assets/styles/example.png");
  });

  it("routes every style-preset thumbnail through the shared resolver", () => {
    for (const component of [
      "src/components/modules/ArtDirection.tsx",
      "src/components/series/SeriesArtDirectionPanel.tsx",
    ]) {
      const source = readFileSync(resolve(process.cwd(), component), "utf8");
      expect(source).toContain("getBundledAssetUrl");
      expect(source).not.toMatch(/src=\{(?:style|preset|p)\.thumbnail\}/);
    }
  });
});
