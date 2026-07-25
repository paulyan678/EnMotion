import { describe, expect, it } from "vitest";

import {
  APPROVED_NEWAPI_MODELS,
  DEFAULT_ACTIVE_MODELS,
  NEWAPI_SECRET_FIELDS,
  buildSecretReplacementPatch,
  configuredSecretFields,
  getNewApiValidationErrors,
  getSecretFieldForModel,
  isMaskedSecretValue,
  normalizeActiveSelection,
} from "@/lib/newApiModels";

describe("New API credentials", () => {
  it("maps every approved model to one unique model-specific key", () => {
    expect(APPROVED_NEWAPI_MODELS).toHaveLength(7);
    expect(new Set(APPROVED_NEWAPI_MODELS.map((model) => model.id)).size).toBe(7);
    expect(new Set(APPROVED_NEWAPI_MODELS.map((model) => model.secretField)).size).toBe(7);
    expect(NEWAPI_SECRET_FIELDS).toHaveLength(7);

    for (const model of APPROVED_NEWAPI_MODELS) {
      expect(getSecretFieldForModel(model.id, model.capability)).toBe(model.secretField);
    }
  });

  it("never returns a key for a mismatched capability or unsupported model", () => {
    expect(getSecretFieldForModel("gpt-image-2", "video")).toBeUndefined();
    expect(getSecretFieldForModel("doubao-seedance-2-0-fast-260128", "chat")).toBeUndefined();
    expect(getSecretFieldForModel("kling-v3", "video")).toBeUndefined();
  });

  it("uses backend boolean status without exposing saved key contents", () => {
    const configured = configuredSecretFields({
      secrets_configured: {
        NEWAPI_GPT_IMAGE_2_API_KEY: true,
        NEWAPI_DEEPSEEK_V4_FLASH_API_KEY: false,
      },
      NEWAPI_GPT_IMAGE_2_API_KEY: "••••last4",
    });
    expect(configured.NEWAPI_GPT_IMAGE_2_API_KEY).toBe(true);
    expect(configured.NEWAPI_DEEPSEEK_V4_FLASH_API_KEY).toBe(false);
    expect(isMaskedSecretValue("••••last4")).toBe(true);
    expect(isMaskedSecretValue("********last4")).toBe(true);
  });

  it("omits empty and masked replacement inputs from save payloads", () => {
    expect(buildSecretReplacementPatch({
      NEWAPI_GPT_IMAGE_2_API_KEY: "••••last4",
      NEWAPI_SEEDANCE_2_API_KEY: "   ",
      NEWAPI_SEEDANCE_2_FAST_API_KEY: "new-fast-key",
    })).toEqual({
      NEWAPI_SEEDANCE_2_FAST_API_KEY: "new-fast-key",
    });
  });

  it("validates the exact selected model key and does not accept another model key", () => {
    const selection = {
      chat: DEFAULT_ACTIVE_MODELS.chat,
      image: DEFAULT_ACTIVE_MODELS.image,
      video: DEFAULT_ACTIVE_MODELS.video,
    };
    const configured = Object.fromEntries(NEWAPI_SECRET_FIELDS.map((field) => [field, false]));
    configured.NEWAPI_SEEDANCE_2_API_KEY = true;
    configured.NEWAPI_GPT_IMAGE_2_API_KEY = true;
    configured.NEWAPI_DEEPSEEK_V4_FLASH_API_KEY = true;

    expect(getNewApiValidationErrors("https://new-api.example/v1", selection, configured))
      .toContain("NEWAPI_SEEDANCE_2_FAST_API_KEY");

    configured.NEWAPI_SEEDANCE_2_FAST_API_KEY = true;
    expect(getNewApiValidationErrors("https://new-api.example/v1", selection, configured)).toEqual([]);
  });

  it("requires NEWAPI_BASE_URL and rejects unsupported stale selections", () => {
    const configured = Object.fromEntries(NEWAPI_SECRET_FIELDS.map((field) => [field, true]));
    expect(getNewApiValidationErrors("", {
      chat: "obsolete-chat",
      image: "obsolete-image",
      video: "obsolete-video",
    }, configured)).toEqual([
      "NEWAPI_BASE_URL",
      "Unsupported chat model",
      "Unsupported image model",
      "Unsupported video model",
    ]);
  });

  it("normalizes stale persisted selections to the approved defaults", () => {
    expect(normalizeActiveSelection({
      chat: "dashscope",
      image: "wan2.7-image",
      video: "vidu-q3-r2v",
    })).toEqual(DEFAULT_ACTIVE_MODELS);
  });
});
