import { describe, expect, it } from "vitest";

import {
  CHAT_MODELS,
  DEFAULT_I2V_MODEL_ID,
  DEFAULT_MODEL_SETTINGS,
  IMAGE_MODELS,
  I2V_MODELS,
  resolveModelId,
  resolveModelSettings,
} from "@/lib/modelCatalog";
import { DEFAULT_ACTIVE_MODELS } from "@/lib/newApiModels";

const CHAT_IDS = ["deepseek-v4-flash", "qwen3.7-max", "deepseek-v4-pro"];
const IMAGE_IDS = ["gpt-image-2"];
const VIDEO_IDS = [
  "doubao-seedance-2-0-260128",
  "doubao-seedance-2-0-fast-260128",
  "doubao-seedance-2-0-mini-260615",
];

describe("New API-only model catalog", () => {
  it("exposes exactly the approved models by capability", () => {
    expect(CHAT_MODELS.map((model) => model.id)).toEqual(CHAT_IDS);
    expect(IMAGE_MODELS.map((model) => model.id)).toEqual(IMAGE_IDS);
    expect(I2V_MODELS.map((model) => model.id)).toEqual(VIDEO_IDS);
  });

  it("uses the required independent defaults", () => {
    expect(DEFAULT_ACTIVE_MODELS).toEqual({
      chat: "deepseek-v4-flash",
      image: "gpt-image-2",
      video: "doubao-seedance-2-0-fast-260128",
    });
    expect(DEFAULT_MODEL_SETTINGS.chat_model).toBe(DEFAULT_ACTIVE_MODELS.chat);
    expect(DEFAULT_MODEL_SETTINGS.image_model).toBe(DEFAULT_ACTIVE_MODELS.image);
    expect(DEFAULT_MODEL_SETTINGS.video_model).toBe(DEFAULT_ACTIVE_MODELS.video);
    expect(DEFAULT_I2V_MODEL_ID).toBe(DEFAULT_ACTIVE_MODELS.video);
  });

  it("filters unsupported IDs and stale saved settings to capability defaults", () => {
    expect(resolveModelId("chat", "wan2.7-image", "global_settings")).toBe(DEFAULT_ACTIVE_MODELS.chat);
    expect(resolveModelId("image", "qwen-image-2.0", "project_settings")).toBe(DEFAULT_ACTIVE_MODELS.image);
    expect(resolveModelId("video", "kling-v3-r2v", "video_sidebar")).toBe(DEFAULT_ACTIVE_MODELS.video);

    expect(resolveModelSettings({
      chat_model: "dashscope-qwen",
      image_model: "mulerouter-image",
      video_model: "vidu-q3-pro-r2v",
    })).toMatchObject({
      chat_model: DEFAULT_ACTIVE_MODELS.chat,
      image_model: DEFAULT_ACTIVE_MODELS.image,
      video_model: DEFAULT_ACTIVE_MODELS.video,
      t2i_model: DEFAULT_ACTIVE_MODELS.image,
      i2i_model: DEFAULT_ACTIVE_MODELS.image,
      i2v_model: DEFAULT_ACTIVE_MODELS.video,
    });
  });

  it("preserves valid switched chat and video choices through serialization", () => {
    const saved = JSON.stringify({
      chat_model: "deepseek-v4-pro",
      image_model: "gpt-image-2",
      video_model: "doubao-seedance-2-0-mini-260615",
    });
    const restored = resolveModelSettings(JSON.parse(saved));
    expect(restored.chat_model).toBe("deepseek-v4-pro");
    expect(restored.image_model).toBe("gpt-image-2");
    expect(restored.video_model).toBe("doubao-seedance-2-0-mini-260615");
  });

  it("does not advertise reference-to-video capability", () => {
    const allDescriptions = [...CHAT_MODELS, ...IMAGE_MODELS, ...I2V_MODELS]
      .map((model) => `${model.id} ${model.description}`.toLowerCase())
      .join(" ");
    expect(allDescriptions).not.toContain("reference-to-video");
    expect(allDescriptions).not.toContain("r2v");
  });
});
