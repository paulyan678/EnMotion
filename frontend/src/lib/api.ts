import { apiClient as axios, apiFetch } from "@/lib/httpClient";
import { API_URL } from "@/lib/apiUrl";
import type { FrameMovementType } from "@/lib/frameMovement";
import type { NewApiSecretField } from "@/lib/newApiModels";
import { notifyAssetUsageChanged } from "@/lib/assetLibrarySync";
import { authApi, type AccountUsageItem } from "@/lib/authApi";
import { isHybridModeEnabled } from "@/lib/serverMode";
import { getAssetUrl } from "@/lib/utils";
import { apiTimestampMilliseconds } from "@/lib/dateTime";
import { readWorkspaceItem } from "@/lib/workspaceStorage";
export { API_URL } from "@/lib/apiUrl";

export const SERIES_MODEL_SETTINGS_TIMEOUT_MS = 30_000;

export interface EnvConfigPayload {
    NEWAPI_BASE_URL?: string;
    NEWAPI_CHAT_MODEL?: string;
    NEWAPI_IMAGE_MODEL?: string;
    NEWAPI_VIDEO_MODEL?: string;
    NEWAPI_GPT_IMAGE_2_API_KEY?: string;
    NEWAPI_SEEDANCE_2_API_KEY?: string;
    NEWAPI_SEEDANCE_2_FAST_API_KEY?: string;
    NEWAPI_SEEDANCE_2_MINI_API_KEY?: string;
    NEWAPI_DEEPSEEK_V4_FLASH_API_KEY?: string;
    NEWAPI_QWEN_37_MAX_API_KEY?: string;
    NEWAPI_DEEPSEEK_V4_PRO_API_KEY?: string;
    // Secrets from GET are masked (bullets + last 4 chars). This map reports
    // which credential fields are actually configured on the backend.
    secrets_configured?: Partial<Record<NewApiSecretField, boolean>>;
    [key: string]: string | Record<string, string> | Record<string, boolean> | boolean | undefined;
}

export interface ApiKeyInspectionItem {
    display_name: string;
    capability: "chat" | "image" | "video";
    api_key_field: NewApiSecretField;
    configured: boolean;
    active: boolean;
    in_use: boolean;
    value: string;
}

export interface ApiKeyInspectionPayload {
    revealed: boolean;
    items: ApiKeyInspectionItem[];
}

export interface ModelSettingsPayload {
    chat_model?: string;
    image_model?: string;
    video_model?: string;
    t2i_model?: string;
    i2i_model?: string;
    i2v_model?: string;
    character_aspect_ratio?: string;
    scene_aspect_ratio?: string;
    prop_aspect_ratio?: string;
    storyboard_aspect_ratio?: string;
}

export type ModelSettingsUpdatePayload = {
    [Field in keyof ModelSettingsPayload]?: ModelSettingsPayload[Field] | null;
};

export interface EffectiveModelSettingsPayload extends ModelSettingsPayload {
    model_settings_overrides?: string[];
    inherited_model_settings?: ModelSettingsPayload;
}

export interface PromptConfigPayload {
    storyboard_polish?: string;
    video_polish?: string;
    entity_extraction?: string;
    style_analysis?: string;
    storyboard_extraction?: string;
    polish_model?: string;
}

const DEFAULT_MODEL_SETTINGS_KEY = "enmotion_default_model_settings";
const DEFAULT_PROMPT_CONFIG_KEY = "enmotion_default_prompt_config";

function workspaceDefaultModelSettings(): ModelSettingsPayload | undefined {
    try {
        const raw = readWorkspaceItem(DEFAULT_MODEL_SETTINGS_KEY);
        if (!raw) return undefined;
        const parsed = JSON.parse(raw) as ModelSettingsPayload;
        const candidate: ModelSettingsPayload = {
            chat_model: parsed.chat_model,
            image_model: parsed.image_model ?? parsed.t2i_model ?? parsed.i2i_model,
            video_model: parsed.video_model ?? parsed.i2v_model,
            character_aspect_ratio: parsed.character_aspect_ratio,
            scene_aspect_ratio: parsed.scene_aspect_ratio,
            prop_aspect_ratio: parsed.prop_aspect_ratio,
            storyboard_aspect_ratio: parsed.storyboard_aspect_ratio,
        };
        const present = Object.fromEntries(
            Object.entries(candidate).filter(
                ([, value]) => typeof value === "string" && value.trim().length > 0,
            ),
        ) as ModelSettingsPayload;
        return Object.keys(present).length > 0 ? present : undefined;
    } catch {
        return undefined;
    }
}

function workspaceDefaultPromptConfig(): PromptConfigPayload | undefined {
    try {
        const raw = readWorkspaceItem(DEFAULT_PROMPT_CONFIG_KEY);
        if (!raw) return undefined;
        const parsed = JSON.parse(raw) as PromptConfigPayload;
        const candidate: PromptConfigPayload = {};
        for (const field of [
            "storyboard_polish",
            "video_polish",
            "entity_extraction",
            "style_analysis",
            "storyboard_extraction",
            "polish_model",
        ] as const) {
            const value = parsed[field];
            if (typeof value === "string" && value.trim()) {
                candidate[field] = value;
            }
        }
        return Object.keys(candidate).length > 0 ? candidate : undefined;
    } catch {
        return undefined;
    }
}

export interface ImportFileConfirmResponse {
    series: {
        id: string;
    };
    episodes: Array<{
        id: string;
    }>;
}

export interface DurableJobMarker {
    task_id: string;
    status: "queued" | "running";
}

export interface DurableJobStatus {
    task_id: string;
    status: "queued" | "running" | "completed" | "failed" | "canceled";
    progress?: number;
    error?: string | null;
    result?: Record<string, unknown> | null;
}

export type AssetOwnerKind = "project" | "series" | "global";
export type EditableAssetType = "character" | "scene" | "prop";

export interface AssetMetadataPatch {
    attributes: Record<string, string | number | boolean | null | undefined>;
    prompts?: Record<string, string>;
    target_asset_type?: EditableAssetType;
}

export interface AssetGenerationRequest {
    generation_type?: string;
    prompt: string;
    apply_style?: boolean;
    negative_prompt?: string;
    batch_size?: number;
    model_name?: string;
    aspect_ratio?: string;
    template_id?: string;
}

export interface AssetMotionGenerationRequest {
    motion_type: string;
    prompt: string;
    duration: number;
    batch_size?: number;
    model?: string;
    audio_url?: string;
}

export interface AssetDeleteReference {
    reference_type: "storyboard" | "generation_task" | string;
    owner_kind: AssetOwnerKind | string;
    owner_id: string;
    owner_title?: string | null;
    frame_id?: string | null;
    task_id?: string | null;
    task_status?: string | null;
    job_type?: string | null;
}

export interface AssetDeleteImpact {
    source_kind: AssetOwnerKind;
    source_id: string;
    asset_type: EditableAssetType;
    asset_id: string;
    asset_name: string;
    references: AssetDeleteReference[];
    reference_count: number;
    has_references: boolean;
}

export interface AssetDeleteResult {
    status: "deleted";
    source_kind: AssetOwnerKind;
    source_id: string;
    asset_type: EditableAssetType;
    id: string;
    reference_count: number;
    reclaimed_media_files: number;
}

export type ApiCallStatus = "queued" | "running" | "completed" | "failed" | "canceled";
export type ApiCallCategory = "text" | "image" | "video" | "other";
export type ApiCallSource = "playground" | "workspace" | "library";

export interface ApiCallProgressStep {
    id: string;
    state: "pending" | "active" | "completed" | "failed";
    started_at?: string | null;
    finished_at?: string | null;
    message?: string | null;
}

export interface ApiCallMedia {
    id: string;
    media_type: "image" | "video";
    media_path: string;
    thumbnail_path?: string | null;
    filename?: string | null;
    mime_type?: string | null;
    size_bytes?: number | null;
}

export interface ApiCallSourceContext {
    type: ApiCallSource;
    route?: string | null;
    series_id?: string | null;
    episode_id?: string | null;
    project_id?: string | null;
    frame_id?: string | null;
    asset_id?: string | null;
    asset_type?: string | null;
    playground_generation_id?: string | null;
    video_task_id?: string | null;
}

