import {
    DEFAULT_ACTIVE_MODELS,
    getApprovedModels,
    normalizeActiveModel,
} from "@/lib/newApiModels";

export type DurationConfig =
    | { type: "slider"; min: number; max: number; step: number; default: number }
    | { type: "buttons"; options: number[]; default: number }
    | { type: "fixed"; value: number };

export interface ModelParamSupport {
    resolution?: { options: string[]; default: string };
    ratio?: { options: string[]; default: string };
    seed?: boolean;
    audio?: boolean;
    watermark?: boolean;
}

export interface I2VModelConfig {
    id: string;
    name: string;
    description: string;
    duration: DurationConfig;
    params: ModelParamSupport;
    badges?: string[];
    recommended?: boolean;
    family?: string;
    status?: string;
}

export interface SelectableModelOption {
    id: string;
    name: string;
    description: string;
    badges?: string[];
    recommended?: boolean;
    family?: string;
    status?: string;
}

export type ModelOption = SelectableModelOption;

export interface FrontendModelSettings {
    chat_model: string;
    image_model: string;
    video_model: string;
    /** Compatibility mirrors accepted by existing project documents. */
    t2i_model: string;
    i2i_model: string;
    i2v_model: string;
    character_aspect_ratio: string;
    scene_aspect_ratio: string;
    prop_aspect_ratio: string;
    storyboard_aspect_ratio: string;
}

type SelectionGroup = "chat" | "image" | "t2i" | "i2i" | "video" | "i2v";
type SettingsSurface = "project_settings" | "series_settings" | "global_settings";
type VisibilitySurface = SettingsSurface | "video_sidebar";

export const DEFAULT_MODEL_SETTINGS: FrontendModelSettings = Object.freeze({
    chat_model: DEFAULT_ACTIVE_MODELS.chat,
    image_model: DEFAULT_ACTIVE_MODELS.image,
    video_model: DEFAULT_ACTIVE_MODELS.video,
    t2i_model: DEFAULT_ACTIVE_MODELS.image,
    i2i_model: DEFAULT_ACTIVE_MODELS.image,
    i2v_model: DEFAULT_ACTIVE_MODELS.video,
    character_aspect_ratio: "3:4",
    scene_aspect_ratio: "16:9",
    prop_aspect_ratio: "1:1",
    storyboard_aspect_ratio: "16:9",
});

const APPROVED_CHAT_OPTIONS: SelectableModelOption[] = getApprovedModels("chat").map((model) => ({
    id: model.id,
    name: model.name,
    description: model.description,
    family: "New API",
    recommended: model.id === DEFAULT_ACTIVE_MODELS.chat,
    status: "active",
}));

const APPROVED_IMAGE_OPTIONS: SelectableModelOption[] = getApprovedModels("image").map((model) => ({
    id: model.id,
    name: model.name,
    description: model.description,
    family: "New API",
    recommended: true,
    status: "active",
}));

const APPROVED_VIDEO_OPTIONS: I2VModelConfig[] = getApprovedModels("video").map((model) => ({
    id: model.id,
    name: model.name,
    description: model.description,
    family: "New API",
    recommended: model.id === DEFAULT_ACTIVE_MODELS.video,
    status: "active",
    duration: { type: "slider", min: 4, max: 15, step: 1, default: 5 },
    params: {
        resolution: { options: ["720p"], default: "720p" },
        ratio: { options: ["16:9", "9:16", "1:1"], default: "16:9" },
        seed: true,
        audio: true,
        watermark: true,
    },
}));

export function resolveModelId(
    group: SelectionGroup,
    requestedId: string | null | undefined,
    _surface: VisibilitySurface,
): string {
    if (group === "chat") return normalizeActiveModel("chat", requestedId);
    if (group === "video" || group === "i2v") return normalizeActiveModel("video", requestedId);
    return normalizeActiveModel("image", requestedId);
}

export function resolveModelSettings(
    settings?: Partial<FrontendModelSettings> | null,
    surface: SettingsSurface = "project_settings",
): FrontendModelSettings {
    const imageCandidate = settings?.image_model ?? settings?.t2i_model ?? settings?.i2i_model;
    const videoCandidate = settings?.video_model ?? settings?.i2v_model;
    const imageModel = resolveModelId("image", imageCandidate, surface);
    const videoModel = resolveModelId("video", videoCandidate, surface);
    return {
        chat_model: resolveModelId("chat", settings?.chat_model, surface),
        image_model: imageModel,
        video_model: videoModel,
        t2i_model: imageModel,
        i2i_model: imageModel,
        i2v_model: videoModel,
        character_aspect_ratio: settings?.character_aspect_ratio || DEFAULT_MODEL_SETTINGS.character_aspect_ratio,
        scene_aspect_ratio: settings?.scene_aspect_ratio || DEFAULT_MODEL_SETTINGS.scene_aspect_ratio,
        prop_aspect_ratio: settings?.prop_aspect_ratio || DEFAULT_MODEL_SETTINGS.prop_aspect_ratio,
        storyboard_aspect_ratio: settings?.storyboard_aspect_ratio || DEFAULT_MODEL_SETTINGS.storyboard_aspect_ratio,
    };
}

export const normalizeModelSettings = resolveModelSettings;
export const normalizeModelId = resolveModelId;

export function getMaxReferenceImages(modelId?: string | null): number {
    return normalizeActiveModel("image", modelId) === DEFAULT_ACTIVE_MODELS.image ? 16 : 0;
}

export const CHAT_MODELS = APPROVED_CHAT_OPTIONS;
export const PROJECT_CHAT_MODELS = APPROVED_CHAT_OPTIONS;
export const SERIES_CHAT_MODELS = APPROVED_CHAT_OPTIONS;
export const GLOBAL_CHAT_MODELS = APPROVED_CHAT_OPTIONS;

export const PROJECT_T2I_MODELS = APPROVED_IMAGE_OPTIONS;
export const SERIES_T2I_MODELS = APPROVED_IMAGE_OPTIONS;
export const GLOBAL_T2I_MODELS = APPROVED_IMAGE_OPTIONS;
export const PROJECT_I2I_MODELS = APPROVED_IMAGE_OPTIONS;
export const SERIES_I2I_MODELS = APPROVED_IMAGE_OPTIONS;
export const GLOBAL_I2I_MODELS = APPROVED_IMAGE_OPTIONS;
export const PROJECT_IMAGE_MODELS = APPROVED_IMAGE_OPTIONS;
export const SERIES_IMAGE_MODELS = APPROVED_IMAGE_OPTIONS;
export const GLOBAL_IMAGE_MODELS = APPROVED_IMAGE_OPTIONS;

export const PROJECT_I2V_MODELS = APPROVED_VIDEO_OPTIONS;
export const SERIES_I2V_MODELS = APPROVED_VIDEO_OPTIONS;
export const GLOBAL_I2V_MODELS = APPROVED_VIDEO_OPTIONS;
export const VIDEO_I2V_MODELS = APPROVED_VIDEO_OPTIONS;

export const T2I_MODELS = PROJECT_T2I_MODELS;
export const I2I_MODELS = PROJECT_I2I_MODELS;
export const IMAGE_MODELS = PROJECT_IMAGE_MODELS;
export const I2V_MODELS = PROJECT_I2V_MODELS;
export const VIDEO_SIDEBAR_I2V_MODELS = VIDEO_I2V_MODELS;
export const DEFAULT_I2V_MODEL_ID = DEFAULT_ACTIVE_MODELS.video;
