import { describe, expect, it } from "vitest";

import {
  clipFrameType,
  clipImageId,
  clipStartImageVariants,
  frameTaskStatus,
  selectedClipStartImage,
  selectedStoryboardImage,
  storyboardImageVariants,
} from "@/lib/clipStartFrame";

describe("shot-specific clip start frames", () => {
  it("enumerates every unique variant and preserves an exact persisted selection", () => {
    const frame = {
      rendered_image_asset: {
        selected_id: "render-2",
        variants: [
          { id: "render-1", url: "storyboard/a.png" },
          { id: "render-2", url: "storyboard/b.png" },
        ],
      },
      image_asset: {
        selected_id: "draft-1",
        variants: [{ id: "draft-1", url: "storyboard/draft.png" }],
      },
      t2i_image_urls: ["storyboard/b.png", "uploads/custom.webp"],
      t2i_selected_index: 1,
      clip_start_image_id: clipImageId("uploads/custom.webp"),
      clip_start_image_url: "/files/uploads/custom.webp?token=temporary",
    };

    const variants = clipStartImageVariants(frame);

    expect(variants.map((variant) => variant.url)).toEqual([
      "storyboard/a.png",
      "storyboard/b.png",
      "storyboard/draft.png",
      "uploads/custom.webp",
    ]);
    expect(selectedClipStartImage(frame, variants)?.url).toBe("uploads/custom.webp");
  });

  it("uses stable identities for relative and served versions of one image", () => {
    expect(clipImageId("uploads/shot.png")).toBe(
      clipImageId("https://studio.example/files/uploads/shot.png?signature=short-lived"),
    );
  });

  it("uses the exact nested selected variant for legacy storyboard frames", () => {
    const frame = {
      rendered_image_asset: {
        selected_id: "render-selected",
        variants: [
          { id: "render-old", url: "storyboard/old.png" },
          { id: "render-selected", url: "/files/storyboard/selected.png?signature=old" },
        ],
      },
      rendered_image_url: "storyboard/old.png",
      t2i_image_urls: ["storyboard/t2i-old.png"],
      t2i_selected_index: 0,
    };

    expect(selectedStoryboardImage(frame)?.id).toBe("render-selected");
    expect(selectedStoryboardImage(frame)?.url).toContain("selected.png");
  });

  it("keeps storyboard artwork and the Motion clip-start selection distinct", () => {
    const frame = {
      rendered_image_asset: {
        selected_id: "render-selected",
        variants: [{ id: "render-selected", url: "storyboard/rendered.png" }],
      },
      image_asset: {
        selected_id: "uploaded-selected",
        variants: [{ id: "uploaded-selected", url: "uploads/chosen.webp" }],
      },
      clip_start_image_id: "uploaded-selected",
      clip_start_image_url: "https://studio.example/files/uploads/chosen.webp?token=temporary",
    };

    const variants = storyboardImageVariants(frame);
    expect(selectedClipStartImage(frame, variants)).toEqual(expect.objectContaining({
      id: "uploaded-selected",
      url: "uploads/chosen.webp",
      source: "storyboard",
    }));
    expect(selectedStoryboardImage(frame, variants)).toEqual(expect.objectContaining({
      id: "render-selected",
      url: "storyboard/rendered.png",
      source: "rendered",
    }));
  });

  it("returns no selection only when a storyboard frame genuinely has no image", () => {
    expect(storyboardImageVariants({})).toEqual([]);
    expect(selectedStoryboardImage({})).toBeNull();
  });

  it("normalizes translated camera movements to canonical frame types", () => {
    expect(clipFrameType({ camera_movement: "跟拍" })).toBe("follow");
    expect(clipFrameType({ camera_movement: "Push In" })).toBe("push_in");
    expect(clipFrameType({ camera_movement: "unknown movement" })).toBe("static");
  });

  it("keeps queued, processing, completed, and failed shot states distinct", () => {
    const tasks = [
      { frame_id: "shot-1", status: "completed", created_at: 1 },
      { frame_id: "shot-2", status: "failed", created_at: 2 },
      { frame_id: "shot-3", status: "pending", created_at: 3 },
      { frame_id: "shot-4", status: "processing", created_at: 4 },
    ];

    expect(frameTaskStatus(tasks, "shot-1")).toBe("completed");
    expect(frameTaskStatus(tasks, "shot-2")).toBe("failed");
    expect(frameTaskStatus(tasks, "shot-3")).toBe("queued");
    expect(frameTaskStatus(tasks, "shot-4")).toBe("processing");
    expect(frameTaskStatus(tasks, "shot-5")).toBeNull();
  });
});