export interface ApiCallActivity {
    id: string;
    task_id: string;
    type: string;
    status: ApiCallStatus;
    category: ApiCallCategory;
    source: ApiCallSource;
    progress: number;
    progress_stage?: string | null;
    progress_is_estimated?: boolean;
    provider_progress?: number | null;
    progress_steps?: ApiCallProgressStep[];
    error?: string | null;
    /** Stable application error identifier; provider codes stay diagnostic-only. */
    error_code?: string | null;
    /** Bounded provider diagnostics shown only after an explicit user expansion. */
    error_diagnostic?: string | null;
    detail?: string | null;
    prompt?: string | null;
    model_name?: string | null;
    parameters?: Record<string, string | number | boolean>;
    source_context?: ApiCallSourceContext;
    input_media?: ApiCallMedia[];
    outputs?: ApiCallMedia[];
    queue_position?: number | null;
    attempts: number;
    created_at: string;
    updated_at: string;
    started_at?: string | null;
    finished_at?: string | null;
    /** Managed billing history is immutable from the desktop client. */
    managed_read_only?: boolean;
    /** Billing settlement and application generation are separate activities. */
    activity_kind?: "billing" | "generation";
    billing_status?: string | null;
}

const DURABLE_JOB_POLL_INTERVAL_MS = 1000;
const DURABLE_JOB_TIMEOUT_MS = 2 * 60 * 60 * 1000;

function isDurableJobMarker(value: unknown): value is DurableJobMarker {
    if (!value || typeof value !== "object") return false;
    const candidate = value as Partial<DurableJobMarker>;
    return (
        typeof candidate.task_id === "string" &&
        (candidate.status === "queued" || candidate.status === "running")
    );
}

function wait(delayMs: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, delayMs));
}

/**
 * Wait for a server-mode durable job. Desktop endpoints never return a job
 * marker, so their original synchronous response path is unchanged.
 */
export async function waitForDurableJob(
    jobId: string,
    options: { pollIntervalMs?: number; timeoutMs?: number } = {},
): Promise<DurableJobStatus> {
    const pollIntervalMs = options.pollIntervalMs ?? DURABLE_JOB_POLL_INTERVAL_MS;
    const timeoutMs = options.timeoutMs ?? DURABLE_JOB_TIMEOUT_MS;
    const startedAt = Date.now();

    while (true) {
        const response = await axios.get<DurableJobStatus>(`${API_URL}/jobs/${jobId}`);
        const job = response.data;
        if (job.status === "completed") return job;
        if (job.status === "failed" || job.status === "canceled") {
            throw new Error(job.error || `Job ${job.status}`);
        }
        if (Date.now() - startedAt >= timeoutMs) {
            throw new Error("等待服务器任务完成超时");
        }
        await wait(pollIntervalMs);
    }
}

async function resolveProjectJobResponse<T>(data: T, scriptId: string): Promise<T> {
    if (!isDurableJobMarker(data)) return data;
    await waitForDurableJob(data.task_id);
    const response = await axios.get(`${API_URL}/projects/${scriptId}`);
    return { ...response.data, originalText: response.data.original_text } as T;
}

async function resolveExportJobResponse<T>(data: T): Promise<T> {
    if (!isDurableJobMarker(data)) return data;
    const job = await waitForDurableJob(data.task_id);
    if (!job.result || typeof job.result.url !== "string") {
        throw new Error("导出任务已完成，但没有返回输出地址");
    }
    return job.result as T;
}

// R2V v2 Phase 4 — Cross-episode reconcile types
export interface ReconcileSuggestion {
    local_id: string;
    local_name: string;
    suggested_series_id: string | null;
    suggested_series_name: string | null;
    confidence: number;
}

export interface BgmPreset {
    id: string;
    label: string;
    mood: string;
    url: string;
    available: boolean;
}

export interface ReconcileAction {
    local_id: string;
    action: "merge_into_series" | "create_new_in_series" | "skip";
    target_series_id?: string;
}

export interface VideoTask {
    id: string;
    project_id: string;
    image_url: string;
    prompt: string;
    status: "pending" | "processing" | "completed" | "failed" | "canceled";
    video_url?: string;
    duration: number;
    seed?: number;
    resolution: string;
    generate_audio: boolean;
    created_at: number;
    model?: string;
    frame_id?: string;
    source_image_id?: string | null;
    source_image_url?: string | null;
    frame_type?: FrameMovementType | null;
    generation_mode?: "t2v" | "i2v";
    ratio?: string;
    /** Failure reason set by pipeline / cancel / orphan recovery. */
    error?: string | null;
    /** Stable application error code used for localized failure copy. */
    error_code?: string | null;
    /** Redacted provider/worker details shown only when explicitly expanded. */
    error_diagnostic?: string | null;
    /** User-starred shortlist flag (multi-select per shot) — set via
     *  PATCH /annotate. Optional on the wire so older task records
     *  parse unchanged. */
    is_starred?: boolean;
    /** User-attached short free-text note (≤20 chars, server-truncated). */
    label?: string | null;
    /** Source tab in the storyboard workbench. Older records
     *  parse with null/undefined; CandidatesSection falls back to
     *  generation_mode to bucket them in that case. */
    workbench_tab?: "t2i_i2v" | null;
    /** New API provider-side identifiers used by TaskQueuePanel diagnostics. */
    provider_name?: string | null;
    provider_task_id?: string | null;
    provider_request_id?: string | null;
}

export interface CreateVideoTaskPayload {
    image_url: string;
    prompt: string;
    frame_id: string;
    source_image_id: string;
    frame_type: FrameMovementType;
    duration?: number;
    seed?: number | null;
    resolution?: string;
    generate_audio?: boolean;
    batch_size?: number;
    model?: string | null;
    generation_mode?: "t2v" | "i2v";
    ratio?: string | null;
    watermark?: boolean | null;
    workbench_tab?: "t2i_i2v" | null;
}

// ─── Storyboard Schema v2 types ─────────────────────────────────────────────

export interface DialogueStructured {
    speaker: string;
    line: string;
    emotion?: string | null;
    delivery?: string | null;
}

export interface CameraMovementStructured {
    primary: string;
    secondary?: string | null;
    speed: string;
    description?: string | null;
}

export interface BlockingData {
    description?: string | null;
    stage?: Array<{
        ref: string;
        zone?: string | null;
        depth?: string | null;
        height?: string | null;
        facing?: string | null;
        posture?: string | null;
    }> | null;
    camera_relation?: string | null;
}

export interface AudioNoteData {
    sfx?: string | null;
    ambience?: string | null;
    bgm_note?: string | null;
}

export interface LightingData {
    direction?: string | null;
    quality?: string | null;
    color_temp?: string | null;
    description?: string | null;
}

export interface RefineSSEEvent {
    type: "frame_refine_start" | "frame_refine_complete" | "frame_refine_error" | "batch_complete";
    frame_id?: string;
    frame_index?: number;
    total?: number;
    error?: string;
    reason?: string;
    label?: string;
    success?: number;
    failed?: number;
}

