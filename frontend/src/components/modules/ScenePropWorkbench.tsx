"use client";

import {
  ChevronDown,
  Image as ImageIcon,
  Loader2,
  Sparkles,
  Video,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";

import AssetEditorShell from "@/components/assets/AssetEditorShell";
import { VariantSelector } from "@/components/common/VariantSelector";
import { VideoVariantSelector } from "@/components/common/VideoVariantSelector";
import GenerationRequestReview from "@/components/generation/GenerationRequestReview";
import type { CompiledGenerationRequest, EditableAssetType } from "@/lib/api";
import { primaryAssetImage, primaryAssetImageUrl } from "@/lib/assetImage";
import { normalizeVisualWeight } from "@/lib/assetMetadata";
import {
  PROJECT_IMAGE_MODELS,
  VIDEO_I2V_MODELS,
} from "@/lib/modelCatalog";
import { useModelDisplayName } from "@/lib/useModelDisplayName";
import { getAssetUrl } from "@/lib/utils";
import type { Prop, Scene } from "@/store/projectStore";

type EditableScenePropAssetType = "character" | "scene" | "prop";
type InspectorTab = "generate" | "details";
type EditorMode = "static" | "motion";

function withVisibleStyle(prompt: string, style: string) {
  const base = prompt.trim();
  const layer = style.trim().replace(/^,+|,+$/g, "");
  if (!layer || base.toLowerCase().includes(layer.toLowerCase())) return base;
  return `${base}, ${layer}`;
}

export interface ScenePropMetadataDraft {
  assetType: EditableScenePropAssetType;
  attributes: Record<string, string | number>;
  prompt: string;
  videoPrompt: string;
}

interface MotionGenerationOptions {
  model?: string;
  batchSize?: number;
  audioUrl?: string;
}

export interface ScenePropWorkbenchProps {
  asset: Scene | Prop;
  assetType: Exclude<EditableAssetType, "character">;
  onClose: () => void;
  onGenerate: (
    prompt: string,
    applyStyle: boolean,
    negativePrompt: string,
    batchSize: number,
    options?: { modelName?: string; aspectRatio?: string },
  ) => void;
  isGenerating: boolean;
  generatingBatchSize?: number;
  stylePrompt?: string;
  styleNegativePrompt?: string;
  onGenerateVideo?: (
    prompt: string,
    duration: number,
    options?: MotionGenerationOptions,
  ) => void;
  onPreviewGeneration?: (
    prompt: string,
    applyStyle: boolean,
    negativePrompt: string,
    batchSize: number,
    options?: { modelName?: string; aspectRatio?: string },
  ) => Promise<CompiledGenerationRequest>;
  onPreviewVideo?: (
    prompt: string,
    duration: number,
    options?: MotionGenerationOptions,
  ) => Promise<CompiledGenerationRequest>;
  onSelectVideo?: (videoId: string) => Promise<void> | void;
  onDeleteVideo?: (videoId: string) => Promise<void> | void;
  onFavoriteVideo?: (
    videoId: string,
    isFavorited: boolean,
  ) => Promise<void> | void;
  isGeneratingVideo?: boolean;
  onSelectVariant?: (variantId: string) => Promise<void> | void;
  onDeleteVariant?: (variantId: string) => Promise<void> | void;
  onFavoriteVariant?: (
    variantId: string,
    isFavorited: boolean,
  ) => Promise<void> | void;
  onSaveMetadata: (
    draft: ScenePropMetadataDraft,
  ) => Promise<boolean | void> | boolean | void;
  canChangeAssetType?: boolean;
  isSavingMetadata?: boolean;
  supportsMotion?: boolean;
  motionDisabledReason?: string;
  ownerNotice?: ReactNode;
  defaultModelName?: string;
  defaultVideoModelName?: string;
  defaultAspectRatio?: string;
}

export default function ScenePropWorkbench({
  asset,
  assetType,
  onClose,
  onGenerate,
  isGenerating,
  generatingBatchSize,
  stylePrompt = "",
  onGenerateVideo,
  onPreviewGeneration,
  onPreviewVideo,
  onSelectVideo,
  onDeleteVideo,
  onFavoriteVideo,
  isGeneratingVideo = false,
  onSelectVariant,
  onDeleteVariant,
  onFavoriteVariant,
  onSaveMetadata,
  canChangeAssetType = false,
  isSavingMetadata = false,
  supportsMotion = true,
  motionDisabledReason,
  ownerNotice,
  defaultModelName,
  defaultVideoModelName,
  defaultAspectRatio,
}: ScenePropWorkbenchProps) {
  const t = useTranslations("library");
  const tc = useTranslations("character");
  const modelDisplayName = useModelDisplayName();
  const imageAsset = primaryAssetImage(asset, assetType);
  const imageUrl = primaryAssetImageUrl(asset, assetType);
  const initialPrompt = withVisibleStyle(
    asset.image_prompt || asset.description || "",
    stylePrompt,
  );
  const initialVideoPrompt =
    asset.video_prompt
    || `Cinematic ${assetType} reference of ${asset.name}. ${asset.description}. Subtle natural motion, stable composition, high quality.`;

  const [inspectorTab, setInspectorTab] =
    useState<InspectorTab>("generate");
  const [mode, setMode] = useState<EditorMode>("static");
  const [name, setName] = useState(asset.name || "");
  const [nextType, setNextType] =
    useState<EditableScenePropAssetType>(assetType);
  const [description, setDescription] = useState(asset.description || "");
  // Preserve legacy metadata without exposing a second prompt-building UI.
  const timeOfDay = "time_of_day" in asset ? asset.time_of_day || "" : "";
  const lightingMood = "lighting_mood" in asset ? asset.lighting_mood || "" : "";
  const visualWeight = normalizeVisualWeight(
    "visual_weight" in asset ? asset.visual_weight ?? 1 : 1,
  );
  const [prompt, setPrompt] = useState(initialPrompt);
  const [videoPrompt, setVideoPrompt] = useState(initialVideoPrompt);
  const negativePrompt = "";
  const applyStyle = false;
  const [modelName, setModelName] = useState(
    defaultModelName || PROJECT_IMAGE_MODELS[0]?.id || "",
  );
  const [videoModelName, setVideoModelName] = useState(
    defaultVideoModelName || VIDEO_I2V_MODELS[0]?.id || "",
  );
  const [aspectRatio, setAspectRatio] = useState(
    defaultAspectRatio || (assetType === "scene" ? "16:9" : "1:1"),
  );
  const [batchSize, setBatchSize] = useState(1);
  const [motionBatchSize, setMotionBatchSize] = useState(1);
  const [duration, setDuration] = useState(5);

  const snapshot = JSON.stringify({
    name,
    nextType,
    description,
    timeOfDay,
    lightingMood,
    visualWeight: normalizeVisualWeight(visualWeight),
    prompt,
    videoPrompt,
  });
  const [savedSnapshot, setSavedSnapshot] = useState(snapshot);
  const isDirty = snapshot !== savedSnapshot;
  const typeChanged = nextType !== assetType;
  const motionReady =
    supportsMotion && Boolean(imageUrl) && !motionDisabledReason;
  const mutationDisabled =
    isSavingMetadata || isGenerating || typeChanged;

  const requestClose = () => {
    if (isSavingMetadata) return;
    if (isDirty && !window.confirm(t("unsavedChangesConfirm"))) return;
    onClose();
  };

  const saveMetadata = async () => {
    if (!name.trim() || !isDirty || isSavingMetadata) return;
    const result = await onSaveMetadata({
      assetType: nextType,
      attributes: {
        name: name.trim(),
        description: description.trim(),
        ...(nextType === "scene"
          ? {
              time_of_day: timeOfDay.trim(),
              lighting_mood: lightingMood.trim(),
              visual_weight: normalizeVisualWeight(visualWeight),
            }
          : {}),
      },
      prompt,
      videoPrompt,
    });
    if (result !== false) setSavedSnapshot(snapshot);
  };

  const rail = (
    <div className="flex h-full flex-col">
      <p className="px-1 text-[0.6875rem] font-bold uppercase tracking-[0.16em] text-text-muted">
        {t("assetOverviewLabel")}
      </p>
      <div className="mt-3 rounded-xl border border-primary/45 bg-primary/10 p-3">
        <div className="aspect-video overflow-hidden rounded-lg border border-glass-border bg-elevated">
          {imageUrl ? (
            <img
              src={getAssetUrl(imageUrl)}
              alt=""
              className="h-full w-full object-cover"
            />
          ) : (
            <span className="grid h-full w-full place-items-center text-text-muted">
              <ImageIcon size={24} aria-hidden="true" />
            </span>
          )}
        </div>
        <h2 className="mt-3 truncate text-sm font-bold text-foreground">
          {name || asset.name}
        </h2>
        <p className="mt-1 text-xs text-text-muted">
          {imageUrl ? t("readyState") : t("notGeneratedState")}
        </p>
      </div>
      <dl className="mt-4 space-y-3 px-1 text-xs">
        <div className="flex items-center justify-between gap-3">
          <dt className="text-text-muted">{t("imageVariantsLabel")}</dt>
          <dd className="font-semibold text-foreground">
            {imageAsset?.variants?.length || 0}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-text-muted">{t("motionVariantsLabel")}</dt>
          <dd className="font-semibold text-foreground">
            {asset.video_assets?.length || 0}
          </dd>
        </div>
      </dl>
    </div>
  );

  const preview = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="mb-3 flex shrink-0 items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-bold text-foreground">
            {mode === "motion"
              ? t("motionReferenceLabel")
              : t("imageReferenceLabel")}
          </h2>
          <p className="text-xs text-text-muted">
            {mode === "motion"
              ? t("motionPreviewHint")
              : t("imagePreviewHint")}
          </p>
        </div>
        <span className="rounded-full border border-glass-border bg-glass px-2.5 py-1 text-xs text-text-secondary">
          {mode === "motion" ? tc("motionMode") : tc("staticMode")}
        </span>
      </div>
      <div className="min-h-0 flex-1">
        {mode === "motion" ? (
          <VideoVariantSelector
            videos={asset.video_assets || []}
            selectedId={asset.selected_video_id}
            onSelect={(id) => void onSelectVideo?.(id)}
            onDelete={(id) => void onDeleteVideo?.(id)}
            onFavorite={(id, favorite) =>
              void onFavoriteVideo?.(id, favorite)
            }
            isGenerating={isGeneratingVideo}
            showGenerationControls={false}
            layout="stage"
            aspectRatio={assetType === "scene" ? "16:9" : "1:1"}
            fallbackImageUrl={imageUrl}
          />
        ) : (
          <VariantSelector
            asset={imageAsset}
            currentImageUrl={imageUrl}
            onSelect={(id) => void onSelectVariant?.(id)}
            onDelete={(id) => void onDeleteVariant?.(id)}
            onFavorite={(id, favorite) =>
              void onFavoriteVariant?.(id, favorite)
            }
            isGenerating={isGenerating}
            generatingBatchSize={generatingBatchSize}
            disabled={mutationDisabled}
            showGenerationControls={false}
            layout="stage"
            aspectRatio={aspectRatio}
          />
        )}
      </div>
    </div>
  );

  const inspector = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="grid shrink-0 grid-cols-2 border-b border-glass-border p-2">
        {(["generate", "details"] as InspectorTab[]).map((tab) => (
          <button
            type="button"
            key={tab}
            onClick={() => setInspectorTab(tab)}
            className={`min-h-10 rounded-lg text-sm font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring ${
              inspectorTab === tab
                ? "bg-primary/15 text-primary"
                : "text-text-muted hover:bg-hover-bg hover:text-foreground"
            }`}
            aria-pressed={inspectorTab === tab}
          >
            {tab === "generate" ? t("generateTab") : t("detailsTab")}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
        {inspectorTab === "generate" ? (
          <div className="space-y-5">
            {supportsMotion ? (
              <div className="grid grid-cols-2 rounded-xl border border-glass-border bg-input-bg p-1">
                <button
                  type="button"
                  onClick={() => setMode("static")}
                  className={`inline-flex min-h-9 items-center justify-center gap-2 rounded-lg text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring ${
                    mode === "static"
                      ? "bg-primary text-primary-foreground"
                      : "text-text-secondary hover:text-foreground"
                  }`}
                  aria-pressed={mode === "static"}
                >
                  <ImageIcon size={14} aria-hidden="true" />
                  {tc("staticMode")}
                </button>
                <button
                  type="button"
                  onClick={() => motionReady && setMode("motion")}
                  disabled={!motionReady}
                  title={
                    motionDisabledReason
                    || (!imageUrl ? t("motionRequiresImage") : undefined)
                  }
                  className={`inline-flex min-h-9 items-center justify-center gap-2 rounded-lg text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:cursor-not-allowed disabled:opacity-40 ${
                    mode === "motion"
                      ? "bg-primary text-primary-foreground"
                      : "text-text-secondary hover:text-foreground"
                  }`}
                  aria-pressed={mode === "motion"}
                >
                  <Video size={14} aria-hidden="true" />
                  {tc("motionMode")}
                </button>
              </div>
            ) : null}

            <label className="block space-y-2">
              <span className="text-xs font-bold uppercase tracking-[0.12em] text-text-muted">
                {mode === "motion"
                  ? t("videoPromptLabel")
                  : t("promptLabel")}
              </span>
              <textarea
                value={mode === "motion" ? videoPrompt : prompt}
                onChange={(event) =>
                  mode === "motion"
                    ? setVideoPrompt(event.target.value)
                    : setPrompt(event.target.value)
                }
                className="min-h-36 w-full resize-y rounded-xl border border-glass-border bg-input-bg p-3 font-mono text-sm leading-relaxed text-foreground outline-none transition-colors focus:border-primary"
              />
            </label>

            {mode === "motion" ? (
              <>
                <GenerationSettings
                  label={t("generationSettingsLabel")}
                  summary={`${modelDisplayName(videoModelName)} · ${duration}秒 · ×${motionBatchSize}`}
                >
                  <SelectField
                    label={t("modelLabel")}
                    value={videoModelName}
                    onChange={setVideoModelName}
                    options={VIDEO_I2V_MODELS.map((model) => ({
                      value: model.id,
                      label: modelDisplayName(model.id, model.name),
                    }))}
                  />
                  <div className="grid grid-cols-2 gap-3">
                    <SelectField
                      label={t("durationLabel")}
                      value={String(duration)}
                      onChange={(value) => setDuration(Number(value))}
                      options={[
                        { value: "5", label: "5秒" },
                        { value: "10", label: "10秒" },
                        { value: "15", label: "15秒" },
                      ]}
                    />
                    <SelectField
                      label={t("batchSizeLabel")}
                      value={String(motionBatchSize)}
                      onChange={(value) =>
                        setMotionBatchSize(Number(value))
                      }
                      options={[1, 2, 3, 4].map((value) => ({
                        value: String(value),
                        label: `×${value}`,
                      }))}
                    />
                  </div>
                </GenerationSettings>
                {onPreviewVideo ? (
                  <GenerationRequestReview
                    fingerprint={JSON.stringify({ videoPrompt, duration, videoModelName, motionBatchSize })}
                    disabled={!motionReady || !videoPrompt.trim() || typeChanged}
                    loadPreview={() => onPreviewVideo(
                      videoPrompt,
                      duration,
                      {
                        model: videoModelName || undefined,
                        batchSize: motionBatchSize,
                      },
                    )}
                  />
                ) : null}
                {!motionReady ? (
                  <p className="rounded-lg border border-status-pending-border bg-status-pending-bg p-3 text-xs text-status-pending-fg">
                    {motionDisabledReason || t("motionRequiresImage")}
                  </p>
                ) : null}
                <button
                  type="button"
                  onClick={() =>
                    onGenerateVideo?.(videoPrompt, duration, {
                      model: videoModelName || undefined,
                      batchSize: motionBatchSize,
                    })
                  }
                  disabled={
                    !motionReady
                    || isGeneratingVideo
                    || !videoPrompt.trim()
                    || typeChanged
                  }
                  className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-primary text-sm font-bold text-primary-foreground shadow-lg shadow-primary/20 transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:cursor-not-allowed disabled:opacity-45"
                >
                  {isGeneratingVideo ? (
                    <Loader2 size={17} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Sparkles size={17} aria-hidden="true" />
                  )}
                  {isGeneratingVideo
                    ? t("generating")
                    : t("generateMotionOutput")}
                </button>
              </>
            ) : (
              <>
                <GenerationSettings
                  label={t("generationSettingsLabel")}
                  summary={`${modelDisplayName(modelName)} · ${aspectRatio} · ×${batchSize}`}
                >
                  <SelectField
                    label={t("modelLabel")}
                    value={modelName}
                    onChange={setModelName}
                    options={PROJECT_IMAGE_MODELS.map((model) => ({
                      value: model.id,
                      label: modelDisplayName(model.id, model.name),
                    }))}
                  />
                  <div className="grid grid-cols-2 gap-3">
                    <SelectField
                      label={t("aspectRatioLabel")}
                      value={aspectRatio}
                      onChange={setAspectRatio}
                      options={["16:9", "1:1", "9:16", "3:4"].map(
                        (value) => ({ value, label: value }),
                      )}
                    />
                    <SelectField
                      label={t("batchSizeLabel")}
                      value={String(batchSize)}
                      onChange={(value) => setBatchSize(Number(value))}
                      options={[1, 2, 3, 4].map((value) => ({
                        value: String(value),
                        label: `×${value}`,
                      }))}
                    />
                  </div>
                </GenerationSettings>

                {onPreviewGeneration ? (
                  <GenerationRequestReview
                    fingerprint={JSON.stringify({ prompt, modelName, aspectRatio, batchSize })}
                    disabled={mutationDisabled || !prompt.trim()}
                    loadPreview={() => onPreviewGeneration(
                      prompt,
                      false,
                      "",
                      batchSize,
                      {
                        modelName: modelName || undefined,
                        aspectRatio,
                      },
                    )}
                  />
                ) : null}

                {typeChanged ? (
                  <p className="text-xs text-status-pending-fg">
                    {t("saveTypeBeforeGenerating")}
                  </p>
                ) : null}
                <button
                  type="button"
                  onClick={() =>
                    onGenerate(
                      prompt,
                      applyStyle,
                      negativePrompt,
                      batchSize,
                      {
                        modelName: modelName || undefined,
                        aspectRatio,
                      },
                    )
                  }
                  disabled={
                    mutationDisabled || !prompt.trim()
                  }
                  className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-primary text-sm font-bold text-primary-foreground shadow-lg shadow-primary/20 transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:cursor-not-allowed disabled:opacity-45"
                >
                  {isGenerating ? (
                    <Loader2 size={17} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Sparkles size={17} aria-hidden="true" />
                  )}
                  {isGenerating ? t("generating") : t("generateOutput")}
                </button>
              </>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <Field
              label={t("nameLabel")}
              value={name}
              onChange={setName}
              required
            />
            <SelectField
              label={t("typeLabel")}
              value={nextType}
              onChange={(value) =>
                setNextType(value as EditableScenePropAssetType)
              }
              disabled={!canChangeAssetType || isSavingMetadata}
              options={[
                { value: "character", label: t("characterLabel") },
                { value: "scene", label: t("sceneLabel") },
                { value: "prop", label: t("propLabel") },
              ]}
            />
            {typeChanged ? (
              <p className="text-xs text-status-pending-fg">
                {t("saveTypeBeforeGenerating")}
              </p>
            ) : null}
            <TextAreaField
              label={t("descriptionLabel")}
              value={description}
              onChange={setDescription}
            />
          </div>
        )}
      </div>
    </div>
  );

  return (
    <AssetEditorShell
      title={name || asset.name || t("editorTitle")}
      typeLabel={
        assetType === "scene" ? t("sceneLabel") : t("propLabel")
      }
      ownerNotice={ownerNotice}
      isDirty={isDirty}
      isSaving={isSavingMetadata}
      saveDisabled={!name.trim()}
      onSave={() => void saveMetadata()}
      onRequestClose={requestClose}
      rail={rail}
      preview={preview}
      inspector={inspector}
    />
  );
}

function GenerationSettings({
  label,
  summary,
  children,
}: {
  label: string;
  summary: string;
  children: ReactNode;
}) {
  return (
    <details className="group rounded-xl border border-glass-border bg-black/10">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between px-3 text-sm font-semibold text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus-ring">
        <span>{label}</span>
        <span className="ml-auto flex min-w-0 items-center gap-2">
          <span className="max-w-48 truncate text-xs font-normal text-text-muted">
            {summary}
          </span>
          <ChevronDown
            size={15}
            className="shrink-0 transition-transform group-open:rotate-180"
            aria-hidden="true"
          />
        </span>
      </summary>
      <div className="space-y-3 border-t border-glass-border p-3">
        {children}
      </div>
    </details>
  );
}

function Field({
  label,
  value,
  onChange,
  required = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-semibold text-text-muted">{label}</span>
      <input
        value={value}
        required={required}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-11 w-full rounded-lg border border-glass-border bg-input-bg px-3 text-sm text-foreground outline-none focus:border-primary"
      />
    </label>
  );
}

function TextAreaField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-semibold text-text-muted">{label}</span>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-24 w-full resize-y rounded-lg border border-glass-border bg-input-bg p-3 text-sm text-foreground outline-none focus:border-primary"
      />
    </label>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
  disabled = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  disabled?: boolean;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-semibold text-text-muted">{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-11 w-full rounded-lg border border-glass-border bg-input-bg px-3 text-sm text-foreground outline-none focus:border-primary disabled:opacity-50"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
