import { beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiUrl", () => ({
  API_URL: "http://127.0.0.1:17177",
}));

let getAssetUrl: typeof import("@/lib/utils").getAssetUrl;

beforeAll(async () => {
  ({ getAssetUrl } = await import("@/lib/utils"));
});

describe("getAssetUrl", () => {
  it.each([
    ["assets/scene.png", "http://127.0.0.1:17177/files/assets/scene.png"],
    ["/assets/scene.png", "http://127.0.0.1:17177/files/assets/scene.png"],
    ["output/assets/scene.png", "http://127.0.0.1:17177/files/assets/scene.png"],
    ["/outputs/assets/scene.png", "http://127.0.0.1:17177/files/assets/scene.png"],
    ["files/assets/scene.png", "http://127.0.0.1:17177/files/assets/scene.png"],
    ["/files/assets/scene.png", "http://127.0.0.1:17177/files/assets/scene.png"],
    [
      "/app/output/workspaces/workspace-1/output/playground/images/result.png",
      "http://127.0.0.1:17177/files/playground/images/result.png",
    ],
    [
      "C:\\EnMotion\\output\\playground\\images\\result.png",
      "http://127.0.0.1:17177/files/playground/images/result.png",
    ],
  ])("normalizes persisted local path %s", (input, expected) => {
    expect(getAssetUrl(input)).toBe(expected);
  });

  it.each([
    "https://cdn.example/scene.png",
    "http://cdn.example/scene.png",
    "//cdn.example/scene.png",
    "blob:https://studio.example/id",
    "data:image/png;base64,AAAA",
  ])("preserves supported direct URL %s", (input) => {
    expect(getAssetUrl(input)).toBe(input);
  });

  it.each([
    [
      "https://old-studio.example/files/storyboard/scene.png?token=expired",
      "http://127.0.0.1:17177/files/storyboard/scene.png",
    ],
    [
      "https://old-studio.example/api-proxy/files/uploads/scene.webp?signature=expired",
      "http://127.0.0.1:17177/files/uploads/scene.webp",
    ],
    [
      "//old-studio.example/files/assets/scene.png",
      "http://127.0.0.1:17177/files/assets/scene.png",
    ],
  ])("rebases historical authenticated media URL %s", (input, expected) => {
    expect(getAssetUrl(input)).toBe(expected);
  });

  it("keeps a malformed percent in a legacy authenticated filename usable", () => {
    expect(getAssetUrl("https://old-studio.example/files/uploads/100%real.png?expired=1")).toBe(
      "http://127.0.0.1:17177/files/uploads/100%real.png",
    );
  });

  it("removes stale signatures from restored Playground file URLs", () => {
    expect(
      getAssetUrl(
        "https://studio-old.example/files/playground/images/result.png?token=expired#fragment",
      ),
    ).toBe("http://127.0.0.1:17177/files/playground/images/result.png");
  });

  it("does not mistake a local name beginning with http for a remote URL", () => {
    expect(getAssetUrl("http-cache/scene.png")).toBe(
      "http://127.0.0.1:17177/files/http-cache/scene.png",
    );
  });

  it("returns an empty URL for absent paths", () => {
    expect(getAssetUrl(undefined)).toBe("");
    expect(getAssetUrl(null)).toBe("");
    expect(getAssetUrl("  ")).toBe("");
  });
});
