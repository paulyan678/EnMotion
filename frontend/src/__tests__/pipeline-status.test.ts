import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("episode pipeline status semantics", () => {
  it("does not render a completed checkmark before a video is assembled", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/components/project/ProjectClient.tsx"),
      "utf8",
    );

    expect(source).toContain(
      'frameCount > 0 ? { status: "warn", statusLabel: tp("railAssemblyReady") }',
    );
    expect(source).toContain(
      '{ status: "ready", statusLabel: tp("railAssembled") }',
    );
  });
});
