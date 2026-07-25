import { describe, expect, it } from "vitest";

import { DEFAULT_I2V_MODEL_ID, VIDEO_I2V_MODELS } from "@/lib/modelCatalog";

const VIDEO_IDS = [
  "doubao-seedance-2-0-260128",
  "doubao-seedance-2-0-fast-260128",
  "doubao-seedance-2-0-mini-260615",
];

describe("approved Seedance video parameters", () => {
  it("contains only the three exact New API model IDs", () => {
    expect(VIDEO_I2V_MODELS.map((model) => model.id)).toEqual(VIDEO_IDS);
    expect(DEFAULT_I2V_MODEL_ID).toBe("doubao-seedance-2-0-fast-260128");
  });

  it("supports only the strict New API request controls", () => {
    for (const model of VIDEO_I2V_MODELS) {
      expect(model.duration).toEqual({
        type: "slider",
        min: 4,
        max: 15,
        step: 1,
        default: 5,
      });
      expect(model.params.resolution).toEqual({
        options: ["720p"],
        default: "720p",
      });
      expect(model.params.ratio?.options).toEqual(["16:9", "9:16", "1:1"]);
      expect(model.params.seed).toBe(true);
      expect(model.params.audio).toBe(true);
      expect(model.params.watermark).toBe(true);
    }
  });

  it("does not include provider-specific or reference-video controls", () => {
    for (const model of VIDEO_I2V_MODELS) {
      const params = model.params as Record<string, unknown>;
      expect(params).not.toHaveProperty("mode");
      expect(params).not.toHaveProperty("cfgScale");
      expect(params).not.toHaveProperty("viduAudio");
      expect(params).not.toHaveProperty("movementAmplitude");
      expect(params).not.toHaveProperty("referenceVideo");
      expect(params).not.toHaveProperty("r2v");
    }
  });

  it("advertises I2V descriptions without claiming multi-reference R2V", () => {
    for (const model of VIDEO_I2V_MODELS) {
      const text = `${model.name} ${model.description}`.toLowerCase();
      expect(text).not.toContain("r2v");
      expect(text).not.toContain("multi-reference");
      expect(text).not.toContain("kling");
      expect(text).not.toContain("vidu");
      expect(text).not.toContain("wan");
    }
  });
});