export const api = {
    createProject: async (title: string, text: string, skipAnalysis: boolean = false, workflowMode: string = "i2v_legacy", seriesId?: string) => {
        const standaloneDefaults = seriesId
            ? {}
            : {
                model_settings: workspaceDefaultModelSettings(),
                prompt_config: workspaceDefaultPromptConfig(),
            };
        const res = await axios.post(`${API_URL}/projects`, {
            title,
            text,
            workflow_mode: workflowMode,
            series_id: seriesId,
            ...standaloneDefaults,
        }, {
            params: { skip_analysis: skipAnalysis }
        });
        // New persisted projects can contain lineage edges, and attaching one
        // to a series can change which same-ID owner wins resolution.
        notifyAssetUsageChanged();
        return { ...res.data, originalText: res.data.original_text };
    },

    getProjects: async () => {
        const res = await axios.get(`${API_URL}/projects/`);
        return res.data.map((p: any) => ({ ...p, originalText: p.original_text }));
    },

    getProject: async (scriptId: string) => {
        const res = await axios.get(`${API_URL}/projects/${scriptId}`);
        return { ...res.data, originalText: res.data.original_text };
    },

    deleteProject: async (scriptId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}`);
        notifyAssetUsageChanged();
        return res.data;
    },

    /** Toggle the user-starred (featured) flag on a project. Returns the
     *  updated Script. No request body — the backend flips the current flag. */
    toggleProjectStarred: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/toggle_starred`);
        return res.data;
    },

    reparseProject: async (scriptId: string, text: string, previewRevision = "") => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/reparse`, {
            text,
            preview_revision: previewRevision,
        });
        notifyAssetUsageChanged();
        return { ...res.data, originalText: res.data.original_text };
    },

    extractPreview: async (scriptId: string, text: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/extract_preview`, { text });
        return res.data as {
            characters: any[];
            scenes: any[];
            props: any[];
            preview_revision?: string;
        };
    },

    /** Persist `original_text` without LLM reparse. Used for textarea
     *  blur-saves so navigation/reload doesn't drop in-progress drafts. */
    updateScriptText: async (scriptId: string, text: string) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/text`, { text });
        return { ...res.data, originalText: res.data.original_text };
    },

    syncDescriptions: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/sync_descriptions`);
        return res.data;
    },

    generateAssets: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/generate_assets`);
        return res.data;
    },

    createVideoTask: async (id: string, payload: CreateVideoTaskPayload) => {
        const res = await axios.post(`${API_URL}/projects/${id}/video_tasks`, payload);
        return res.data;
    },

    /** Upload an external image as a T2I首帧 candidate for an I2V flow.
     *  Backend appends to the frame's t2i_image_urls history and auto-
     *  selects the new image (it becomes the active首帧, unlocking
     *  Step 2). Returns the updated frame.
     *
     *  Validation lives on the backend:
     *   - ≤ 8 MB (413 if exceeded)
     *   - jpg/jpeg/png/webp only (415 if not)
     *  The caller does cheap front-side checks first to avoid a
     *  round-trip on obvious rejects (file type / size from the File
     *  object) and surfaces backend errors verbatim otherwise. */
    uploadT2IFrame: async (scriptId: string, frameId: string, file: File) => {
        const formData = new FormData();
        formData.append("file", file);
        const res = await axios.post(
            `${API_URL}/projects/${scriptId}/frames/${frameId}/upload_t2i`,
            formData,
            { headers: { "Content-Type": "multipart/form-data" } },
        );
        return res.data;
    },

    /** Persist storyboard I2V workbench state onto a frame.
     *  Used by the storyboard to write T2I history/active index/
     *  batch-count whenever the user changes them. Server clamps:
     *    t2i_image_urls ≤ 10 FIFO,
     *    t2i_selected_index ∈ [0, len-1],
     *    workbench_generate_count ∈ [1, 6].
     *  Unknown tab_mode returns 400. */
    updateFrameWorkbench: async (
        scriptId: string,
        frameId: string,
        patch: {
            workbench_tab_mode?: "t2i_i2v";
            t2i_image_urls?: string[];
            t2i_selected_index?: number;
            workbench_generate_count?: number;
            clip_start_image_id?: string;
            clip_start_image_url?: string;
            video_prompt?: string;
        },
    ) => {
        const res = await axios.patch(
            `${API_URL}/projects/${scriptId}/frames/${frameId}/workbench`,
            patch,
        );
        return res.data;
    },

    deleteFrameT2IImage: async (scriptId: string, frameId: string, imageIndex: number) => {
        const res = await axios.delete(
            `${API_URL}/projects/${scriptId}/frames/${frameId}/t2i-images/${imageIndex}`,
        );
        return res.data;
    },


    uploadFile: async (file: File) => {
        const formData = new FormData();
        formData.append("file", file);
        const response = await apiFetch(`${API_URL}/upload`, {
            method: "POST",
            body: formData,
        });
        if (!response.ok) throw new Error("文件上传失败");
        return response.json();
    },

    /** Lightweight liveness probe + log path. Used by the Diagnose UI
     *  on stuck tasks. 5s timeout because it's only meant to confirm
     *  the backend is alive, not to wait through a slow request. */
    healthCheck: async (): Promise<{
        ok: boolean;
        time: number;
        log_file: string;
        log_dir: string;
        studio_projects: number;
    }> => {
        const res = await axios.get(`${API_URL}/health`, { timeout: 5000 });
        return res.data;
    },

    /** Return last N lines of the backend log + any ERROR-flavored
     *  lines, for the Diagnose UI on stuck tasks. Backend caps at
     *  1000 lines so a runaway client can't drag the server. */
    diagnoseLogTail: async (lines: number = 200): Promise<{
        path: string;
        total_lines?: number;
        returned_lines?: number;
        lines: string[];
        errors: string[];
        missing: boolean;
    }> => {
        const res = await axios.get(`${API_URL}/diagnose/log_tail`, {
            params: { lines },
            timeout: 8000,
        });
        return res.data;
    },

    /** Set the user's star + label annotations on a video task. Used
     *  by Storyboard's candidates panel (shortlist + free-text note).
     *  All payload fields optional; pass clear_label=true to remove
     *  the label explicitly (label=null on its own = "don't change"). */
    annotateVideoTask: async (
        scriptId: string,
        taskId: string,
        payload: { is_starred?: boolean; label?: string | null; clear_label?: boolean },
    ) => {
        const res = await axios.patch(
            `${API_URL}/projects/${scriptId}/video_tasks/${taskId}/annotate`,
            payload,
        );
        return res.data;
    },

    /** Cancel a queued video task. Running provider calls are rejected by
     *  the server because they cannot be interrupted safely. */
    cancelVideoTask: async (scriptId: string, taskId: string) => {
        const res = await axios.post(
            `${API_URL}/projects/${scriptId}/video_tasks/${taskId}/cancel`,
        );
        return res.data;
    },

    /** Retry the same persisted task with its original parameters. */
    retryVideoTask: async (scriptId: string, taskId: string): Promise<VideoTask> => {
        const res = await axios.post(
            `${API_URL}/projects/${scriptId}/video_tasks/${taskId}/retry`,
        );
        return res.data;
    },

    /**
     * Upload an asset image as a new variant.
     * The uploaded image will be marked as the 'upload source' for reverse generation.
     */
    uploadAsset: async (
        scriptId: string,
        assetType: string,
        assetId: string,
        file: File,
        uploadType: string,
        description?: string
    ) => {
        const formData = new FormData();
        formData.append("file", file);

        const params = new URLSearchParams({
            upload_type: uploadType,
        });
        if (description) {
            params.append("description", description);
        }

        const response = await apiFetch(
            `${API_URL}/projects/${scriptId}/assets/${assetType}/${assetId}/upload?${params.toString()}`,
            {
                method: "POST",
                body: formData,
            }
        );

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || "Failed to upload asset");
        }

        return response.json();
    },

    generateAsset: async (scriptId: string, assetId: string, assetType: string, stylePreset: string, stylePrompt?: string, generationType: string = "all", prompt: string = "", applyStyle: boolean = true, negativePrompt: string = "", batchSize: number = 1, modelName?: string, aspectRatio?: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/generate`, {
            asset_id: assetId,
            asset_type: assetType,
            style_preset: stylePreset,
            style_prompt: stylePrompt,
            generation_type: generationType,
            prompt: prompt,
            apply_style: applyStyle,
            negative_prompt: negativePrompt,
            batch_size: batchSize,
            model_name: modelName,
            aspect_ratio: aspectRatio,
        });
        return res.data;
    },

    getTaskStatus: async (taskId: string) => {
        const res = await axios.get(`${API_URL}/tasks/${taskId}`);
        return res.data;
    },

    generateAssetVideo: async (scriptId: string, assetType: string, assetId: string, data: { prompt?: string, duration?: number, aspect_ratio?: string }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/${assetType}/${assetId}/generate_video`, data);
        return res.data;
    },

    /**
     * Generate Motion Reference video for an asset (Character Full Body/Headshot, Scene, or Prop).
     * This is part of Asset Activation v2.
     */
    generateMotionRef: async (
        scriptId: string,
        assetId: string,
        assetType: 'full_body' | 'head_shot' | 'scene' | 'prop',
        prompt?: string,
        audioUrl?: string,
        duration: number = 5,
        batchSize: number = 1
    ): Promise<any & { _task_id?: string }> => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/generate_motion_ref`, {
            asset_id: assetId,
            asset_type: assetType,
            prompt,
            audio_url: audioUrl,
            duration,
            batch_size: batchSize
        });
        return res.data;
    },

    deleteAssetVideo: async (scriptId: string, assetType: string, assetId: string, videoId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/assets/${assetType}/${assetId}/videos/${videoId}`);
        return res.data;
    },

    deleteVideoTask: async (scriptId: string, taskId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/video_tasks/${taskId}`);
        return res.data;
    },

    toggleAssetLock: async (scriptId: string, assetId: string, assetType: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/toggle_lock`, {
            asset_id: assetId,
            asset_type: assetType
        });
        return res.data;
    },

    toggleAssetStarred: async (scriptId: string, assetId: string, assetType: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/toggle_starred`, {
            asset_id: assetId,
            asset_type: assetType
        });
        return res.data;
    },

    toggleSeriesAssetStarred: async (seriesId: string, assetId: string, assetType: string) => {
        const res = await axios.post(`${API_URL}/series/${seriesId}/assets/toggle_starred`, {
            asset_id: assetId,
            asset_type: assetType
        });
        return res.data;
    },

    updateAssetImage: async (scriptId: string, assetId: string, assetType: string, imageUrl: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/update_image`, {
            asset_id: assetId,
            asset_type: assetType,
            image_url: imageUrl
        });
        return res.data;
    },

    selectAssetVariant: async (scriptId: string, assetId: string, assetType: string, variantId: string, generationType?: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/variant/select`, {
            asset_id: assetId,
            asset_type: assetType,
            variant_id: variantId,
            generation_type: generationType
        });
        return res.data;
    },

    deleteAssetVariant: async (scriptId: string, assetId: string, assetType: string, variantId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/variant/delete`, {
            asset_id: assetId,
            asset_type: assetType,
            variant_id: variantId
        });
        return res.data;
    },

    favoriteAssetVariant: async (scriptId: string, assetId: string, assetType: string, variantId: string, isFavorited: boolean, generationType?: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/variant/favorite`, {
            asset_id: assetId,
            asset_type: assetType,
            variant_id: variantId,
            is_favorited: isFavorited,
            generation_type: generationType
        });
        return res.data;
    },

    updateModelSettings: async (scriptId: string, settings: ModelSettingsUpdatePayload) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/model_settings`, settings);
        return res.data;
    },

    getPromptConfig: async (scriptId: string) => {
        const res = await axios.get(`${API_URL}/projects/${scriptId}/prompt_config`);
        return res.data;
    },

    updatePromptConfig: async (scriptId: string, config: { storyboard_polish?: string; video_polish?: string; polish_model?: string; entity_extraction?: string; style_analysis?: string; storyboard_extraction?: string }) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/prompt_config`, config);
        return res.data;
    },

    /** Built-in defaults for supported prompt keys
     *  (storyboard_polish / video_polish / entity_extraction /
     *  style_analysis / storyboard_extraction). Settings pre-fills fields from
     *  this; on save it stores "" for any field still equal to its default
     *  (delta semantics → backend uses the built-in). */
    fetchPromptDefaults: async (): Promise<Record<string, string>> => {
        const res = await axios.get<Record<string, string>>(`${API_URL}/prompt_defaults`);
        return res.data;
    },

    selectVideo: async (scriptId: string, frameId: string, videoId: string) => {
        // Manual pick — sets frame.is_video_pinned=true so future
        // auto_select_latest_video calls (fired by R2V poll completion)
        // skip this frame.
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/select_video`, {
            video_id: videoId
        });
        return res.data;
    },

    autoSelectLatestVideo: async (scriptId: string, frameId: string) => {
        // Fire-and-forget on every R2V poll completion. Backend picks the
        // latest completed task for this frame and updates frame.video_url
        // unless the user has pinned a different take.
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/auto_select_latest_video`);
        return res.data;
    },

    unpinVideo: async (scriptId: string, frameId: string) => {
        // Clear the pin; selected_video_id and video_url stay put until
        // the next auto-select picks a newer completed task.
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/unpin_video`);
        return res.data;
    },

    mergeVideos: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/merge`);
        return resolveProjectJobResponse(res.data, scriptId);
    },

    // Art Direction APIs
    analyzeScriptForStyles: async (scriptId: string, scriptText: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/art_direction/analyze`, {
            script_text: scriptText
        });
        return res.data;
    },

    saveArtDirection: async (scriptId: string, selectedStyleId: string, styleConfig: any, customStyles: any[] = [], aiRecommendations: any[] = []) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/art_direction/save`, {
            selected_style_id: selectedStyleId,
            style_config: styleConfig,
            custom_styles: customStyles,
            ai_recommendations: aiRecommendations
        });
        return res.data;
    },

    getStylePresets: async () => {
        const res = await axios.get(`${API_URL}/art_direction/presets`);
        return res.data;
    },

    // NOTE: polishPrompt removed - use refineFramePrompt for storyboard prompts
    //
    // 后端契约（#117）：
    //   成功 → 200 + { prompt_cn, prompt_en }
    //   失败 → 502 + { detail: { reason, message_zh, message_en, prompt_cn?, prompt_en? } }
    //     reason ∈ is_configured_false | api_error | json_parse_error | missing_keys | model_echo
    //     model_echo 是 warning（带原文），其余是 hard error。
    //
    // prevCn（#119）：迭代时传入上一次 CN 实现双语锚点；首次留空。
    // image_urls passes the active I2V first frame when available.
    //   polishModel: explicit override; "" lets backend resolve from
    //     project/series PromptConfig.polish_model, then default.
    polishVideoPrompt: async (
        draftPrompt: string,
        feedback: string = "",
        scriptId: string = "",
        prevCn: string = "",
        imageUrls: string[] = [],
        polishModel: string = "",
    ) => {
        const res = await axios.post(`${API_URL}/video/polish_prompt`, {
            draft_prompt: draftPrompt,
            feedback: feedback,
            script_id: scriptId,
            prev_cn: prevCn,
            image_urls: imageUrls,
            polish_model: polishModel,
        });
        return res.data;
    },
    updateAssetDescription: async (scriptId: string, assetId: string, assetType: string, description: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/update_description`, {
            asset_id: assetId,
            asset_type: assetType,
            description: description
        });
        return res.data;
    },

    updateAssetAttributes: async (scriptId: string, assetId: string, assetType: string, attributes: any) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/update_attributes`, {
            asset_id: assetId,
            asset_type: assetType,
            attributes: attributes
        });
        return res.data;
    },

    toggleFrameLock: async (scriptId: string, frameId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/toggle_lock`, {
            frame_id: frameId
        });
        return res.data;
    },

    updateFrame: async (scriptId: string, frameId: string, data: {
        image_prompt?: string;
        action_description?: string;
        dialogue?: string;
        camera_angle?: string;
        scene_id?: string;
        character_ids?: string[];
        prop_ids?: string[];
        duration?: number;
        shot_size?: string;
        camera_movement?: FrameMovementType;
        camera_movement_description?: string;
        transition_hint?: string;
    }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/update`, {
            frame_id: frameId,
            ...data
        });
        if (
            Object.hasOwn(data, "scene_id")
            || Object.hasOwn(data, "character_ids")
            || Object.hasOwn(data, "prop_ids")
        ) {
            notifyAssetUsageChanged();
        }
        return res.data;
    },

    updateProjectStyle: async (scriptId: string, stylePreset: string, stylePrompt?: string) => {
        const res = await axios.patch(`${API_URL}/projects/${scriptId}/style`, {
            style_preset: stylePreset,
            style_prompt: stylePrompt
        });
        return res.data;
    },

    renderFrame: async (scriptId: string, frameId: string, compositionData: any, prompt: string, batchSize: number = 1) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/storyboard/render`, {
            frame_id: frameId,
            composition_data: compositionData,
            prompt: prompt,
            batch_size: batchSize
        });
        return resolveProjectJobResponse(res.data, scriptId);
    },

    // === STORYBOARD DRAMATIZATION v2 ===

    /**
     * Analyzes script text and generates storyboard frames using AI.
     * Replaces existing frames with newly generated ones.
     */
    analyzeToStoryboard: async (scriptId: string, text: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/storyboard/analyze`, {
            text: text
        });
        notifyAssetUsageChanged();
        return res.data;
    },

    /**
     * Refines a raw prompt into bilingual (CN/EN) prompts using AI.
     * Returns { prompt_cn, prompt_en, frame_updated }.
     */
    refineFramePrompt: async (scriptId: string, frameId: string, rawPrompt: string, assets: any[] = [], feedback: string = "") => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/storyboard/refine_prompt`, {
            frame_id: frameId,
            raw_prompt: rawPrompt,
            assets: assets,
            feedback: feedback
        });
        return res.data;
    },

    generateStoryboard: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/generate_storyboard`);
        return resolveProjectJobResponse(res.data, scriptId);
    },

    previewDub: async (scriptId: string, frameId: string, videoTaskId: string, offsetMs: number = 0) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/dub/preview`, {
            video_task_id: videoTaskId,
            offset_ms: offsetMs,
        }, { timeout: 120000 });
        return resolveProjectJobResponse(res.data, scriptId);
    },

    applyDub: async (scriptId: string, frameId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/dub/apply`);
        return res.data;
    },

    revertDub: async (scriptId: string, frameId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/frames/${frameId}/dub`);
        return res.data;
    },

    /** Schema v2 · Refine a single frame (Phase 2 rich fields). */
    refineSingleFrame: async (scriptId: string, frameId: string) => {
        const response = await apiFetch(`${API_URL}/projects/${scriptId}/frames/${frameId}/refine`, {
            method: "POST",
        });
        if (!response.ok) throw new Error("分镜优化失败");
        return response.json();
    },

    /** Schema v2 · Desktop uses SSE; server mode returns a durable-job marker. */
    refineBatchFrames: async (
        scriptId: string,
        onEvent: (event: RefineSSEEvent) => void,
    ): Promise<void> => {
        const response = await apiFetch(`${API_URL}/projects/${scriptId}/storyboard/refine_batch`, {
            method: "POST",
        });
        if (!response.ok) throw new Error("批量优化启动失败");

        if ((response.headers.get("content-type") || "").toLowerCase().includes("application/json")) {
            const marker: unknown = await response.json();
            if (!isDurableJobMarker(marker)) {
                throw new Error("批量优化返回了无效的服务器任务标识");
            }
            await waitForDurableJob(marker.task_id);
            return;
        }

        const reader = response.body?.getReader();
        if (!reader) return;
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";
            let currentEventType = "";
            for (const line of lines) {
                if (line.startsWith("event: ")) {
                    currentEventType = line.slice(7).trim();
                } else if (line.startsWith("data: ")) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        onEvent({ type: currentEventType as RefineSSEEvent["type"], ...data });
                    } catch { /* skip malformed lines */ }
                }
            }
        }
    },

    /** PR-3k · BGM preset catalog for Assembly Mix phase. */
    listBgmPresets: async (): Promise<BgmPreset[]> => {
        const response = await apiFetch(`${API_URL}/bgm/presets`);
        if (!response.ok) throw new Error("背景音乐预设加载失败");
        return response.json();
    },

    /** Upload and select a project-owned local BGM track. */
    uploadCustomBgm: async (scriptId: string, file: File) => {
        const formData = new FormData();
        formData.append("file", file);
        const response = await axios.post(
            `${API_URL}/projects/${scriptId}/audio_mix/bgm`,
            formData,
            { headers: { "Content-Type": "multipart/form-data" } },
        );
        return response.data;
    },

    /** PR-3k · Update audio mix (BGM url + per-track volumes). */
    updateAudioMix: async (scriptId: string, payload: {
        bgm_url?: string | null;
        dialogue_volume?: number;
        bgm_volume?: number;
        sfx_volume?: number;
    }) => {
        const response = await apiFetch(`${API_URL}/projects/${scriptId}/audio_mix`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!response.ok) throw new Error("音频混合更新失败");
        return response.json();
    },

    updateVoiceParams: async (scriptId: string, charId: string, speed: number, pitch: number, volume: number) => {
        const response = await apiFetch(`${API_URL}/projects/${scriptId}/characters/${charId}/voice_params`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ speed, pitch, volume }),
        });
        if (!response.ok) throw new Error("语音参数更新失败");
        return response.json();
    },

    exportProject: async (scriptId: string, options: any) => {
        const response = await apiFetch(`${API_URL}/projects/${scriptId}/export`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(options),
        });
        if (!response.ok) throw new Error("项目导出失败");
        return resolveExportJobResponse(await response.json());
    },

    generateVideo: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/generate_video`);
        return resolveProjectJobResponse(res.data, scriptId);
    },

    getEnvConfig: async (): Promise<EnvConfigPayload> => {
        const res = await axios.get<EnvConfigPayload>(`${API_URL}/config/env`);
        return res.data;
    },

    saveEnvConfig: async (config: EnvConfigPayload) => {
        const res = await axios.post(`${API_URL}/config/env`, config, {
            timeout: 60000, // 60 seconds timeout
        });
        return res.data;
    },

    inspectApiKeys: async (reveal = false): Promise<ApiKeyInspectionPayload> => {
        const res = await axios.post<ApiKeyInspectionPayload>(
            `${API_URL}/config/api-keys/inspect`,
            { reveal },
        );
        return res.data;
    },

    extractLastFrame: async (scriptId: string, frameId: string, videoTaskId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/extract_last_frame`, {
            video_task_id: videoTaskId,
        });
        return res.data;
    },

    uploadFrameImage: async (scriptId: string, frameId: string, file: File) => {
        const formData = new FormData();
        formData.append("file", file);
        const response = await apiFetch(
            `${API_URL}/projects/${scriptId}/frames/${frameId}/upload_image`,
            { method: "POST", body: formData }
        );
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || "Failed to upload frame image");
        }
        return response.json();
    },

    // ============================================
    // Series APIs
    // ============================================

    // Series CRUD
    createSeriesV2: async (
        title: string,
        opts: { description?: string; workflow_mode?: "i2v_legacy"; content_mode?: "scripted" | "freeform"; default_generation_mode?: "i2v" } = {},
    ) => {
        const response = await axios.post(`${API_URL}/series`, {
            title,
            description: opts.description ?? "",
            workflow_mode: opts.workflow_mode ?? "i2v_legacy",
            content_mode: opts.content_mode ?? "scripted",
            default_generation_mode: opts.default_generation_mode ?? "i2v",
            model_settings: workspaceDefaultModelSettings(),
            prompt_config: workspaceDefaultPromptConfig(),
        });
        return response.data;
    },

    createSeries: async (title: string, description?: string, workflowMode: "i2v_legacy" = "i2v_legacy") => {
        const response = await axios.post(`${API_URL}/series`, {
            title,
            description,
            workflow_mode: workflowMode,
            model_settings: workspaceDefaultModelSettings(),
            prompt_config: workspaceDefaultPromptConfig(),
        });
        return response.data;
    },
    listSeries: async () => {
        const response = await axios.get(`${API_URL}/series`);
        return response.data;
    },
    /** Core 全局/共享资产池（跨系列/项目聚合）。后端：GET /library/assets → {characters, scenes, props}。 */
    listLibraryAssets: async () => {
        const res = await axios.get(`${API_URL}/library/assets`);
        return res.data;
    },
    /** Compact top-level library feed. Avoids downloading complete projects,
     *  storyboard frames, scripts, and generation history. */
    getAssetLibraryOverview: async () => {
        const res = await axios.get(`${API_URL}/library/overview`, { timeout: 10_000 });
        return res.data;
    },
    /** Owner-aware Asset Editor API shared by Home, Series, and Episode views. */
    getOwnedAsset: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
    ) => {
        const res = await axios.get(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}`,
        );
        return res.data;
    },
    getOwnedAssetDeleteImpact: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
    ): Promise<AssetDeleteImpact> => {
        const res = await axios.get<AssetDeleteImpact>(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}/delete-impact`,
        );
        return res.data;
    },
    deleteOwnedAsset: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
        force = false,
    ): Promise<AssetDeleteResult> => {
        const res = await axios.delete<AssetDeleteResult>(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}`,
            { params: { force } },
        );
        notifyAssetUsageChanged();
        return res.data;
    },
    updateOwnedAsset: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
        patch: AssetMetadataPatch,
    ) => {
        const res = await axios.patch(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}`,
            patch,
        );
        if (
            patch.target_asset_type
            || Object.hasOwn(patch.attributes, "base_character_id")
        ) {
            notifyAssetUsageChanged();
        }
        return res.data;
    },
    generateOwnedAsset: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
        request: AssetGenerationRequest,
    ) => {
        const res = await axios.post(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}/generate`,
            { asset_id: assetId, asset_type: assetType, ...request },
        );
        return res.data;
    },
    selectOwnedAssetVariant: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
        variantId: string,
        generationType?: string,
    ) => {
        const res = await axios.post(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}/variants/${variantId}/select`,
            { generation_type: generationType },
        );
        return res.data;
    },
    deleteOwnedAssetVariant: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
        variantId: string,
        generationType?: string,
    ) => {
        const res = await axios.delete(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}/variants/${variantId}`,
            { data: { generation_type: generationType } },
        );
        return res.data;
    },
    favoriteOwnedAssetVariant: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
        variantId: string,
        isFavorited: boolean,
        generationType?: string,
    ) => {
        const res = await axios.post(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}/variants/${variantId}/favorite`,
            { is_favorited: isFavorited, generation_type: generationType },
        );
        return res.data;
    },
    setOwnedAssetFavorite: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
        starred: boolean,
    ) => {
        const res = await axios.put(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}/favorite`,
            { starred },
        );
        return res.data;
    },
    generateOwnedAssetMotion: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
        request: AssetMotionGenerationRequest,
    ) => {
        const res = await axios.post(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}/motion/generate`,
            request,
        );
        return res.data;
    },
    selectOwnedAssetMotionVariant: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
        variantId: string,
        motionType: string,
    ) => {
        const res = await axios.post(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}/motion/variants/${variantId}/select`,
            { motion_type: motionType },
        );
        return res.data;
    },
    deleteOwnedAssetMotionVariant: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
        variantId: string,
        motionType: string,
    ) => {
        const res = await axios.delete(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}/motion/variants/${variantId}`,
            { data: { motion_type: motionType } },
        );
        return res.data;
    },
    favoriteOwnedAssetMotionVariant: async (
        sourceKind: AssetOwnerKind,
        sourceId: string,
        assetType: EditableAssetType,
        assetId: string,
        variantId: string,
        isFavorited: boolean,
        motionType: string,
    ) => {
        const res = await axios.put(
            `${API_URL}/asset-sources/${sourceKind}/${sourceId}/assets/${assetType}/${assetId}/motion/variants/${variantId}/favorite`,
            { motion_type: motionType, is_favorited: isFavorited },
        );
        return res.data;
    },
    /** 新建一条全局/共享资产。后端：POST /library/assets。
     *  assetType 为单数（"character"|"scene"|"prop"）。data 可含 name/description/persona/image_url。 */
    createLibraryAsset: async (
        assetType: string,
        data: { name: string; description?: string; persona?: string; image_url?: string },
    ) => {
        const res = await axios.post(`${API_URL}/library/assets`, { asset_type: assetType, ...data });
        notifyAssetUsageChanged();
        return res.data;
    },
    /** 上传一张本地图片到全局资产库，返回可被前端加载的 image_url。
     *  后端契约：POST /library/assets/upload，multipart 字段名 "file" → { image_url }。
     *  调用方拿到 image_url 后传给 createLibraryAsset。 */
    uploadLibraryImage: async (file: File): Promise<{ image_url: string }> => {
        const formData = new FormData();
        formData.append("file", file);
        const res = await axios.post<{ image_url: string }>(`${API_URL}/library/assets/upload`, formData, {
            headers: { "Content-Type": "multipart/form-data" },
        });
        return res.data;
    },
    /** 补丁更新全局资产（仅发送的字段生效，PATCH 语义）。后端：PUT /library/assets/{type}/{id}。assetType 单数。 */
    updateLibraryAsset: async (
        assetType: string,
        assetId: string,
        patch: {
            name?: string;
            description?: string;
            persona?: string;
            image_url?: string;
            starred?: boolean;
            locked?: boolean;
            visual_weight?: number;
        },
    ) => {
        const res = await axios.put(`${API_URL}/library/assets/${assetType}/${assetId}`, patch);
        return res.data;
    },
    /** 把项目/系列来源资产 deep-copy 提升进全局共享池。后端：POST /library/assets/promote。
     *  sourceKind: "project"|"series"；assetType 单数。 */
    promoteAssetToLibrary: async (
        sourceKind: "project" | "series",
        sourceId: string,
        assetType: string,
        assetId: string,
    ) => {
        const res = await axios.post(`${API_URL}/library/assets/promote`, {
            source_kind: sourceKind,
            source_id: sourceId,
            asset_type: assetType,
            asset_id: assetId,
        });
        notifyAssetUsageChanged();
        return res.data;
    },
    getSeries: async (seriesId: string) => {
        const response = await axios.get(`${API_URL}/series/${seriesId}`);
        return response.data;
    },
    updateSeries: async (
        seriesId: string,
        data: { title?: string; description?: string; art_direction?: any },
    ) => {
        const response = await axios.put(`${API_URL}/series/${seriesId}`, data);
        return response.data;
    },

    /** R2V v2 Phase 3 — fetch previous episode raw snippet + AI summary cache state.
     *  P2-a extended response with last_frames for Storyboard cross-step rail. */
    getPreviousEpisodeSummary: async (scriptId: string): Promise<{
        has_previous: boolean;
        previous_episode_id: string | null;
        previous_episode_title: string | null;
        script_available?: boolean;
        raw_snippet: string;
        ai_summary: string | null;
        ai_summary_stale: boolean;
        last_frames?: Array<{
            id: string;
            action_description: string;
            thumbnail_url: string | null;
            video_url: string | null;
        }>;
    }> => {
        const res = await axios.get(`${API_URL}/projects/${scriptId}/previous_episode`);
        return res.data;
    },

    /** On-demand generate AI summary of previous episode (user-triggered). */
    generatePreviousEpisodeSummary: async (scriptId: string): Promise<{
        ai_summary: string;
        ai_summary_stale: boolean;
        previous_episode_id: string;
        previous_episode_title: string;
    }> => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/previous_episode/summary`);
        return res.data;
    },

    /** R2V v2 Phase 4 — fetch reconcile suggestions for this episode's
     *  extracted entities vs the parent series's shared library. */
    getReconcileSuggestions: async (scriptId: string): Promise<{
        characters: ReconcileSuggestion[];
        scenes: ReconcileSuggestion[];
        props: ReconcileSuggestion[];
    }> => {
        const res = await axios.get(`${API_URL}/projects/${scriptId}/reconcile/suggestions`);
        return res.data;
    },

    /** Apply user-confirmed reconcile decisions. */
    applyReconcile: async (
        scriptId: string,
        decisions: {
            characters?: ReconcileAction[];
            scenes?: ReconcileAction[];
            props?: ReconcileAction[];
        },
    ) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/reconcile/apply`, decisions);
        notifyAssetUsageChanged();
        return res.data;
    },

    /** R2V v2 Phase 5 — series-scope quick-create CRUD for Cast modal. */
    createSeriesAsset: async (
        seriesId: string,
        kind: "characters" | "scenes" | "props",
        data: { name: string; description?: string; persona?: string; image_url?: string },
    ) => {
        const res = await axios.post(`${API_URL}/series/${seriesId}/${kind}`, data);
        notifyAssetUsageChanged();
        return res.data;
    },

    /** R2V v2 Phase 2 — clear project-level art_direction (return to series inherit). */
    clearProjectArtDirection: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/art_direction/clear`);
        return res.data;
    },

    /** R2V v2 P2-b — next-episode hook prediction state. */
    getNextEpisodeHook: async (scriptId: string): Promise<{
        has_text: boolean;
        hook: string | null;
        stale: boolean;
    }> => {
        const res = await axios.get(`${API_URL}/projects/${scriptId}/next_hook`);
        return res.data;
    },

    /** Generate hook prediction (user-triggered). */
    generateNextEpisodeHook: async (scriptId: string): Promise<{
        hook: string;
        stale: boolean;
    }> => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/next_hook`);
        return res.data;
    },

    /** Manually edit / clear hook cache. */
    updateNextEpisodeHook: async (scriptId: string, hook: string | null) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/next_hook`, { hook });
        return res.data;
    },

    /** R2V v2 P1-c — cross-episode character appearances (for @ helper). */
    getCharacterAppearances: async (seriesId: string, characterId: string): Promise<{
        character: { id: string; name: string; persona: string; description: string };
        appearances: Array<{ episode_id: string; episode_number: number | null; episode_title: string; frame_count: number }>;
        total_frames: number;
    }> => {
        const res = await axios.get(`${API_URL}/series/${seriesId}/characters/${characterId}/appearances`);
        return res.data;
    },

    /** R2V v2 P1-b — manually edit / clear last_episode_summary cache. */
    updateLastEpisodeSummary: async (scriptId: string, aiSummary: string | null) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/last_episode_summary`, {
            ai_summary: aiSummary,
        });
        return res.data;
    },
    deleteSeries: async (seriesId: string) => {
        const response = await axios.delete(`${API_URL}/series/${seriesId}`, {
            params: { delete_episodes: true },
        });
        notifyAssetUsageChanged();
        return response.data;
    },

    // Series Episodes
    getSeriesEpisodes: async (seriesId: string) => {
        const response = await axios.get(`${API_URL}/series/${seriesId}/episodes`);
        return response.data;
    },
    addEpisodeToSeries: async (seriesId: string, scriptId: string, episodeNumber?: number) => {
        const response = await axios.post(`${API_URL}/series/${seriesId}/episodes`, { script_id: scriptId, episode_number: episodeNumber });
        notifyAssetUsageChanged();
        return response.data;
    },
    removeEpisodeFromSeries: async (seriesId: string, scriptId: string) => {
        const response = await axios.delete(`${API_URL}/series/${seriesId}/episodes/${scriptId}`);
        notifyAssetUsageChanged();
        return response.data;
    },

    // Series Assets
    getSeriesAssets: async (seriesId: string) => {
        const response = await axios.get(`${API_URL}/series/${seriesId}/assets`);
        return response.data;
    },
    importSeriesAssets: async (seriesId: string, sourceSeriesId: string, assetIds: string[]) => {
        const response = await axios.post(`${API_URL}/series/${seriesId}/assets/import`, { source_series_id: sourceSeriesId, asset_ids: assetIds });
        notifyAssetUsageChanged();
        return response.data;
    },

    // Series Prompt Config
    getSeriesPromptConfig: async (seriesId: string) => {
        const response = await axios.get(`${API_URL}/series/${seriesId}/prompt_config`);
        return response.data;
    },
    updateSeriesPromptConfig: async (seriesId: string, config: { storyboard_polish?: string; video_polish?: string; polish_model?: string; storyboard_extraction?: string }) => {
        const response = await axios.put(`${API_URL}/series/${seriesId}/prompt_config`, config);
        return response.data;
    },
    getSeriesModelSettings: async (seriesId: string): Promise<EffectiveModelSettingsPayload> => {
        const response = await axios.get(`${API_URL}/series/${seriesId}/model_settings`);
        return response.data;
    },
    updateSeriesModelSettings: async (seriesId: string, settings: ModelSettingsUpdatePayload): Promise<EffectiveModelSettingsPayload> => {
        const response = await axios.put(
            `${API_URL}/series/${seriesId}/model_settings`,
            settings,
            { timeout: SERIES_MODEL_SETTINGS_TIMEOUT_MS },
        );
        return response.data;
    },

    // Helper: create a project and add it as an episode to a series
    createEpisodeForSeries: async (seriesId: string, title: string, _episodeNumber: number, workflowMode: string = "i2v_legacy") => {
        return api.createProject(title, "", true, workflowMode, seriesId);
    },

    // File Import
    importFilePreview: async (file: File, suggestedEpisodes: number = 1) => {
        const formData = new FormData();
        formData.append('file', file);
        const response = await axios.post(`${API_URL}/series/import/preview?suggested_episodes=${suggestedEpisodes}`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },
    importFileConfirm: async (data: { title: string; description?: string; import_id?: string; text?: string; episodes: any[] }) => {
        const response = await axios.post<ImportFileConfirmResponse>(
            `${API_URL}/series/import/confirm`,
            {
                ...data,
                model_settings: workspaceDefaultModelSettings(),
                prompt_config: workspaceDefaultPromptConfig(),
            },
        );
        notifyAssetUsageChanged();
        return response.data;
    },
};

// ============================================
// CRUD APIs for Assets and Frames
// ============================================

export const crudApi = {
    // Character CRUD
    createCharacter: async (scriptId: string, data: {
        name: string;
        description?: string;
        age?: string;
        gender?: string;
        clothing?: string;
    }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/characters`, data);
        notifyAssetUsageChanged();
        return res.data;
    },

    deleteCharacter: async (scriptId: string, characterId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/characters/${characterId}`);
        notifyAssetUsageChanged();
        return res.data;
    },

    // Scene CRUD
    createScene: async (scriptId: string, data: {
        name: string;
        description?: string;
        time_of_day?: string;
        lighting_mood?: string;
    }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/scenes`, data);
        notifyAssetUsageChanged();
        return res.data;
    },

    deleteScene: async (scriptId: string, sceneId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/scenes/${sceneId}`);
        notifyAssetUsageChanged();
        return res.data;
    },

    // Prop CRUD
    createProp: async (scriptId: string, data: {
        name: string;
        description?: string;
    }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/props`, data);
        notifyAssetUsageChanged();
        return res.data;
    },

    deleteProp: async (scriptId: string, propId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/props/${propId}`);
        notifyAssetUsageChanged();
        return res.data;
    },

    // Frame CRUD
    createFrame: async (scriptId: string, data: {
        scene_id: string;
        action_description: string;
        character_ids?: string[];
        prop_ids?: string[];
        dialogue?: string;
        speaker?: string;
        camera_angle?: string;
        insert_at?: number;
    }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames`, data);
        notifyAssetUsageChanged();
        return res.data;
    },

    deleteFrame: async (scriptId: string, frameId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/frames/${frameId}`);
        notifyAssetUsageChanged();
        return res.data;
    },

    copyFrame: async (scriptId: string, frameId: string, insertAt?: number) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/copy`, {
            frame_id: frameId,
            insert_at: insertAt
        });
        notifyAssetUsageChanged();
        return res.data;
    },

    reorderFrames: async (scriptId: string, frameIds: string[]) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/frames/reorder`, {
            frame_ids: frameIds
        });
        return res.data;
    }
};

// ─── Central API activity monitor ───────────────────────────────────────────

function accountUsageStatus(status: string): ApiCallStatus {
  const normalized = status.trim().toLowerCase();
  if (normalized === "failed") return "failed";
  if (normalized === "canceled" || normalized === "cancelled") return "canceled";
  if ([
    "settled",
    "completed",
    "captured",
    "succeeded",
    "released",
    "refunded",
  ].includes(normalized)) return "completed";
  return "running";
}

function accountUsageCategory(operation: string): ApiCallCategory {
  if (operation.startsWith("images.")) return "image";
  if (operation.startsWith("video.")) return "video";
  if (operation.startsWith("chat.")) return "text";
  return "other";
}

function accountUsageActivity(item: AccountUsageItem): ApiCallActivity {
  const status = accountUsageStatus(item.status);
  const terminal = ["completed", "failed", "canceled"].includes(status);
  return {
    id: `billing:${item.id}`,
    task_id: item.id,
    type: item.operation,
    status,
    category: accountUsageCategory(item.operation),
    source: "workspace",
    progress: status === "completed" ? 100 : status === "running" ? 50 : 0,
    progress_is_estimated: status === "running",
    error_code: item.error_code ?? null,
    model_name: item.model,
    attempts: 1,
    created_at: item.created_at,
    updated_at: item.settled_at || item.created_at,
    started_at: item.created_at,
    finished_at: terminal ? item.settled_at || item.created_at : null,
    managed_read_only: true,
    activity_kind: "billing",
    billing_status: item.status,
  };
}

const PLAYGROUND_ACTIVITY_PREFIX = "playground:";

function playgroundActivityStatus(status: string): ApiCallStatus {
  if (status === "pending") return "queued";
  if (status === "processing") return "running";
  if (status === "failed") return "failed";
  if (status === "completed") return "completed";
  return "running";
}

function playgroundActivityType(mode: string): string {
  if (mode === "i2i") return "images.edits";
  if (mode === "t2i") return "images.generations";
  return "video.generations";
}

function primitiveParameters(
  parameters: Record<string, any>,
): Record<string, string | number | boolean> {
  return Object.fromEntries(
    Object.entries(parameters).filter((entry): entry is [string, string | number | boolean] => {
      const value = entry[1];
      return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
    }),
  );
}

function playgroundActivity(item: PlaygroundGenerationResponse): ApiCallActivity {
  const status = playgroundActivityStatus(item.status);
  const terminal = status === "completed" || status === "failed" || status === "canceled";
  const recordUpdatedAt = item.updated_at || item.created_at;
  const lifecycleUpdatedAt = terminal
    ? item.finished_at || recordUpdatedAt
    : recordUpdatedAt;
  const stage = status === "queued"
    ? "queued"
    : status === "running"
      ? "provider_processing"
      : status === "completed"
        ? "completed"
        : "provider_processing";
  const steps: ApiCallProgressStep[] = [
    {
      id: "queued",
      state: status === "queued" ? "active" : "completed",
      started_at: item.created_at,
      finished_at: status === "queued" ? null : item.created_at,
    },
  ];
  if (status !== "queued") {
    steps.push({
      id: status === "completed" ? "completed" : "provider_processing",
      state: status === "running" ? "active" : status === "failed" ? "failed" : "completed",
      started_at: item.created_at,
      finished_at: terminal ? lifecycleUpdatedAt : null,
      message: status === "failed" ? item.error ?? null : null,
    });
  }
  return {
    id: `${PLAYGROUND_ACTIVITY_PREFIX}${item.id}`,
    task_id: item.id,
    type: playgroundActivityType(item.mode),
    status,
    category: item.mode === "t2i" || item.mode === "i2i" ? "image" : "video",
    source: "playground",
    progress: status === "completed" ? 100 : status === "running" ? 50 : 0,
    progress_stage: stage,
    progress_is_estimated: status === "running",
    progress_steps: steps,
    error: item.error ?? null,
    prompt: item.prompt,
    model_name: item.model_id,
    parameters: primitiveParameters(item.parameters),
    source_context: {
      type: "playground",
      route: "#/playground",
      playground_generation_id: item.id,
    },
    input_media: item.input_media.map((mediaPath, index) => ({
      id: `input-${index}`,
      media_type: "image",
      media_path: mediaPath,
      filename: mediaPath.split("/").pop() || `input-${index + 1}`,
    })),
    outputs: item.outputs.map((output) => ({
      id: output.id,
      media_type: output.media_type === "video" ? "video" : "image",
      media_path: output.media_path,
      thumbnail_path: output.thumbnail_path ?? null,
      filename: output.media_path.split("/").pop() || output.id,
    })),
    attempts: 1,
    created_at: item.created_at,
    // API Calls sorts and calculates duration from lifecycle time. A later
    // metadata edit (for example save-to-library) must not make an old
    // generation look newly completed.
    updated_at: lifecycleUpdatedAt,
    started_at: status === "queued" ? null : item.created_at,
    finished_at: terminal ? lifecycleUpdatedAt : null,
    managed_read_only: true,
    activity_kind: "generation",
    billing_status: null,
  };
}

export const apiCallsApi = {
  list: async (limit = 200): Promise<ApiCallActivity[]> => {
    if (isHybridModeEnabled()) {
      const [billing, playground] = await Promise.all([
        authApi.accountUsage(Math.min(limit, 100)),
        axios.get<PlaygroundGenerationResponse[]>(`${API_URL}/playground/history`, {
          params: { limit, offset: 0 },
        }),
      ]);
      const activities = [
        ...playground.data.map(playgroundActivity),
        ...billing.items.map(accountUsageActivity),
      ];
      return activities
        .sort(
          (a, b) =>
            (apiTimestampMilliseconds(b.updated_at) ?? 0) -
            (apiTimestampMilliseconds(a.updated_at) ?? 0),
        )
        .slice(0, limit);
    }
    const response = await axios.get<ApiCallActivity[]>(`${API_URL}/jobs`, { params: { limit } });
    return response.data;
  },

  cancel: (jobId: string) =>
    axios
      .post<ApiCallActivity>(`${API_URL}/jobs/${jobId}/cancel`)
      .then((response) => response.data),

  retry: (jobId: string) =>
    axios
      .post<ApiCallActivity>(`${API_URL}/jobs/${jobId}/retry`)
      .then((response) => response.data),

  dismiss: (jobId: string) =>
    axios.delete<void>(`${API_URL}/jobs/${jobId}`).then(() => undefined),

  download: async (jobId: string, outputId: string) => {
    if (jobId.startsWith(PLAYGROUND_ACTIVITY_PREFIX)) {
      const generationId = jobId.slice(PLAYGROUND_ACTIVITY_PREFIX.length);
      const generation = await axios
        .get<PlaygroundGenerationResponse>(`${API_URL}/playground/history/${generationId}`)
        .then((response) => response.data);
      const output = generation.outputs.find((candidate) => candidate.id === outputId);
      if (!output) throw new Error("Playground output not found");
      const response = await apiFetch(getAssetUrl(output.media_path));
      if (!response.ok) {
        throw new Error(`Playground output download failed (${response.status})`);
      }
      const blob = await response.blob();
      if (blob.size === 0) throw new Error("Playground output download was empty");
      return {
        blob,
        filename: output.media_path.split("/").pop() || `enmotion-output-${outputId}`,
      };
    }
    const response = await axios.get<Blob>(
      `${API_URL}/jobs/${jobId}/outputs/${encodeURIComponent(outputId)}/download`,
      { responseType: "blob" },
    );
    const disposition = String(response.headers["content-disposition"] || "");
    const utf8Name = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const quotedName = disposition.match(/filename="([^"]+)"/i)?.[1];
    let filename = utf8Name ? decodeURIComponent(utf8Name) : quotedName;
    if (!filename) filename = `enmotion-output-${outputId}`;
    return { blob: response.data, filename };
  },
};

// ─── Playground API ─────────────────────────────────────────────────────────

export interface PlaygroundGenerateRequest {
  mode: string;
  model_id: string;
  prompt: string;
  negative_prompt?: string;
  input_media?: string[];
  parameters?: Record<string, any>;
  batch_size?: number;
}

export interface PlaygroundGenerationResponse {
  id: string;
  mode: string;
  model_id: string;
  prompt: string;
  negative_prompt?: string;
  input_media: string[];
  parameters: Record<string, any>;
  batch_size: number;
  outputs: Array<{
    id: string;
    media_path: string;
    media_type: string;
    thumbnail_path?: string;
    saved_to_library: boolean;
    library_category?: PlaygroundLibraryCategory;
  }>;
  status: string;
  error?: string;
  created_at: string;
  updated_at?: string;
  finished_at?: string | null;
}

export type PlaygroundLibraryCategory = "character" | "scene" | "prop";

export interface PlaygroundTemplateResponse {
  id: string;
  name: string;
  category: string;
  prompt: string;
  negative_prompt?: string;
  default_mode?: string;
  default_model_id?: string;
  default_parameters: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export const playgroundApi = {
  generate: (data: PlaygroundGenerateRequest) =>
    axios.post<PlaygroundGenerationResponse>(API_URL + "/playground/generate", data).then(r => r.data),

  getHistory: (limit = 50, offset = 0) =>
    axios.get<PlaygroundGenerationResponse[]>(API_URL + "/playground/history", { params: { limit, offset } }).then(r => r.data),

  getGeneration: (id: string) =>
    axios.get<PlaygroundGenerationResponse>(API_URL + "/playground/history/" + id).then(r => r.data),

  getGenerationStatus: (id: string) =>
    axios.get<{ id: string; status: string; outputs: any[]; error?: string }>(API_URL + "/playground/history/" + id + "/status").then(r => r.data),

  deleteGeneration: (id: string) =>
    axios.delete(API_URL + "/playground/history/" + id).then(r => r.data),

  saveToLibrary: (
    generationId: string,
    outputId: string,
    category: PlaygroundLibraryCategory,
  ) =>
    axios.post<{ ok: boolean; category: PlaygroundLibraryCategory }>(
      API_URL + "/playground/history/" + generationId + "/outputs/" + outputId + "/save-to-library",
      { category },
    ).then(r => r.data),

  getTemplates: () =>
    axios.get<PlaygroundTemplateResponse[]>(API_URL + "/playground/templates").then(r => r.data),

  createTemplate: (data: { name: string; category?: string; prompt: string; negative_prompt?: string; default_mode?: string; default_model_id?: string; default_parameters?: Record<string, any> }) =>
    axios.post<PlaygroundTemplateResponse>(API_URL + "/playground/templates", data).then(r => r.data),

  updateTemplate: (id: string, data: Partial<{ name: string; category: string; prompt: string; negative_prompt: string; default_mode: string; default_model_id: string; default_parameters: Record<string, any> }>) =>
    axios.put<PlaygroundTemplateResponse>(API_URL + "/playground/templates/" + id, data).then(r => r.data),

  deleteTemplate: (id: string) =>
    axios.delete(API_URL + "/playground/templates/" + id).then(r => r.data),

  // Upload media file for playground input (returns file path)
  uploadMedia: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return axios.post<{ path: string }>(API_URL + "/playground/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then(r => r.data);
  },

  deleteUpload: (path: string) =>
    axios.delete<{ ok: boolean }>(
      API_URL + "/playground/upload",
      { data: { path } },
    ).then(r => r.data),
};
