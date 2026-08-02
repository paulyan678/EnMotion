import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const moduleNames = [
  "ScriptProcessor",
  "ArtDirection",
  "Cast",
  "StoryboardR2V",
  "ConsistencyVault",
  "StoryboardComposer",
] as const;

const titleKeys = [
  "scriptTitle",
  "styleTitle",
  "castTitle",
  "storyboardTitle",
  "vaultTitle",
  "storyboardComposerTitle",
] as const;

describe("Episode Editor step headers", () => {
  it.each(moduleNames)("removes the redundant visible title bar in %s", (moduleName) => {
    const source = readFileSync(
      resolve(process.cwd(), "src", "components", "modules", `${moduleName}.tsx`),
      "utf8",
    );

    expect(source).toContain('className="sr-only"');
    expect(source).not.toContain("<StepPageHeader");
    expect(source).not.toContain("<StepHeader");
    expect(source).not.toContain("StepPill");
    expect(source).not.toMatch(/\b(?:stepNumber|englishName|pills)=/);
    expect(source).not.toContain('subtitle={tStep(');
  });

  it.each([
    ["ScriptProcessor", "data-scroll-away-actions"],
    ["StoryboardComposer", "<ScrollFlowActions"],
    ["StoryboardR2V", "<ScrollFlowActions"],
  ])("keeps %s actions inside scroll-away content", (moduleName, marker) => {
    const source = readFileSync(
      resolve(process.cwd(), "src", "components", "modules", `${moduleName}.tsx`),
      "utf8",
    );
    expect(source).toContain(marker);
  });

  it("removes the obsolete decorative header implementation", () => {
    expect(
      existsSync(resolve(process.cwd(), "src", "components", "shared", "StepHeader.tsx")),
    ).toBe(false);
  });

  it.each(["VideoAssembly", "VideoGenerator"])(
    "does not reserve vertical space for a redundant title bar in %s",
    (moduleName) => {
      const contents = readFileSync(
        resolve(process.cwd(), "src", "components", "modules", `${moduleName}.tsx`),
        "utf8",
      );
      expect(contents).not.toContain("StepPageHeader");
      expect(contents).not.toContain('useTranslations("stepHeader")');
    },
  );

  it.each(["en", "zh"])("keeps only localized functional titles in %s", (locale) => {
    const messages = JSON.parse(
      readFileSync(resolve(process.cwd(), "messages", `${locale}.json`), "utf8"),
    ) as { stepHeader: Record<string, string> };

    expect(Object.keys(messages.stepHeader).sort()).toEqual([...titleKeys].sort());
    titleKeys.forEach((key) => expect(messages.stepHeader[key]).toBeTruthy());
  });
});
