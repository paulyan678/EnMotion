/**
 * The complete model allow-list exposed by the EnMotion UI.
 *
 * API keys deliberately live outside this registry.  The browser sends only
 * the selected model id; the backend owns model -> credential resolution.
 */
export type NewApiCapability = "chat" | "image" | "video";

export type NewApiSecretField =
  | "NEWAPI_GPT_IMAGE_2_API_KEY"
  | "NEWAPI_SEEDANCE_2_API_KEY"
  | "NEWAPI_SEEDANCE_2_FAST_API_KEY"
  | "NEWAPI_SEEDANCE_2_MINI_API_KEY"
  | "NEWAPI_DEEPSEEK_V4_FLASH_API_KEY"
  | "NEWAPI_QWEN_37_MAX_API_KEY"
  | "NEWAPI_DEEPSEEK_V4_PRO_API_KEY";

export interface ApprovedNewApiModel {
  id: string;
  name: string;
  capability: NewApiCapability;
  description: string;
  secretField: NewApiSecretField;
  capabilities: readonly string[];
}

export const MODEL_TRANSLATION_KEYS: Record<string, string> = {
  "deepseek-v4-flash": "deepseekFlash",
  "qwen3.7-max": "qwenMax",
  "deepseek-v4-pro": "deepseekPro",
  "gpt-image-2": "gptImage",
  "doubao-seedance-2-0-260128": "seedance",
  "doubao-seedance-2-0-fast-260128": "seedanceFast",
  "doubao-seedance-2-0-mini-260615": "seedanceMini",
};

export function getModelTranslationKey(modelId: string): string | undefined {
  return MODEL_TRANSLATION_KEYS[modelId];
}

export const APPROVED_NEWAPI_MODELS = [
  {
    id: "deepseek-v4-flash",
    name: "DeepSeek V4 Flash",
    capability: "chat",
    description: "适用于快速对话与提示词处理。",
    secretField: "NEWAPI_DEEPSEEK_V4_FLASH_API_KEY",
    capabilities: ["chat"],
  },
  {
    id: "qwen3.7-max",
    name: "Qwen 3.7 Max",
    capability: "chat",
    description: "高能力对话模型。",
    secretField: "NEWAPI_QWEN_37_MAX_API_KEY",
    capabilities: ["chat"],
  },
  {
    id: "deepseek-v4-pro",
    name: "DeepSeek V4 Pro",
    capability: "chat",
    description: "适用于高级推理与提示词处理。",
    secretField: "NEWAPI_DEEPSEEK_V4_PRO_API_KEY",
    capabilities: ["chat"],
  },
  {
    id: "gpt-image-2",
    name: "GPT Image 2",
    capability: "image",
    description: "适用于图像生成与图像编辑。",
    secretField: "NEWAPI_GPT_IMAGE_2_API_KEY",
    capabilities: ["t2i", "i2i"],
  },
  {
    id: "doubao-seedance-2-0-260128",
    name: "Seedance 2.0",
    capability: "video",
    description: "适用于文生视频与图生视频。",
    secretField: "NEWAPI_SEEDANCE_2_API_KEY",
    capabilities: ["t2v", "i2v"],
  },
  {
    id: "doubao-seedance-2-0-fast-260128",
    name: "Seedance 2.0 Fast",
    capability: "video",
    description: "速度更快的文生视频与图生视频模型。",
    secretField: "NEWAPI_SEEDANCE_2_FAST_API_KEY",
    capabilities: ["t2v", "i2v"],
  },
  {
    id: "doubao-seedance-2-0-mini-260615",
    name: "Seedance 2.0 Mini",
    capability: "video",
    description: "高效的文生视频与图生视频模型。",
    secretField: "NEWAPI_SEEDANCE_2_MINI_API_KEY",
    capabilities: ["t2v", "i2v"],
  },
] as const satisfies readonly ApprovedNewApiModel[];

export const DEFAULT_ACTIVE_MODELS = Object.freeze({
  chat: "deepseek-v4-flash",
  image: "gpt-image-2",
  video: "doubao-seedance-2-0-fast-260128",
});

