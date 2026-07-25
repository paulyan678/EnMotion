"use client";

import {
    AlertCircle,
    Check,
    CircleCheck,
    Film,
    Image as ImageIcon,
    Loader2,
    Upload,
    Wand2,
    X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import NextImage from "next/image";

import {
    clipFrameType,
    clipStartImageVariants,
    frameTaskStatus,
    selectedClipStartImage,
    type ClipStartImageVariant,
} from "@/lib/clipStartFrame";
import { api, type VideoTask } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";
import {
    configuredSecretFields,
    getSecretFieldForModel,
    isApprovedModelForCapability,
} from "@/lib/newApiModels";
import { VIDEO_I2V_MODELS } from "@/lib/modelCatalog";
import {
    useProjectStore,
    type Project,
    type StoryboardFrame,
    type VideoParams,
} from "@/store/projectStore";
import { toast } from "@/store/toastStore";

interface VideoCreatorProps {
    onTaskCreated: (project: Project) => void;
    remixData: Partial<VideoTask> | null;
    onRemixClear: () => void;
    params: VideoParams;
}

type ShotTaskStatus = ReturnType<typeof frameTaskStatus>;

function defaultMotionPrompt(frame: StoryboardFrame): string {
    const action = frame.action_description || frame.visual_description || frame.character_acting || "";
    const camera = frame.camera_movement_structured?.description || "";
    const dialogue = frame.dialogue ? ` ${frame.dialogue}` : "";
    return [action, camera, dialogue].filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
}

function apiErrorDetail(error: unknown): string | null {
    if (!error || typeof error !== "object") return null;
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    return typeof response?.data?.detail === "string" ? response.data.detail : null;
}

function supportsDuration(
    model: (typeof VIDEO_I2V_MODELS)[number],
    duration: number,
): boolean {
    if (model.duration.type === "fixed") return duration === model.duration.value;
    if (model.duration.type === "buttons") return model.duration.options.includes(duration);
    return duration >= model.duration.min
        && duration <= model.duration.max
        && (duration - model.duration.min) % model.duration.step === 0;
}

function statusClasses(status: ShotTaskStatus): string {
    if (status === "processing") return "border-primary/45 bg-primary/15 text-primary";
    if (status === "queued") return "border-amber-400/45 bg-amber-400/10 text-amber-300";
    if (status === "completed") return "border-emerald-400/45 bg-emerald-400/10 text-emerald-300";
    return "border-red-400/45 bg-red-400/10 text-red-300";
}

export default function VideoCreator({
    onTaskCreated,
    remixData,
    onRemixClear,
    params,
}: VideoCreatorProps) {
    const t = useTranslations("creator");
    const ts = useTranslations("storyboard");
    const tv = useTranslations("video");
    const locale = useLocale();
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const [selectedFrameId, setSelectedFrameId] = useState<string | null>(null);
    const [prompt, setPrompt] = useState("");
    const [isSavingPrompt, setIsSavingPrompt] = useState(false);
    const [isSelectingImage, setIsSelectingImage] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [isPolishing, setIsPolishing] = useState(false);
    const [retryingTaskId, setRetryingTaskId] = useState<string | null>(null);
    const [submitSuccess, setSubmitSuccess] = useState(false);
    const [inlineError, setInlineError] = useState<string | null>(null);
    const uploadInputRef = useRef<HTMLInputElement>(null);

    const frames = useMemo(
        () => (currentProject?.frames ?? []) as StoryboardFrame[],
        [currentProject?.frames],
    );
    const tasks = useMemo(
        () => (currentProject?.video_tasks ?? []) as VideoTask[],
        [currentProject?.video_tasks],
    );
    const selectedFrame = useMemo(
        () => frames.find((frame) => frame.id === selectedFrameId) ?? null,
        [frames, selectedFrameId],
    );
    const variants = useMemo(
        () => selectedFrame ? clipStartImageVariants(selectedFrame) : [],
        [selectedFrame],
    );
    const selectedImage = useMemo(
        () => selectedFrame ? selectedClipStartImage(selectedFrame, variants) : null,
        [selectedFrame, variants],
    );
    const selectedFrameType = selectedFrame ? clipFrameType(selectedFrame) : "static";
    const selectedTasks = useMemo(
        () => tasks.filter((task) => task.frame_id === selectedFrameId),
        [tasks, selectedFrameId],
    );
    const currentModel = VIDEO_I2V_MODELS.find((model) => model.id === params.model);

    useEffect(() => {
        if (!remixData) return;
        let canceled = false;
        queueMicrotask(() => {
            if (canceled) return;
            if (remixData.frame_id) setSelectedFrameId(remixData.frame_id);
            if (remixData.prompt) setPrompt(remixData.prompt);
            onRemixClear();
        });
        return () => { canceled = true; };
    }, [onRemixClear, remixData]);

    const applyUpdatedFrame = (updatedFrame: StoryboardFrame) => {
        if (!currentProject) return;
        updateProject(currentProject.id, {
            frames: frames.map((frame) => frame.id === updatedFrame.id ? updatedFrame : frame),
        });
    };

    const openShot = (frame: StoryboardFrame) => {
        setSelectedFrameId(frame.id);
        setPrompt(frame.video_prompt || defaultMotionPrompt(frame));
        setInlineError(null);
    };

    const savePrompt = async (nextPrompt = prompt): Promise<boolean> => {
        if (!currentProject || !selectedFrame || isSavingPrompt) return false;
        const clean = nextPrompt.trim();
        if (clean === (selectedFrame.video_prompt || "").trim()) return true;
        setIsSavingPrompt(true);
        try {
            const updated = await api.updateFrameWorkbench(currentProject.id, selectedFrame.id, {
                video_prompt: clean,
            });
            applyUpdatedFrame(updated);
            return true;
        } catch (error) {
            console.error("Failed to save shot motion prompt", error);
            setInlineError(t("promptSaveFailed"));
            return false;
        } finally {
            setIsSavingPrompt(false);
        }
    };

    const selectVariant = async (variant: ClipStartImageVariant) => {
        if (!currentProject || !selectedFrame || isSelectingImage) return;
        setIsSelectingImage(true);
        setInlineError(null);
        try {
            const t2iIndex = (selectedFrame.t2i_image_urls ?? []).findIndex(
                (url: string) => variant.url === url,
            );
            const updated = await api.updateFrameWorkbench(currentProject.id, selectedFrame.id, {
                clip_start_image_id: variant.id,
                clip_start_image_url: variant.url,
                ...(t2iIndex >= 0 ? { t2i_selected_index: t2iIndex } : {}),
            });
            applyUpdatedFrame(updated);
        } catch (error) {
            console.error("Failed to select clip start image", error);
            setInlineError(t("imageSelectionFailed"));
        } finally {
            setIsSelectingImage(false);
        }
    };

    const uploadImage = async (file: File | undefined) => {
        if (!file || !currentProject || !selectedFrame || isUploading) return;
        if (!new Set(["image/jpeg", "image/png", "image/webp"]).has(file.type)) {
            setInlineError(t("uploadTypeError"));
            return;
        }
        if (file.size > 8 * 1024 * 1024) {
            setInlineError(t("uploadSizeError"));
            return;
        }
        setIsUploading(true);
        setInlineError(null);
        try {
            const updated = await api.uploadT2IFrame(currentProject.id, selectedFrame.id, file);
            applyUpdatedFrame(updated);
            toast.success(t("uploadComplete"));
        } catch (error: unknown) {
            console.error("Shot image upload failed", error);
            setInlineError(apiErrorDetail(error) || t("uploadFailed"));
        } finally {
            setIsUploading(false);
            if (uploadInputRef.current) uploadInputRef.current.value = "";
        }
    };

    const polishPrompt = async () => {
        if (!currentProject || !selectedFrame || !prompt.trim() || isPolishing) return;
        setIsPolishing(true);
        setInlineError(null);
        try {
            const result = await api.polishVideoPrompt(
                prompt.trim(),
                "",
                currentProject.id,
                "",
                selectedImage ? [selectedImage.url] : [],
            );
            const polished = result.prompt_en || result.prompt_cn || prompt;
            setPrompt(polished);
            await savePrompt(polished);
        } catch (error) {
            console.error("Shot prompt polish failed", error);
            setInlineError(t("aiPolishFailed"));
        } finally {
            setIsPolishing(false);
        }
    };

    const disabledReason = (() => {
        if (!selectedFrame) return t("selectShotReason");
        if (!selectedImage) return variants.length ? t("selectStartImageReason") : t("uploadStartImageReason");
        if (!prompt.trim()) return t("enterMotionPromptReason");
        if (isSavingPrompt) return t("savingPrompt");
        if (!currentModel || !isApprovedModelForCapability(params.model, "video")) return t("selectVideoModelReason");
        if (
            !supportsDuration(currentModel, params.duration)
            || !currentModel.params.resolution?.options.includes(params.resolution)
            || !currentModel.params.ratio?.options.includes(params.ratio)
            || (params.seed !== undefined && currentModel.params.seed === false)
            || (params.generateAudio && currentModel.params.audio === false)
            || (params.watermark && currentModel.params.watermark === false)
        ) return t("invalidParametersReason");
        return null;
    })();

    const generateClip = async () => {
        if (!currentProject || !selectedFrame || !selectedImage || disabledReason || isSubmitting) return;
        setIsSubmitting(true);
        setInlineError(null);
        try {
            const secretField = getSecretFieldForModel(params.model, "video");
            const env = await api.getEnvConfig();
            const configured = configuredSecretFields(env as Record<string, unknown>);
            if (!secretField || !configured[secretField]) {
                setInlineError(t("configureModelKey", {
                    key: locale === "zh" ? t("selectedModelKey") : (secretField || t("selectedModelKey")),
                }));
                return;
            }

            if (!await savePrompt(prompt)) return;
            const created = await api.createVideoTask(currentProject.id, {
                image_url: selectedImage.url,
                source_image_id: selectedImage.id,
                frame_id: selectedFrame.id,
                frame_type: selectedFrameType,
                prompt: prompt.trim(),
                duration: params.duration,
                seed: params.seed,
                resolution: params.resolution,
                generate_audio: params.generateAudio,
                batch_size: params.batchSize,
                model: params.model,
                generation_mode: "i2v",
                ratio: params.ratio,
                watermark: params.watermark,
                workbench_tab: "t2i_i2v",
            });
            const createdTasks = Array.isArray(created) ? created : [created];
            onTaskCreated({
                ...currentProject,
                video_tasks: [...tasks, ...createdTasks],
            });
            setSubmitSuccess(true);
            window.setTimeout(() => setSubmitSuccess(false), 1500);
            toast.success(t("clipSubmitted"));
        } catch (error: unknown) {
            console.error("Clip submission failed", error);
            setInlineError(apiErrorDetail(error) || t("submitFailed"));
            try {
                onTaskCreated(await api.getProject(currentProject.id));
            } catch {
                // Preserve the actionable submission error when refresh fails.
            }
        } finally {
            setIsSubmitting(false);
        }
    };

    const retryTask = async (task: VideoTask) => {
        if (!currentProject || retryingTaskId) return;
        setRetryingTaskId(task.id);
        setInlineError(null);
        try {
            const retried = await api.retryVideoTask(currentProject.id, task.id);
            onTaskCreated({
                ...currentProject,
                video_tasks: tasks.map((item) => item.id === task.id ? retried : item),
            });
        } catch (error) {
            console.error("Failed to retry shot clip task", error);
            setInlineError(apiErrorDetail(error) || tv("retryTaskFailed"));
        } finally {
            setRetryingTaskId(null);
        }
    };

    return (
        <div className="relative h-full min-h-0 overflow-hidden">
            <div className="h-full overflow-y-auto p-5 custom-scrollbar sm:p-7">
                <div className="mx-auto w-full max-w-[1500px] pb-8">
                    <div className="mb-4 flex items-center justify-between gap-4">
                        <h2 className="text-lg font-semibold text-foreground">{t("clipStartFrame")}</h2>
                        <span className="text-xs text-text-muted">{t("shotCount", { count: frames.length })}</span>
                    </div>

                    {frames.length ? (
                        <div
                            data-testid="clip-start-frame-grid"
                            className="grid auto-rows-fr grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-4"
                        >
                            {frames.map((frame, index) => {
                                const frameVariants = clipStartImageVariants(frame);
                                const active = selectedClipStartImage(frame, frameVariants);
                                const status = frameTaskStatus(tasks, frame.id);
                                return (
                                    <button
                                        type="button"
                                        key={frame.id}
                                        onClick={() => openShot(frame)}
                                        aria-label={t("openShot", { number: index + 1 })}
                                        className="group min-w-0 overflow-hidden rounded-xl border border-glass-border bg-surface text-left transition hover:-translate-y-0.5 hover:border-primary/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                                    >
                                        <div className="relative aspect-video w-full overflow-hidden bg-black/25">
                                            {active ? (
                                                <NextImage
                                                    src={getAssetUrl(active.url)}
                                                    alt={t("shotImageAlt", { number: index + 1 })}
                                                    fill
                                                    sizes="(max-width: 640px) 100vw, (max-width: 1200px) 33vw, 240px"
                                                    className="object-cover"
                                                    unoptimized
                                                />
                                            ) : (
                                                <div className="flex h-full flex-col items-center justify-center gap-2 text-text-muted">
                                                    <ImageIcon size={24} aria-hidden="true" />
                                                    <span className="text-xs">{t("noImage")}</span>
                                                </div>
                                            )}
                                            <span className="absolute left-2 top-2 rounded-md bg-black/70 px-2 py-1 font-mono text-[11px] text-white">
                                                {t("shotNumber", { number: index + 1 })}
                                            </span>
                                            {frameVariants.length > 1 ? (
                                                <span className="absolute right-2 top-2 rounded-full bg-black/70 px-2 py-1 text-[10px] text-white">
                                                    {t("variantCount", { count: frameVariants.length })}
                                                </span>
                                            ) : null}
                                            {status ? (
                                                <span className={`absolute bottom-2 right-2 rounded-full border px-2 py-1 text-[10px] font-medium ${statusClasses(status)}`}>
                                                    {t(`taskStatus.${status}`)}
                                                </span>
                                            ) : null}
                                        </div>
                                        <div className="min-w-0 p-3">
                                            <p className="truncate text-sm font-medium text-foreground">
                                                {frame.action_description || frame.visual_description || t("untitledShot")}
                                            </p>
                                            <p className="mt-1 truncate text-xs text-text-muted">
                                                {ts(`frameTypes.${clipFrameType(frame)}`)}
                                            </p>
                                        </div>
                                    </button>
                                );
                            })}
                        </div>
                    ) : (
                        <div className="flex min-h-64 flex-col items-center justify-center rounded-xl border border-dashed border-glass-border text-text-muted">
                            <ImageIcon size={30} />
                            <p className="mt-3 text-sm">{t("noStoryboardFrames")}</p>
                        </div>
                    )}
                </div>
            </div>

            {selectedFrame ? (
                <div className="absolute inset-0 z-30 flex justify-end bg-black/45 backdrop-blur-[2px]" role="presentation">
                    <section
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="clip-shot-panel-title"
                        className="flex h-full w-full max-w-3xl flex-col border-l border-glass-border bg-surface shadow-2xl"
                    >
                        <header className="flex items-start justify-between gap-4 border-b border-glass-border px-5 py-4">
                            <div className="min-w-0">
                                <p className="font-mono text-xs uppercase tracking-widest text-primary">
                                    {t("shotNumber", { number: frames.findIndex((frame) => frame.id === selectedFrame.id) + 1 })}
                                </p>
                                <h3 id="clip-shot-panel-title" className="mt-1 truncate text-xl font-semibold text-foreground">
                                    {t("shotConfiguration")}
                                </h3>
                                <p className="mt-1 line-clamp-2 text-sm text-text-secondary">
                                    {selectedFrame.action_description || selectedFrame.visual_description || t("untitledShot")}
                                </p>
                            </div>
                            <button type="button" onClick={() => setSelectedFrameId(null)} aria-label={t("closeShotPanel")} className="rounded-lg p-2 text-text-muted hover:bg-glass hover:text-foreground">
                                <X size={20} />
                            </button>
                        </header>

                        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-5 custom-scrollbar">
                            <div className="grid gap-5 sm:grid-cols-[minmax(0,1.35fr)_minmax(180px,0.65fr)]">
                                <div className="relative aspect-video overflow-hidden rounded-xl border border-glass-border bg-black/25">
                                    {selectedImage ? (
                                        <NextImage
                                            src={getAssetUrl(selectedImage.url)}
                                            alt={t("selectedStartImageAlt")}
                                            fill
                                            sizes="(max-width: 640px) 100vw, 520px"
                                            className="object-cover"
                                            unoptimized
                                        />
                                    ) : (
                                        <div className="flex h-full flex-col items-center justify-center gap-2 text-text-muted">
                                            <ImageIcon size={30} />
                                            <span className="text-sm">{t("noImage")}</span>
                                        </div>
                                    )}
                                </div>
                                <dl className="space-y-3 rounded-xl border border-glass-border bg-glass p-4 text-sm">
                                    <div>
                                        <dt className="text-xs text-text-muted">{t("frameIdentity")}</dt>
                                        <dd className="mt-1 break-all font-mono text-xs text-foreground">{selectedFrame.id}</dd>
                                    </div>
                                    <div>
                                        <dt className="text-xs text-text-muted">{ts("frameType")}</dt>
                                        <dd className="mt-1 text-foreground">{ts(`frameTypes.${selectedFrameType}`)}</dd>
                                    </div>
                                    <div>
                                        <dt className="text-xs text-text-muted">{t("activeVideoModel")}</dt>
                                        <dd className="mt-1 text-foreground">{currentModel?.name ?? t("noVideoModel")}</dd>
                                    </div>
                                    <div>
                                        <dt className="text-xs text-text-muted">{t("clipParameters")}</dt>
                                        <dd className="mt-1 text-xs text-text-secondary">{params.resolution} · {params.ratio} · {params.duration}秒 · ×{params.batchSize}</dd>
                                    </div>
                                </dl>
                            </div>

                            <section>
                                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                                    <div>
                                        <h4 className="text-sm font-semibold text-foreground">{t("availableVariants")}</h4>
                                        <p className="mt-1 text-xs text-text-muted">{t("chooseExactVariant")}</p>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => uploadInputRef.current?.click()}
                                        disabled={isUploading}
                                        className="glass-button flex items-center gap-2 px-3 py-2 text-xs disabled:cursor-wait disabled:opacity-50"
                                    >
                                        {isUploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                                        {isUploading ? t("uploading") : t("uploadForShot")}
                                    </button>
                                    <input
                                        ref={uploadInputRef}
                                        type="file"
                                        accept="image/jpeg,image/png,image/webp"
                                        className="hidden"
                                        onChange={(event) => void uploadImage(event.target.files?.[0])}
                                    />
                                </div>
                                <div className="flex min-h-24 gap-3 overflow-x-auto pb-2">
                                    {variants.map((variant) => {
                                        const active = selectedImage?.id === variant.id;
                                        return (
                                            <button
                                                type="button"
                                                key={variant.id}
                                                onClick={() => void selectVariant(variant)}
                                                disabled={isSelectingImage}
                                                aria-pressed={active}
                                                className={`relative aspect-video h-24 shrink-0 overflow-hidden rounded-lg border-2 transition ${active ? "border-primary ring-2 ring-primary/25" : "border-transparent hover:border-primary/55"}`}
                                            >
                                                <NextImage
                                                    src={getAssetUrl(variant.url)}
                                                    alt={t("variantAlt")}
                                                    fill
                                                    sizes="180px"
                                                    className="object-cover"
                                                    unoptimized
                                                />
                                                {active ? <Check className="absolute right-1 top-1 rounded-full bg-primary p-0.5 text-white" size={18} /> : null}
                                            </button>
                                        );
                                    })}
                                    {!variants.length ? (
                                        <button type="button" onClick={() => uploadInputRef.current?.click()} className="flex aspect-video h-24 shrink-0 flex-col items-center justify-center rounded-lg border border-dashed border-glass-border px-6 text-xs text-text-muted hover:border-primary hover:text-primary">
                                            <Upload size={18} />
                                            <span className="mt-2">{t("uploadStartImageReason")}</span>
                                        </button>
                                    ) : null}
                                </div>
                            </section>

                            <section>
                                <div className="mb-2 flex items-center justify-between gap-3">
                                    <label htmlFor="shot-motion-prompt" className="text-sm font-semibold text-foreground">{t("promptLabel")}</label>
                                    <div className="flex items-center gap-2">
                                        {isSavingPrompt ? <span className="text-xs text-text-muted">{t("savingPrompt")}</span> : null}
                                        <button type="button" onClick={() => void polishPrompt()} disabled={!prompt.trim() || isPolishing} className="glass-button flex items-center gap-2 px-3 py-1.5 text-xs disabled:opacity-50">
                                            {isPolishing ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                                            {isPolishing ? t("polishing") : t("smartPromptPolish")}
                                        </button>
                                    </div>
                                </div>
                                <textarea
                                    id="shot-motion-prompt"
                                    value={prompt}
                                    onChange={(event) => setPrompt(event.target.value)}
                                    onBlur={() => void savePrompt()}
                                    placeholder={t("promptPlaceholder")}
                                    rows={5}
                                    className="glass-input w-full resize-y"
                                />
                            </section>

                            {inlineError ? (
                                <div role="alert" className="flex items-start gap-2 rounded-lg border border-red-400/30 bg-red-500/10 p-3 text-sm text-red-300">
                                    <AlertCircle size={17} className="mt-0.5 shrink-0" />
                                    <span>{inlineError}</span>
                                </div>
                            ) : null}

                            <section>
                                <h4 className="mb-3 text-sm font-semibold text-foreground">{t("shotResults")}</h4>
                                {selectedTasks.length ? (
                                    <div className="grid gap-3 sm:grid-cols-2">
                                        {[...selectedTasks].reverse().map((task) => (
                                            <div key={task.id} className="overflow-hidden rounded-lg border border-glass-border bg-glass">
                                                {task.status === "completed" && task.video_url ? (
                                                    <video src={getAssetUrl(task.video_url)} controls preload="metadata" className="aspect-video w-full bg-black object-cover" />
                                                ) : (
                                                    <div className="flex aspect-video items-center justify-center gap-2 text-sm text-text-muted">
                                                        {task.status === "processing" || task.status === "pending" ? <Loader2 size={18} className="animate-spin text-primary" /> : <AlertCircle size={18} className="text-red-400" />}
                                                        {t(`taskStatus.${task.status === "pending" ? "queued" : task.status}`)}
                                                    </div>
                                                )}
                                                <div className="space-y-2 px-3 py-2 text-xs text-text-muted">
                                                    <div className="flex items-center justify-between gap-2">
                                                        <span className="truncate">{task.model ? VIDEO_I2V_MODELS.find((model) => model.id === task.model)?.name || task.model : ""}</span>
                                                        <span className="font-mono">#{task.id.slice(0, 6)}</span>
                                                    </div>
                                                    {task.status === "failed" ? (
                                                        <button
                                                            type="button"
                                                            onClick={() => void retryTask(task)}
                                                            disabled={retryingTaskId !== null}
                                                            className="glass-button w-full py-1.5 text-xs text-red-300 disabled:cursor-wait disabled:opacity-50"
                                                        >
                                                            {retryingTaskId === task.id ? tv("retrying") : tv("retryTask")}
                                                        </button>
                                                    ) : null}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <p className="rounded-lg border border-dashed border-glass-border p-4 text-center text-sm text-text-muted">{t("noShotTasks")}</p>
                                )}
                            </section>
                        </div>

                        <footer className="border-t border-glass-border bg-surface px-5 py-4">
                            {disabledReason ? <p className="mb-2 text-xs text-amber-300">{disabledReason}</p> : null}
                            <button
                                type="button"
                                onClick={() => void generateClip()}
                                disabled={Boolean(disabledReason) || isSubmitting}
                                className={`flex w-full items-center justify-center gap-2 rounded-xl px-6 py-3 font-semibold text-white transition disabled:cursor-not-allowed disabled:opacity-45 ${submitSuccess ? "bg-emerald-500" : "bg-primary hover:bg-primary/90"}`}
                            >
                                {submitSuccess ? <CircleCheck size={18} /> : isSubmitting ? <Loader2 size={18} className="animate-spin" /> : <Film size={18} />}
                                {submitSuccess ? t("submitted") : isSubmitting ? t("submittingClip") : t("generateClip")}
                            </button>
                        </footer>
                    </section>
                </div>
            ) : null}
        </div>
    );
}
