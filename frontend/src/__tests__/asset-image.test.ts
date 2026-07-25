import { describe, expect, it, vi } from "vitest";
import type { Character, Prop, Scene } from "@/store/projectStore";

vi.mock("@/lib/apiUrl", () => ({
  API_URL: "http://127.0.0.1:17177",
}));

import {
  primaryAssetDisplayUrl,
  primaryAssetImage,
  primaryAssetImageUrl,
} from "@/lib/assetImage";

describe("shared asset image resolution", () => {
  it("uses a character reference sheet before legacy image fields", () => {
    const character = {
      id: "tester",
      name: "Tester",
      reference_sheet: {
        selected_image_id: "selected",
        image_variants: [
          { id: "first", url: "assets/first.png", created_at: 1 },
          { id: "selected", url: "assets/tester.png", created_at: 2 },
        ],
      },
      full_body_image_url: "assets/legacy.png",
    } satisfies Character;

    expect(primaryAssetImageUrl(character, "character")).toBe("assets/tester.png");
    expect(primaryAssetImage(character, "character")).toEqual({
      selected_id: "selected",
      variants: character.reference_sheet.image_variants,
    });
    expect(primaryAssetDisplayUrl(character, "character")).toBe(
      "http://127.0.0.1:17177/files/assets/tester.png",
    );
  });

  it("falls back to the first character variant when the selected id is stale", () => {
    const character = {
      id: "tester",
      name: "Tester",
      reference_sheet: {
        selected_image_id: "missing",
        image_variants: [{ id: "first", url: "assets/first.png", created_at: 1 }],
      },
    } satisfies Character;

    expect(primaryAssetImageUrl(character, "character")).toBe("assets/first.png");
  });

  it.each([
    ["three-view AssetUnit", {
      three_views: {
        selected_image_id: "three-view",
        image_variants: [{ id: "three-view", url: "assets/three-view.png", created_at: 1 }],
      },
    }, "assets/three-view.png"],
    ["legacy three-view ImageAsset", {
      three_view_asset: {
        selected_id: "three-view",
        variants: [{ id: "three-view", url: "assets/legacy-three-view.png", created_at: 1 }],
      },
    }, "assets/legacy-three-view.png"],
    ["head-shot AssetUnit", {
      head_shot: {
        selected_image_id: "head-shot",
        image_variants: [{ id: "head-shot", url: "assets/head-shot.png", created_at: 1 }],
      },
    }, "assets/head-shot.png"],
    ["legacy head-shot ImageAsset", {
      headshot_asset: {
        selected_id: "head-shot",
        variants: [{ id: "head-shot", url: "assets/legacy-head-shot.png", created_at: 1 }],
      },
    }, "assets/legacy-head-shot.png"],
  ] as const)("keeps a %s readable when it is the only image container", (_label, fields, expected) => {
    const character = {
      id: "derived-only",
      name: "Derived only",
      ...fields,
    } as Character;

    expect(primaryAssetImageUrl(character, "character")).toBe(expected);
  });

  it("keeps legacy character and media URLs readable", () => {
    const character = {
      id: "legacy-character",
      name: "Legacy",
      avatar_url: "assets/avatar.png",
    } satisfies Character;
    const scene = {
      id: "legacy-scene",
      name: "Legacy scene",
      description: "",
      reference_image_url: "assets/reference.png",
    } as Scene & { reference_image_url: string };

    expect(primaryAssetImageUrl(character, "character")).toBe("assets/avatar.png");
    expect(primaryAssetImageUrl(scene, "scene")).toBe("assets/reference.png");
  });

  it.each([
    ["scene", { id: "scene-1", name: "Scene", description: "", image_asset: {
      selected_id: "scene-image",
      variants: [{ id: "scene-image", url: "assets/scene.png", created_at: 1 }],
    } } satisfies Scene],
    ["prop", { id: "prop-1", name: "Prop", description: "", image_asset: {
      selected_id: null,
      variants: [{ id: "prop-image", url: "https://cdn.example/prop.png", created_at: 1 }],
    } } satisfies Prop],
  ] as const)("resolves and normalizes %s image containers", (kind, asset) => {
    const url = primaryAssetDisplayUrl(asset, kind);
    expect(url).toBe(
      kind === "scene"
        ? "http://127.0.0.1:17177/files/assets/scene.png"
        : "https://cdn.example/prop.png",
    );
  });
});
