import type { ModelSettingsUpdatePayload } from "@/lib/api";
import type { FrontendModelSettings } from "@/lib/modelCatalog";

export const CANONICAL_MODEL_SETTING_FIELDS = [
  "chat_model",
  "image_model",
  "video_model",
  "character_aspect_ratio",
  "scene_aspect_ratio",
  "prop_aspect_ratio",
  "storyboard_aspect_ratio",
] as const;

export type CanonicalModelSettingField =
  (typeof CANONICAL_MODEL_SETTING_FIELDS)[number];

export function normalizeModelSettingOverrides(
  fields?: readonly string[] | null,
): CanonicalModelSettingField[] {
  const supplied = new Set(fields ?? []);
  return CANONICAL_MODEL_SETTING_FIELDS.filter((field) => supplied.has(field));
}

export function setModelSettingOverride(
  fields: readonly CanonicalModelSettingField[],
  field: CanonicalModelSettingField,
  enabled: boolean,
): CanonicalModelSettingField[] {
  const next = new Set(fields);
  if (enabled) next.add(field);
  else next.delete(field);
  return CANONICAL_MODEL_SETTING_FIELDS.filter((candidate) => next.has(candidate));
}

export function buildModelSettingsPatch(
  initialSettings: FrontendModelSettings,
  initialOverrides: readonly CanonicalModelSettingField[],
  draftSettings: FrontendModelSettings,
  draftOverrides: readonly CanonicalModelSettingField[],
): ModelSettingsUpdatePayload {
  const initialOwned = new Set(initialOverrides);
  const draftOwned = new Set(draftOverrides);
  const patch: ModelSettingsUpdatePayload = {};

  for (const field of CANONICAL_MODEL_SETTING_FIELDS) {
    if (initialOwned.has(field) && !draftOwned.has(field)) {
      patch[field] = null;
    } else if (
      draftOwned.has(field) &&
      (!initialOwned.has(field) || draftSettings[field] !== initialSettings[field])
    ) {
      patch[field] = draftSettings[field];
    }
  }

  return patch;
}
