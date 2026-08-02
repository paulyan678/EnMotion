import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = resolve(process.cwd(), "src", "components");
const source = (path: string) => readFileSync(resolve(root, path), "utf8");

const launchers = [
  "library/AssetLibraryPage.tsx",
  "library/LibraryAssetEditor.tsx",
  "series/SeriesDetailPage.tsx",
  "modules/ConsistencyVault.tsx",
  "modules/Cast.tsx",
  "modules/storyboard-r2v/AssetDrawer.tsx",
  "modules/storyboard-r2v/AssetChipBar.tsx",
];

describe("canonical Asset Editor architecture", () => {
  it("uses one public editor entry at every canonical asset surface", () => {
    for (const path of launchers) {
      expect(source(path), path).toContain("SharedAssetEditor");
    }
  });

  it("keeps workbench implementations private to the canonical editor", () => {
    const shared = source("assets/SharedAssetEditor.tsx");
    expect(shared).toContain("CharacterWorkbench");
    expect(shared).toContain("ScenePropWorkbench");

    for (const path of launchers) {
      const contents = source(path);
      expect(contents, path).not.toMatch(/<CharacterWorkbench\b/);
      expect(contents, path).not.toMatch(/<ScenePropWorkbench\b/);
    }
  });

  it("does not restore the retired R2V editor or feature-owned API orchestration", () => {
    expect(existsSync(resolve(root, "modules/cast/CastWorkbenchModal.tsx"))).toBe(false);

    for (const path of [
      "library/LibraryAssetEditor.tsx",
      "modules/ConsistencyVault.tsx",
      "modules/Cast.tsx",
    ]) {
      const contents = source(path);
      expect(contents, path).not.toMatch(
        /api\.(?:getOwnedAsset|updateOwnedAsset|generateOwnedAsset|selectOwnedAssetVariant|deleteOwnedAssetVariant|favoriteOwnedAssetVariant)/,
      );
    }
  });

  it("leaves raw Playground media selection outside the domain editor", () => {
    const playgroundPicker = source("modules/playground/AssetPickerModal.tsx");
    expect(playgroundPicker).not.toContain("SharedAssetEditor");
  });

  it("keeps owner-scope implementation details out of the editor chrome", () => {
    expect(source("assets/SharedAssetEditor.tsx")).not.toContain("ownerNotice");
    expect(source("assets/AssetEditorShell.tsx")).not.toContain("ownerNotice");
  });
});