export const NEWAPI_SECRET_FIELDS = APPROVED_NEWAPI_MODELS.map(
  (model) => model.secretField,
) as NewApiSecretField[];

export function getApprovedModels(capability: NewApiCapability): ApprovedNewApiModel[] {
  return APPROVED_NEWAPI_MODELS.filter((model) => model.capability === capability);
}

export function getApprovedModel(modelId?: string | null): ApprovedNewApiModel | undefined {
  return APPROVED_NEWAPI_MODELS.find((model) => model.id === modelId);
}

export function getApprovedModelBySecretField(
  secretField?: NewApiSecretField | null,
): ApprovedNewApiModel | undefined {
  return APPROVED_NEWAPI_MODELS.find((model) => model.secretField === secretField);
}

export function isApprovedModelForCapability(
  modelId: string | null | undefined,
  capability: NewApiCapability,
): boolean {
  return getApprovedModels(capability).some((model) => model.id === modelId);
}

export function normalizeActiveModel(
  capability: NewApiCapability,
  modelId?: string | null,
): string {
  return isApprovedModelForCapability(modelId, capability)
    ? modelId!
    : DEFAULT_ACTIVE_MODELS[capability];
}

export function getSecretFieldForModel(
  modelId: string,
  capability?: NewApiCapability,
): NewApiSecretField | undefined {
  const model = getApprovedModel(modelId);
  if (!model || (capability && model.capability !== capability)) return undefined;
  return model.secretField;
}

/** Masks and redacted placeholders are display metadata, never credentials. */
export function isMaskedSecretValue(value?: string | null): boolean {
  if (!value) return false;
  const trimmed = value.trim();
  return (
    trimmed.includes("•") ||
    trimmed.includes("*") ||
    /^<redacted>$/i.test(trimmed) ||
    /^masked:/i.test(trimmed)
  );
}

export function configuredSecretFields(
  payload?: Record<string, unknown> | null,
): Record<NewApiSecretField, boolean> {
  const configured = (payload?.secrets_configured ?? {}) as Record<string, unknown>;
  return Object.fromEntries(
    NEWAPI_SECRET_FIELDS.map((field) => [
      field,
      configured[field] === true ||
        (typeof payload?.[field] === "string" && Boolean((payload[field] as string).trim())),
    ]),
  ) as Record<NewApiSecretField, boolean>;
}

/**
 * Build a save patch from replacement inputs. Empty and masked values mean
 * "leave the saved secret unchanged" and are omitted from the request.
 */
export function buildSecretReplacementPatch(
  replacements: Partial<Record<NewApiSecretField, string>>,
): Partial<Record<NewApiSecretField, string>> {
  const patch: Partial<Record<NewApiSecretField, string>> = {};
  for (const field of NEWAPI_SECRET_FIELDS) {
    const value = replacements[field]?.trim();
    if (value && !isMaskedSecretValue(value)) patch[field] = value;
  }
  return patch;
}

export interface ActiveNewApiSelection {
  chat: string;
  image: string;
  video: string;
}

export function normalizeActiveSelection(input?: Partial<ActiveNewApiSelection> | null): ActiveNewApiSelection {
  return {
    chat: normalizeActiveModel("chat", input?.chat),
    image: normalizeActiveModel("image", input?.image),
    video: normalizeActiveModel("video", input?.video),
  };
}

export function getNewApiValidationErrors(
  baseUrl: string,
  selection: ActiveNewApiSelection,
  configured: Partial<Record<NewApiSecretField, boolean>>,
  replacements: Partial<Record<NewApiSecretField, string>> = {},
): string[] {
  const errors: string[] = [];
  if (!baseUrl.trim()) errors.push("NEWAPI_BASE_URL");

  for (const capability of ["chat", "image", "video"] as const) {
    const modelId = selection[capability];
    if (!isApprovedModelForCapability(modelId, capability)) {
      errors.push(`Unsupported ${capability} model`);
      continue;
    }
    const secretField = getSecretFieldForModel(modelId, capability)!;
    const replacement = replacements[secretField]?.trim();
    if (!configured[secretField] && (!replacement || isMaskedSecretValue(replacement))) {
      errors.push(secretField);
    }
  }
  return errors;
}
