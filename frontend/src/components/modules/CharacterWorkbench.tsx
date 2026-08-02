"use client";

import {
  ChevronDown,
  Image as ImageIcon,
  Loader2,
  Lock,
  RotateCcw,
  Sparkles,
  Video,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";

import AssetEditorShell from "@/components/assets/AssetEditorShell";
import { VariantSelector } from "@/components/common/VariantSelector";
import GenerationRequestReview from "@/components/generation/GenerationRequestReview";
import {
  VideoVariantSelector,
  type SelectableVideoVariant,
} from "@/components/common/VideoVariantSelector";
import type { CompiledGenerationRequest } from "@/lib/api";
import {
  assetUnitAsImageAsset,
  primaryAssetImage,
  primaryAssetImageUrl,
} from "@/lib/assetImage";
import { normalizeVisualWeight } from "@/lib/assetMetadata";
import { selectedVariantUrl } from "@/lib/characterImage";
import {
  PROJECT_IMAGE_MODELS,
  VIDEO_I2V_MODELS,
} from "@/lib/modelCatalog";
import { useModelDisplayName } from "@/lib/useModelDisplayName";
import { getAssetUrl } from "@/lib/utils";

export type CharacterImageKind =
  | "full_body"
  | "three_view"
  | "headshot"
  | "reference_sheet";

type CharacterOutput = "full_body" | "three_view" | "headshot";
type InspectorTab = "generate" | "details";
type EditorMode = "static" | "motion";

export const FICTIONAL_CHARACTER_PROMPT_NOTICE =
  "This is a fictional character created for animation and does not depict, identify, or imitate any real person.";

function withVisibleStyle(prompt: string, style: string) {
  const base = prompt.trim();
  const layer = style.trim().replace(/^,+|,+$/g, "");
  if (!layer || base.toLowerCase().includes(layer.toLowerCase())) return base;
  return `${base}, ${layer}`;
}

export interface CharacterMetadataDraft {
  name: string;
  assetType: "character" | "scene" | "prop";
  description: string;
  persona: string;
  age: string;
  gender: string;
  clothing: string;
  visualWeight: number;
  prompts: {
    reference_sheet_prompt: string;
    full_body_prompt: string;
    three_view_prompt: string;
    headshot_prompt: string;
    video_prompt: string;
    full_body_video_prompt: string;
    headshot_video_prompt: string;
  };
}

interface MotionGenerationOptions {
  model?: string;
  batchSize?: number;
  audioUrl?: string;
}

export interface CharacterWorkbenchProps {
  asset: any;
  onClose: () => void;
  onGenerate: (
    type: string,
    prompt: string,
    applyStyle: boolean,
    negativePrompt: string,
    batchSize: number,
    options?: {
      modelName?: string;
      aspectRatio?: string;
      templateId?: string;
    },
  ) => void;
  generatingTypes: { type: string; batchSize: number }[];
  stylePrompt?: string;
  styleNegativePrompt?: string;
  onGenerateVideo?: (
    prompt: string,
    duration: number,
    subType?: string,
    options?: MotionGenerationOptions,
  ) => void;
  onPreviewGeneration?: (
    type: string,
    prompt: string,
    applyStyle: boolean,
    negativePrompt: string,
    batchSize: number,
    options?: {
      modelName?: string;
      aspectRatio?: string;
      templateId?: string;
    },
  ) => Promise<CompiledGenerationRequest>;
  onPreviewVideo?: (
    prompt: string,
    duration: number,
    subType?: string,
    options?: MotionGenerationOptions,
  ) => Promise<CompiledGenerationRequest>;
  onSelectVideoVariant?: (
    subType: "full_body" | "head_shot",
    videoId: string,
  ) => Promise<void> | void;
  onDeleteVideo?: (
    subType: "full_body" | "head_shot",
    videoId: string,
  ) => Promise<void> | void;
  onFavoriteVideoVariant?: (
    subType: "full_body" | "head_shot",
    videoId: string,
    isFavorited: boolean,
  ) => Promise<void> | void;
  isGeneratingVideo?: boolean;
  onSelectVariant?: (
    type: CharacterImageKind,
    variantId: string,
  ) => Promise<void> | void;
  onDeleteVariant?: (
    type: CharacterImageKind,
    variantId: string,
  ) => Promise<void> | void;
  onFavoriteVariant?: (
    type: CharacterImageKind,
    variantId: string,
    isFavorited: boolean,
  ) => Promise<void> | void;
  onSaveMetadata: (
    draft: CharacterMetadataDraft,
  ) => Promise<boolean | void> | boolean | void;
  canChangeAssetType?: boolean;
  isSavingMetadata?: boolean;
  supportsMotion?: boolean;
  motionDisabledReason?: string;
  defaultModelName?: string;
  defaultVideoModelName?: string;
  defaultAspectRatio?: string;
}

function defaultImagePrompt(
  output: CharacterOutput,
  asset: any,
  hasReference: boolean,
) {
  const name = asset.name || "Character";
  const description = asset.description || "";
  const consistency = hasReference
    ? "STRICTLY MAINTAIN the SAME character appearance, face, hairstyle, skin tone, and clothing as the reference image. "
    : "";

  if (output === "full_body") {
    return `${consistency}${FICTIONAL_CHARACTER_PROMPT_NOTICE} Full body character design of ${name}. ${description}. Standing pose, neutral expression, looking at viewer. Clean simple background, isolated character, high quality concept art.`;
  }
  if (output === "three_view") {
    return `${consistency}${FICTIONAL_CHARACTER_PROMPT_NOTICE} Character reference sheet for ${name}. ${description}. Front, side, and back full-body views with consistent clothing and details. Neutral pose, clean studio background.`;
  }
  return `${consistency}${FICTIONAL_CHARACTER_PROMPT_NOTICE} Close-up portrait of the same character ${name}. ${description}. Face and shoulders, detailed facial features, neutral expression, clean studio background.`;
}

function defaultMotionPrompt(
  output: "full_body" | "headshot",
  asset: any,
  hasAudio: boolean,
) {
  if (output === "full_body") {
    return `Full-body character reference video.\n${FICTIONAL_CHARACTER_PROMPT_NOTICE}\n${asset.description || ""}.\nStanding pose, shifting weight slightly, natural hand gestures, and turning 30 degrees left and right.${
      hasAudio
        ? " Match the uploaded audio with accurate lip-sync and facial expressions."
        : " Speak naturally while counting from one to five."
    }\nHead-to-toe shot, stable camera, flat lighting.`;
  }
  return `High-fidelity portrait reference video.\n${FICTIONAL_CHARACTER_PROMPT_NOTICE}\n${asset.description || ""}.\nFacing camera with subtle head movement, blinking, and rich micro-expressions.${
    hasAudio
      ? " Match the uploaded audio with accurate lip-sync."
      : " Speak naturally while counting from one to five."
  }\nStudio lighting, stable camera.`;
}

export default function CharacterWorkbench({
  asset,
  onClose,
  onGenerate,
  generatingTypes = [],
  stylePrompt = "",
  onGenerateVideo,
  onPreviewGeneration,
  onPreviewVideo,
  onSelectVideoVariant,
  onDeleteVideo,
  onFavoriteVideoVariant,
  isGeneratingVideo = false,
  onSelectVariant,
  onDeleteVariant,
  onFavoriteVariant,
  onSaveMetadata,
  canChangeAssetType = false,
  isSavingMetadata = false,
  supportsMotion = true,
  motionDisabledReason,
  defaultModelName,
  defaultVideoModelName,
  defaultAspectRatio = "9:16",
}: CharacterWorkbenchProps) {
  const t = useTranslations("library");
  const tc = useTranslations("character");
  const modelDisplayName = useModelDisplayName();

  const masterImageAsset = primaryAssetImage(asset, "character");
  const masterImageUrl = primaryAssetImageUrl(asset, "character");
  const threeViewImageAsset =
    assetUnitAsImageAsset(asset.three_views) || asset.three_view_asset;
  const headshotImageAsset =
    assetUnitAsImageAsset(asset.head_shot) || asset.headshot_asset;
  const threeViewImageUrl =
    selectedVariantUrl(threeViewImageAsset) || asset.three_view_image_url;
  const headshotImageUrl =
    selectedVariantUrl(headshotImageAsset)
    || asset.headshot_image_url
    || asset.avatar_url;
  const masterGenerationType = asset.reference_sheet?.image_variants?.length
    ? "reference_sheet"
    : "full_body";

  const hasUploadedReference = Boolean(
    masterImageAsset?.variants?.some(
      (variant: any) => variant.is_uploaded_source,
    )
      || threeViewImageAsset?.variants?.some(
        (variant: any) => variant.is_uploaded_source,
      )
      || headshotImageAsset?.variants?.some(
        (variant: any) => variant.is_uploaded_source,
      ),
  );
  const hasMaster = Boolean(masterImageUrl);
  const hasReference = hasMaster || hasUploadedReference;

  const initialFullBodyPrompt = withVisibleStyle(
    asset.full_body_prompt
    || asset.reference_sheet?.image_prompt
    || defaultImagePrompt("full_body", asset, hasReference),
    stylePrompt,
  );
  const initialThreeViewPrompt = withVisibleStyle(
    asset.three_view_prompt
    || asset.three_views?.image_prompt
    || defaultImagePrompt("three_view", asset, hasReference),
    stylePrompt,
  );
  const initialHeadshotPrompt = withVisibleStyle(
    asset.headshot_prompt
    || asset.head_shot?.image_prompt
    || defaultImagePrompt("headshot", asset, hasReference),
    stylePrompt,
  );
  const initialFullBodyMotionPrompt =
    asset.full_body?.video_prompt
    || asset.reference_sheet?.video_prompt
    || asset.video_prompt
    || defaultMotionPrompt("full_body", asset, false);
  const initialHeadshotMotionPrompt =
    asset.head_shot?.video_prompt
    || defaultMotionPrompt("headshot", asset, false);

  const [activeOutput, setActiveOutput] =
    useState<CharacterOutput>("full_body");
  const [inspectorTab, setInspectorTab] =
    useState<InspectorTab>("generate");
  const [mode, setMode] = useState<EditorMode>("static");
  const [name, setName] = useState(asset.name || "");
  const [assetType, setAssetType] =
    useState<"character" | "scene" | "prop">("character");
  const [description, setDescription] = useState(asset.description || "");
  // Legacy structured metadata remains round-tripped for compatibility, but
  // the normal editing surface keeps generation intent in the full prompt.
  const persona = asset.persona || "";
  const age = asset.age || "";
  const gender = asset.gender || "";
  const clothing = asset.clothing || "";
  const visualWeight = normalizeVisualWeight(asset.visual_weight ?? 1);
  const [fullBodyPrompt, setFullBodyPrompt] =
    useState(initialFullBodyPrompt);
  const [threeViewPrompt, setThreeViewPrompt] =
    useState(initialThreeViewPrompt);
  const [headshotPrompt, setHeadshotPrompt] =
    useState(initialHeadshotPrompt);
  const [fullBodyMotionPrompt, setFullBodyMotionPrompt] =
    useState(initialFullBodyMotionPrompt);
  const [headshotMotionPrompt, setHeadshotMotionPrompt] =
    useState(initialHeadshotMotionPrompt);
  const negativePrompt = "";
  const applyStyle = false;
  const [modelName, setModelName] = useState(
    defaultModelName || PROJECT_IMAGE_MODELS[0]?.id || "",
  );
  const [videoModelName, setVideoModelName] = useState(
    defaultVideoModelName || VIDEO_I2V_MODELS[0]?.id || "",
  );
  const [aspectRatio, setAspectRatio] = useState(defaultAspectRatio);
  const [promptTemplate, setPromptTemplate] = useState("custom");
  const [batchSize, setBatchSize] = useState(1);
  const [motionBatchSize, setMotionBatchSize] = useState(1);
  const [duration, setDuration] = useState(5);

  const snapshot = JSON.stringify({
    name,
    assetType,
    description,
    persona,
    age,
    gender,
    clothing,
    visualWeight: normalizeVisualWeight(visualWeight),
    fullBodyPrompt,
    threeViewPrompt,
    headshotPrompt,
    fullBodyMotionPrompt,
    headshotMotionPrompt,
  });
  const [savedSnapshot, setSavedSnapshot] = useState(snapshot);
  const isDirty = snapshot !== savedSnapshot;
  const typeChanged = assetType !== "character";

  const outputs: Array<{
    id: CharacterOutput;
    label: string;
    description: string;
    imageUrl?: string;
    locked: boolean;
  }> = [
    {
      id: "full_body",
      label: t("fullBodyOutput"),
      description: tc("primaryReferenceDesc"),
      imageUrl: masterImageUrl,
      locked: false,
    },
    {
      id: "three_view",
      label: t("threeViewOutput"),
      description: tc("threeViewReferenceDesc"),
      imageUrl: threeViewImageUrl,
      locked: !threeViewImageUrl && !hasReference,
    },
    {
      id: "headshot",
      label: t("headshotOutput"),
      description: tc("headshotReferenceDesc"),
      imageUrl: headshotImageUrl,
      locked: !headshotImageUrl && !hasReference,
    },
  ];
  const currentOutput = outputs.find((output) => output.id === activeOutput)!;
  const activeImageAsset =
    activeOutput === "full_body"
      ? masterImageAsset
      : activeOutput === "three_view"
        ? threeViewImageAsset
        : headshotImageAsset;
  const activeImageKind: CharacterImageKind =
    activeOutput === "full_body" ? masterGenerationType : activeOutput;
  const activeImagePrompt =
    activeOutput === "full_body"
      ? fullBodyPrompt
      : activeOutput === "three_view"
        ? threeViewPrompt
        : headshotPrompt;
  const setActiveImagePrompt =
    activeOutput === "full_body"
      ? setFullBodyPrompt
      : activeOutput === "three_view"
        ? setThreeViewPrompt
        : setHeadshotPrompt;

  const motionOutput =
    activeOutput === "full_body" || activeOutput === "headshot";
  const motionType =
    activeOutput === "headshot" ? "head_shot" : "full_body";
  const activeMotionPrompt =
    activeOutput === "headshot"
      ? headshotMotionPrompt
      : fullBodyMotionPrompt;
  const setActiveMotionPrompt =
    activeOutput === "headshot"
      ? setHeadshotMotionPrompt
      : setFullBodyMotionPrompt;
  const activeMotionUnit =
    activeOutput === "headshot"
      ? asset.head_shot
      : asset.full_body || asset.reference_sheet;
  const motionVariants: SelectableVideoVariant[] =
    activeMotionUnit?.video_variants
    || (activeOutput === "full_body" ? asset.video_assets : [])
    || [];
  const selectedMotionId =
    activeMotionUnit?.selected_video_id
    || (activeOutput === "full_body" ? asset.selected_video_id : undefined);
  const motionReady =
    motionOutput
    && supportsMotion
    && Boolean(currentOutput.imageUrl)
    && !motionDisabledReason;
  const activeMode: EditorMode =
    mode === "motion" && motionOutput ? "motion" : "static";
  const generatingType =
    generatingTypes.find(
      (entry) =>
        entry.type === activeImageKind
        || entry.type === activeOutput
        || entry.type === "all",
    ) || null;
  const mutationDisabled =
    isSavingMetadata
    || typeChanged
    || Boolean(generatingType)
    || currentOutput.locked;

  const requestClose = () => {
    if (isSavingMetadata) return;
    if (isDirty && !window.confirm(t("unsavedChangesConfirm"))) return;
    onClose();
  };

  const saveMetadata = async () => {
    if (!name.trim() || isSavingMetadata || !isDirty) return;
    const result = await onSaveMetadata({
      name: name.trim(),
      assetType,
      description: description.trim(),
      persona: persona.trim(),
      age: age.trim(),
      gender: gender.trim(),
      clothing: clothing.trim(),
      visualWeight: normalizeVisualWeight(visualWeight),
      prompts: {
        reference_sheet_prompt: fullBodyPrompt,
        full_body_prompt: fullBodyPrompt,
        three_view_prompt: threeViewPrompt,
        headshot_prompt: headshotPrompt,
        video_prompt: fullBodyMotionPrompt,
        full_body_video_prompt: fullBodyMotionPrompt,
        headshot_video_prompt: headshotMotionPrompt,
      },
    });
    if (result !== false) setSavedSnapshot(snapshot);
  };

  const generateImage = () => {
    if (mutationDisabled) return;
    onGenerate(
      activeImageKind,
      activeImagePrompt,
      applyStyle,
      negativePrompt,
      batchSize,
      {
        modelName: modelName || undefined,
        aspectRatio,
        templateId:
          promptTemplate === "custom" ? undefined : promptTemplate,
      },
    );
  };

  const generateMotion = () => {
    if (!onGenerateVideo || !motionReady || isGeneratingVideo) return;
    onGenerateVideo(activeMotionPrompt, duration, motionType, {
      model: videoModelName || undefined,
      batchSize: motionBatchSize,
    });
  };

  const rail = (
    <div className="flex h-full flex-col">
      <div className="mb-3 px-1">
        <p className="text-[0.6875rem] font-bold uppercase tracking-[0.16em] text-text-muted">
          {t("outputsLabel")}
        </p>
        <p className="mt-1 text-xs leading-relaxed text-text-muted">
          {tc("tipConsistency")}
        </p>
      </div>
      <div className="flex gap-2 overflow-x-auto lg:flex-col lg:overflow-visible">
        {outputs.map((output, index) => {
          const active = output.id === activeOutput;
          const ready = Boolean(output.imageUrl);
          return (
            <button
              type="button"
              key={output.id}
              onClick={() => {
                setActiveOutput(output.id);
                if (output.id === "three_view") setMode("static");
              }}
              className={`group min-w-[190px] rounded-xl border p-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring lg:min-w-0 2xl:p-3.5 ${
                active
                  ? "border-primary/60 bg-primary/10"
                  : "border-transparent bg-black/10 hover:border-glass-border hover:bg-hover-bg"
              }`}
              aria-pressed={active}
            >
              <div className="flex items-center gap-2.5 2xl:gap-4">
                <span className="relative h-12 w-12 shrink-0 overflow-hidden rounded-lg border border-glass-border bg-elevated 2xl:h-20 2xl:w-20">
                  {output.imageUrl ? (
                    <img
                      src={getAssetUrl(output.imageUrl)}
                      alt=""
                      className="h-full w-full object-cover"
                    />
                  ) : (
                    <span className="grid h-full w-full place-items-center text-text-muted">
                      {output.locked ? (
                        <Lock size={16} aria-hidden="true" />
                      ) : (
                        <ImageIcon size={17} aria-hidden="true" />
                      )}
                    </span>
                  )}
                </span>
                <span className="min-w-0">
                  <span
                    className={`block truncate text-sm font-bold 2xl:text-base ${
                      active ? "text-primary" : "text-foreground"
                    }`}
                  >
                    {index + 1}. {output.label}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-text-muted">
                    {output.locked
                      ? t("lockedState")
                      : ready
                        ? t("readyState")
                        : t("notGeneratedState")}
                  </span>
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );

  const preview = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="mb-3 flex shrink-0 items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-bold text-foreground">
            {currentOutput.label}
          </h2>
          <p className="text-xs text-text-muted">{currentOutput.description}</p>
        </div>
        <span className="rounded-full border border-glass-border bg-glass px-2.5 py-1 text-xs text-text-secondary">
          {activeMode === "motion" ? tc("motionMode") : tc("staticMode")}
        </span>
      </div>

      <div className="min-h-0 flex-1">
        {currentOutput.locked ? (
          <div className="flex h-full min-h-[360px] flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-glass-border bg-elevated/40 px-8 text-center">
            <Lock size={28} className="text-text-muted" aria-hidden="true" />
            <p className="font-semibold text-foreground">
              {tc("generateMasterFirst")}
            </p>
            <p className="max-w-sm text-sm text-text-muted">
              {t("dependentOutputHint")}
            </p>
          </div>
        ) : activeMode === "motion" ? (
          <VideoVariantSelector
            videos={motionVariants}
            selectedId={selectedMotionId}
            onSelect={(id) =>
              void onSelectVideoVariant?.(motionType, id)
            }
            onDelete={(id) => void onDeleteVideo?.(motionType, id)}
            onFavorite={(id, favorite) =>
              void onFavoriteVideoVariant?.(motionType, id, favorite)
            }
            isGenerating={isGeneratingVideo}
            showGenerationControls={false}
            layout="stage"
            aspectRatio="9:16"
            fallbackImageUrl={currentOutput.imageUrl}
          />
        ) : (
          <VariantSelector
            asset={activeImageAsset}
            currentImageUrl={currentOutput.imageUrl}
            onSelect={(id) => void onSelectVariant?.(activeImageKind, id)}
            onDelete={(id) => void onDeleteVariant?.(activeImageKind, id)}
            onFavorite={(id, favorite) =>
              void onFavoriteVariant?.(activeImageKind, id, favorite)
            }
            isGenerating={Boolean(generatingType)}
            generatingBatchSize={generatingType?.batchSize}
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
            {motionOutput && supportsMotion ? (
              <div className="grid grid-cols-2 rounded-xl border border-glass-border bg-input-bg p-1">
                <button
                  type="button"
                  onClick={() => setMode("static")}
                  className={`inline-flex min-h-9 items-center justify-center gap-2 rounded-lg text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring ${
                    activeMode === "static"
                      ? "bg-primary text-primary-foreground"
                      : "text-text-secondary hover:text-foreground"
                  }`}
                  aria-pressed={activeMode === "static"}
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
                    || (!currentOutput.imageUrl
                      ? t("motionRequiresImage")
                      : undefined)
                  }
                  className={`inline-flex min-h-9 items-center justify-center gap-2 rounded-lg text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:cursor-not-allowed disabled:opacity-40 ${
                    activeMode === "motion"
                      ? "bg-primary text-primary-foreground"
                      : "text-text-secondary hover:text-foreground"
                  }`}
                  aria-pressed={activeMode === "motion"}
                >
                  <Video size={14} aria-hidden="true" />
                  {tc("motionMode")}
                </button>
              </div>
            ) : null}

            <label className="block space-y-2">
              <span className="text-xs font-bold uppercase tracking-[0.12em] text-text-muted">
                {activeMode === "motion"
                  ? t("videoPromptLabel")
                  : t("promptLabel")}
              </span>
              <textarea
                value={
                  activeMode === "motion"
                    ? activeMotionPrompt
                    : activeImagePrompt
                }
                onChange={(event) =>
                  activeMode === "motion"
                    ? setActiveMotionPrompt(event.target.value)
                    : setActiveImagePrompt(event.target.value)
                }
                className="min-h-36 w-full resize-y rounded-xl border border-glass-border bg-input-bg p-3 font-mono text-sm leading-relaxed text-foreground outline-none transition-colors focus:border-primary"
                placeholder={
                  activeMode === "motion"
                    ? tc("motionPromptPlaceholder")
                    : tc("promptPlaceholder")
                }
              />
            </label>

            {activeMode === "motion" ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() =>
                      setActiveMotionPrompt(
                        defaultMotionPrompt(
                          activeOutput === "headshot"
                            ? "headshot"
                            : "full_body",
                          asset,
                          false,
                        ),
                      )
                    }
                    className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-glass-border px-3 text-xs font-semibold text-text-secondary hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
                  >
                    <RotateCcw size={14} aria-hidden="true" />
                    {tc("resetRecommendedPrompt")}
                  </button>
                </div>

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
                    fingerprint={JSON.stringify({
                      activeMotionPrompt,
                      duration,
                      motionType,
                      videoModelName,
                      motionBatchSize,
                    })}
                    disabled={!motionReady || !activeMotionPrompt.trim() || typeChanged}
                    loadPreview={() => onPreviewVideo(
                      activeMotionPrompt,
                      duration,
                      motionType,
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
                  onClick={generateMotion}
                  disabled={
                    !motionReady
                    || isGeneratingVideo
                    || !activeMotionPrompt.trim()
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
                    : tc("generateMotionReference")}
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
                      options={["9:16", "3:4", "1:1", "16:9"].map(
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
                  <SelectField
                    label={t("promptTemplateLabel")}
                    value={promptTemplate}
                    onChange={setPromptTemplate}
                    options={[
                      {
                        value: "custom",
                        label: t("promptTemplateCustom"),
                      },
                      {
                        value: "character_reference",
                        label: t("promptTemplateReference"),
                      },
                      {
                        value: "cinematic_character",
                        label: t("promptTemplateCinematic"),
                      },
                    ]}
                  />
                </GenerationSettings>

                {onPreviewGeneration ? (
                  <GenerationRequestReview
                    fingerprint={JSON.stringify({
                      activeImageKind,
                      activeImagePrompt,
                      modelName,
                      aspectRatio,
                      batchSize,
                      promptTemplate,
                    })}
                    disabled={mutationDisabled || !activeImagePrompt.trim()}
                    loadPreview={() => onPreviewGeneration(
                      activeImageKind,
                      activeImagePrompt,
                      false,
                      "",
                      batchSize,
                      {
                        modelName: modelName || undefined,
                        aspectRatio,
                        templateId: promptTemplate === "custom" ? undefined : promptTemplate,
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
                  onClick={generateImage}
                  disabled={
                    mutationDisabled || !activeImagePrompt.trim()
                  }
                  className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-primary text-sm font-bold text-primary-foreground shadow-lg shadow-primary/20 transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:cursor-not-allowed disabled:opacity-45"
                >
                  {generatingType ? (
                    <Loader2 size={17} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Sparkles size={17} aria-hidden="true" />
                  )}
                  {generatingType ? t("generating") : t("generateOutput")}
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
              value={assetType}
              onChange={(value) =>
                setAssetType(value as "character" | "scene" | "prop")
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
      typeLabel={t("characterLabel")}
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
