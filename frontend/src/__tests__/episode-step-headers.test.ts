import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const moduleNames = [
  "ScriptProcessor",
  "ArtDirection",
  "Cast",
  "StoryboardR2V",
  "VideoAssembly",
  "ConsistencyVault",
  "StoryboardComposer",
  "VideoGenerator",
] as const;

const titleKeys = [
  "scriptTitle",
  "styleTitle",
  "castTitle",
  "storyboardTitle",
  "assemblyTitle",
  "vaultTitle",
  "storyboardComposerTitle",
  "motionTitle",
] as const;

describe("Episode Editor step headers", () => {
  it.each(moduleNames)("uses the compact shared header in %s", (moduleName) => {
    const source = readFileSync(
      resolve(process.cwd(), "src", "components", "modules", `${moduleName}.tsx`),
      "utf8",
    );

    expect(source).toContain("<StepPageHeader");
    expect(source).not.toContain("<StepHeader");
    expect(source).not.toContain("StepPill");
    expect(source).not.toMatch(/\b(?:stepNumber|englishName|pills)=/);
    expect(source).not.toContain('subtitle={tStep(');
  });

  it("removes the obsolete decorative header implementation", () => {
    expect(
      existsSync(resolve(process.cwd(), "src", "components", "shared", "StepHeader.tsx")),
    ).toBe(false);
  });

  it.each(["en", "zh"])("keeps only localized functional titles in %s", (locale) => {
    const messages = JSON.parse(
      readFileSync(resolve(process.cwd(), "messages", `${locale}.json`), "utf8"),
    ) as { stepHeader: Record<string, string> };

    expect(Object.keys(messages.stepHeader).sort()).toEqual([...titleKeys].sort());
    titleKeys.forEach((key) => expect(messages.stepHeader[key]).toBeTruthy());
  });
});
