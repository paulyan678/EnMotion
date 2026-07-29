"use client";

import { useState, useEffect, useMemo, useRef, type ChangeEvent } from "react";
import { useLocale, useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import { Check, Loader2, Film, AlertTriangle, Layout, Clock, FileText, Download, Music, Sliders, Package, RotateCcw, Upload } from "lucide-react";
import { useProjectStore, type Project, type StoryboardFrame, type VideoTask } from "@/store/projectStore";
import { api, type BgmPreset } from "@/lib/api";
import { saveAuthenticatedMedia } from "@/lib/download";
import { getAssetUrl, extractErrorDetail } from "@/lib/utils";
import StepPageHeader from "@/components/shared/StepPageHeader";
import SidePanelHeader from "@/components/shared/SidePanelHeader";
import { useModelDisplayName } from "@/lib/useModelDisplayName";
import ResizableSidePanel, {
    EPISODE_EDITOR_PANEL_STORAGE_KEYS,
} from "@/components/layout/ResizableSidePanel";

type AssemblyPhase = "takes" | "mix" | "export";
type DubAction = "preview" | "apply" | "revert";
type MixTrack = "dialogue" | "bgm" | "sfx";
type SupportedMixTrack = Exclude<MixTrack, "sfx">;
type ExportFormat = "mp4" | "webm";
type ExportSubtitleMode = "none" | "sidecar" | "embedded" | "burn-in";
type ExportResolution = "source" | "360p" | "480p" | "720p" | "1080p" | "2160p";
type ExportArtifact = {
    url: string;
    projectId: string;
    sourceMergedVideoUrl: string;
    resolution: ExportResolution;
    format: ExportFormat;
    subtitles: ExportSubtitleMode;
    outputStem: string;
};

const EXPORT_RESOLUTIONS: ExportResolution[] = [
    "source",
    "360p",
    "480p",
    "720p",
    "1080p",
    "2160p",
];

function safeMediaName(value: string): string {
    const normalized = value.trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
    return normalized || "enmotion";
}

function subtitleUrlFor(mediaUrl: string): string {
    const resolved = getAssetUrl(mediaUrl);
    const suffixIndex = resolved.search(/[?#]/);
    const path = suffixIndex >= 0 ? resolved.slice(0, suffixIndex) : resolved;
    const suffix = suffixIndex >= 0 ? resolved.slice(suffixIndex) : "";
    return `${path.replace(/\.[^./]+$/, "")}.srt${suffix}`;
}

export default function VideoAssembly() {
    const ta = useTranslations("assembly");
    const tStep = useTranslations("stepHeader");
    const modelDisplayName = useModelDisplayName();
    const locale = useLocale();
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const runningOps = useProjectStore((state) => state.runningOps);
    const setRunningOp = useProjectStore((state) => state.setRunningOp);
    const mergeOperationKey = currentProject
        ? `assembly-merge:${currentProject.id}`
        : null;
    const exportOperationKey = currentProject
        ? `assembly-export:${currentProject.id}`
        : null;
    const isMerging = Boolean(
        mergeOperationKey && runningOps[mergeOperationKey],
    );
    const isProjectExporting = Boolean(
        exportOperationKey && runningOps[exportOperationKey],
    );
    const isAssemblyBusy = isMerging || isProjectExporting;

    const [phase, setPhase] = useState<AssemblyPhase>(
        isAssemblyBusy ? "export" : "takes",
    );
    const [selectedFrameId, setSelectedFrameId] = useState<string | null>(null);
    const [mergeError, setMergeError] = useState<string | null>(null);
    const [isDownloading, setIsDownloading] = useState(false);
    const [dubAction, setDubAction] = useState<DubAction | null>(null);
    const [dubError, setDubError] = useState<string | null>(null);
    const dubActionRef = useRef<DubAction | null>(null);

    const handleExportBusyChange = (projectId: string, busy: boolean) => {
        setRunningOp(`assembly-export:${projectId}`, busy);
    };

    useEffect(() => {
        if (isAssemblyBusy) setPhase("export");
    }, [isAssemblyBusy]);

    // Group videos by frame
    const videoTasks = currentProject?.video_tasks;
    const videosByFrame = useMemo(() => {
        if (!videoTasks) return {};

        const grouped: Record<string, VideoTask[]> = {};
        videoTasks.forEach((task: VideoTask) => {
            if (task.status === "completed" && task.video_url) {
                if (task.frame_id) {
                    if (!grouped[task.frame_id]) grouped[task.frame_id] = [];
                    grouped[task.frame_id].push(task);
                }
            }
        });
        return grouped;
    }, [videoTasks]);

    const handleSelectVideo = async (frameId: string, videoId: string) => {
        if (!currentProject) return;
        setDubError(null);
        try {
            const updatedProject = await api.selectVideo(currentProject.id, frameId, videoId);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to select video:", error);
        }
    };

    const handleDubAction = async (
        action: DubAction,
        frame: StoryboardFrame,
        video: VideoTask,
    ) => {
        if (
            !currentProject
            || (action === "preview" && !frame.audio_url)
            || frame.selected_video_id !== video.id
            || dubActionRef.current
        ) {
            return;
        }
        dubActionRef.current = action;
        setDubAction(action);
        setDubError(null);
        try {
            const updatedProject = action === "preview"
                ? await api.previewDub(
                    currentProject.id,
                    frame.id,
                    video.id,
                    frame.dub_offset_ms ?? 0,
                )
                : action === "apply"
                    ? await api.applyDub(currentProject.id, frame.id)
                    : await api.revertDub(currentProject.id, frame.id);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error(`Failed to ${action} dub:`, error);
            setDubError(extractErrorDetail(error, ta("dubActionFailed")));
        } finally {
            dubActionRef.current = null;
            setDubAction(null);
        }
    };

    const handleMerge = async () => {
        if (!currentProject || isAssemblyBusy) return;
        const projectId = currentProject.id;
        const operationKey = `assembly-merge:${projectId}`;
        setRunningOp(operationKey, true);
        setMergeError(null);  // Clear previous errors

        try {
            const updatedProject = await api.mergeVideos(projectId);
            updateProject(projectId, updatedProject);
            // Success - error will be null, merged video will show below
        } catch (error: any) {
            console.error("Failed to merge videos:", error);

            // Extract detailed error message from backend
            const errorDetail = locale === "zh"
                ? ta("mergeUnknownError")
                : extractErrorDetail(error, ta("mergeUnknownError"));
            if (useProjectStore.getState().currentProject?.id === projectId) {
                setMergeError(errorDetail);
                // Also show alert for immediate feedback
                alert(`${ta("mergeFailedAlert")}:\n\n${errorDetail}`);
            }
        } finally {
            setRunningOp(operationKey, false);
        }
    };


    const handleDownload = async () => {
        if (!currentProject?.merged_video_url) return;
        setIsDownloading(true);
        try {
            await saveAuthenticatedMedia(
                getAssetUrl(currentProject.merged_video_url),
                `${safeMediaName(currentProject.title || currentProject.id)}_merged.mp4`,
            );
        } catch (error) {
            console.error("Failed to download video:", error);
            alert(ta("downloadFailed"));
        } finally {
            setIsDownloading(false);
        }
    };

    const selectedFrame = useMemo(() => {
        return currentProject?.frames?.find(
            (frame: StoryboardFrame) => frame.id === selectedFrameId,
        ) as StoryboardFrame | undefined;
    }, [currentProject?.frames, selectedFrameId]);

    const variants = selectedFrameId ? videosByFrame[selectedFrameId] || [] : [];

    const framesReady = currentProject?.frames?.filter((f: any) => f.selected_video_id).length ?? 0;
    const framesTotal = currentProject?.frames?.length ?? 0;

    return (
        // Layout v4: outer horizontal split. The compact step header belongs to main
        // column; right Variants panel is floor-to-ceiling with its own
        // SidePanelHeader.
        <div className="relative h-full flex overflow-hidden">
            {/* Left: main column */}
            <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
                <StepPageHeader
                    title={tStep("assemblyTitle")}
                />
                {/* PR-3k · Phase tabs — Takes / Mix / Export */}
                <div className="flex items-center gap-1 px-6 pt-2 border-b border-glass-border bg-surface">
                    {[
                        { id: "takes" as const,  label: ta("phaseTakes"),  icon: <Film size={12} /> },
                        { id: "mix" as const,    label: ta("phaseMix"),    icon: <Sliders size={12} /> },
                        { id: "export" as const, label: ta("phaseExport"), icon: <Package size={12} /> },
                    ].map((p) => (
                        <button
                            key={p.id}
                            onClick={() => setPhase(p.id)}
                            disabled={isAssemblyBusy && p.id !== "export"}
                            className={`relative inline-flex items-center gap-1.5 px-3 pb-2 font-mono text-[0.6875rem] uppercase tracking-[0.16em] transition-colors ${
                                phase === p.id
                                    ? "text-foreground"
                                    : "text-text-muted hover:text-text-secondary"
                            } disabled:cursor-not-allowed disabled:opacity-50`}
                        >
                            {p.icon}
                            {p.label}
                            {phase === p.id && (
                                <span className="absolute bottom-0 left-2 right-2 h-px bg-primary" aria-hidden="true" />
                            )}
                        </button>
                    ))}
                </div>
                {/* Takes phase body */}
                {phase === "takes" && (
                <div className="flex-1 overflow-y-auto custom-scrollbar p-6 space-y-4">
                        {currentProject?.frames?.map((frame: any, index: number) => {
                            const hasVideos = videosByFrame[frame.id]?.length > 0;
                            const isSelected = frame.id === selectedFrameId;
                            const selectedVideoId = frame.selected_video_id;
                            const selectedVideo = currentProject.video_tasks?.find((v: any) => v.id === selectedVideoId);

                            return (
                                <motion.div
                                    key={frame.id}
                                    layoutId={`frame-${frame.id}`}
                                    onClick={() => {
                                        setSelectedFrameId(frame.id);
                                        setDubError(null);
                                    }}
                                    onKeyDown={(event) => {
                                        if (event.key !== "Enter" && event.key !== " ") return;
                                        event.preventDefault();
                                        setSelectedFrameId(frame.id);
                                        setDubError(null);
                                    }}
                                    role="button"
                                    tabIndex={0}
                                    aria-label={ta("frameNumber", { index: index + 1 })}
                                    aria-pressed={isSelected}
                                    className={`group relative flex rounded-xl overflow-hidden cursor-pointer border transition-all bg-glass hover:bg-hover-bg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 ${isSelected ? "border-primary ring-1 ring-primary/50" :
                                        selectedVideoId ? "border-green-500/30" : "border-glass-border"
                                        }`}
                                >
                                    {/* Left: Preview */}
                                    <div className="w-48 aspect-video relative flex-shrink-0 border-r border-glass-border bg-elevated">
                                        {selectedVideo ? (
                                            <video
                                                src={getAssetUrl(
                                                    frame.dubbed_video_task_id === selectedVideo.id && frame.dubbed_video_url
                                                        ? frame.dubbed_video_url
                                                        : selectedVideo.video_url
                                                )}
                                                className="w-full h-full object-cover"
                                                muted
                                                onMouseOver={(e) => e.currentTarget.play()}
                                                onMouseOut={(e) => {
                                                    e.currentTarget.pause();
                                                    e.currentTarget.currentTime = 0;
                                                }}
                                            />
                                        ) : (
                                            <div className="w-full h-full relative">
                                                {frame.image_url ? (
                                                    <img
                                                        src={getAssetUrl(frame.image_url)}
                                                        className="w-full h-full object-cover opacity-50 grayscale"
                                                    />
                                                ) : (
                                                    <div className="w-full h-full bg-glass" />
                                                )}
                                                <div className="absolute inset-0 flex items-center justify-center">
                                                    {hasVideos ? (
                                                        <div className="bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded text-xs font-bold border border-yellow-500/50">
                                                            {ta("selectVideo")}
                                                        </div>
                                                    ) : (
                                                        <div className="bg-red-500/20 text-red-500 px-2 py-1 rounded text-xs font-bold border border-red-500/50">
                                                            {ta("noVideos")}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        )}
                                        <div className="absolute top-2 left-2 bg-surface px-2 py-0.5 rounded text-[0.625rem] font-mono text-foreground">
                                            #{index + 1}
                                        </div>
                                    </div>

                                    {/* Right: Details */}
                                    <div className="flex-1 p-4 flex flex-col justify-between min-w-0">
                                        <div className="space-y-2">
                                            <div className="flex items-start gap-2">
                                                <FileText size={14} className="text-text-muted mt-0.5 flex-shrink-0" />
                                                <p className="text-sm text-text-secondary line-clamp-2 leading-relaxed">
                                                    {frame.image_prompt || frame.action_description || ta("noPrompt")}
                                                </p>
                                            </div>
                                            {frame.dialogue && (
                                                <div className="flex items-start gap-2 pl-6 border-l-2 border-glass-border ml-1">
                                                    <p className="text-xs text-text-secondary italic">&quot;{frame.dialogue}&quot;</p>
                                                </div>
                                            )}
                                        </div>

                                        <div className="flex items-center justify-between mt-2 pt-2 border-t border-border-subtle">
                                            <div className="flex items-center gap-4 text-xs text-text-muted">
                                                <span className="flex items-center gap-1">
                                                    <Clock size={12} /> {selectedVideo ? ta("durationSeconds", { seconds: selectedVideo.duration }) : "--"}
                                                </span>
                                                <span className="flex items-center gap-1">
                                                    <Film size={12} /> {videosByFrame[frame.id]?.length || 0} {ta("variants")}
                                                </span>
                                            </div>

                                            {selectedVideoId && (
                                                <div className="flex items-center gap-1 text-green-500 text-xs font-bold">
                                                    <Check size={12} /> {ta("ready")}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </motion.div>
                            );
                        })}
                </div>
                )}

                {/* Mix phase body — BGM picker + per-track volume sliders */}
                {phase === "mix" && (
                    <MixPhase key={currentProject?.id ?? "no-project"}
                              scriptId={currentProject?.id ?? null}
                              bgmUrl={currentProject?.bgm_url ?? null}
                              mergedVideoUrl={currentProject?.merged_video_url ?? null}
                              mixSettings={currentProject?.mix_settings as Record<string, number> | undefined}
                              onChange={(updated) => currentProject && updateProject(currentProject.id, updated)}
                    />
                )}

                {/* Export phase body — merge action + final preview + download */}
                <div
                    className={
                        phase === "export"
                            ? "flex-1 overflow-y-auto custom-scrollbar p-8 space-y-6"
                            : "hidden"
                    }
                >
                        <ExportPhase
                            key={currentProject?.id ?? "no-project"}
                            projectId={currentProject?.id ?? null}
                            projectTitle={currentProject?.title ?? ""}
                            mergedVideoUrl={currentProject?.merged_video_url ?? null}
                            isMerging={isMerging}
                            isProjectExporting={isProjectExporting}
                            isDownloading={isDownloading}
                            mergeError={mergeError}
                            framesReady={framesReady}
                            framesTotal={framesTotal}
                            onMerge={handleMerge}
                            onDownload={handleDownload}
                            onDismissError={() => setMergeError(null)}
                            onExportBusyChange={handleExportBusyChange}
                        />
                </div>
                </div>

            {/* Right Sidebar - Variants — only visible in Takes phase */}
            {phase === "takes" && (
            <ResizableSidePanel
                side="right"
                storageKey={EPISODE_EDITOR_PANEL_STORAGE_KEYS.right}
                defaultWidth={360}
                minWidth={280}
                maxWidth={560}
                minRemainingWidth={360}
            >
            <div className="h-full w-full bg-surface flex flex-col z-10 border-l border-glass-border overflow-hidden">
                <SidePanelHeader
                    icon={<Film />}
                    title={ta("variants")}
                    subtitle={selectedFrameId
                        ? ta("frameNumber", { index: (currentProject?.frames?.findIndex((f: any) => f.id === selectedFrameId) ?? -1) + 1 })
                        : undefined}
                />
                <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
                        {selectedFrameId ? (
                            <div className="space-y-4">
                                {variants.length > 0 ? (
                                    variants.map((video: VideoTask, idx: number) => {
                                        const isSelected = selectedFrame?.selected_video_id === video.id;
                                        const isDubTarget = selectedFrame?.dubbed_video_task_id === video.id;
                                        const isPreviewTarget = selectedFrame?.preview_video_task_id === video.id;
                                        const hasPreview = isPreviewTarget && Boolean(selectedFrame?.preview_video_url);
                                        const hasAppliedDub = isDubTarget && Boolean(selectedFrame?.dubbed_video_url);
                                        const canPreview = Boolean(selectedFrame?.audio_url);
                                        const showDubControls = canPreview || hasPreview || hasAppliedDub;
                                        const displayedVideoUrl = hasPreview
                                            ? selectedFrame?.preview_video_url
                                            : hasAppliedDub
                                                ? selectedFrame?.dubbed_video_url
                                                : video.video_url;
                                        return (
                                            <div
                                                key={video.id}
                                                className={`rounded-xl overflow-hidden border transition-all group ${isSelected ? "border-green-500 ring-1 ring-green-500/50 bg-green-500/5" : "border-glass-border bg-glass hover:border-glass-border"
                                                    }`}
                                            >
                                                <div className="aspect-video relative bg-black">
                                                    <video
                                                        data-testid={`take-media-${video.id}`}
                                                        src={getAssetUrl(displayedVideoUrl)}
                                                        className="w-full h-full object-contain"
                                                        controls
                                                    />
                                                    {/* Overlay Info */}
                                                    <div className="absolute top-2 left-2 bg-surface px-1.5 rounded text-[0.625rem] text-foreground opacity-0 group-hover:opacity-100 transition-opacity">
                                                        {ta("durationSeconds", { seconds: video.duration ?? 0 })}
                                                    </div>
                                                </div>
                                                <div className="p-3">
                                                    <div className="flex items-center justify-between mb-2">
                                                        <div className="text-xs text-text-secondary">
                                                            {ta("variantNumber", { index: idx + 1 })}
                                                        </div>
                                                        <div className="text-[0.625rem] px-1.5 py-0.5 rounded bg-hover-bg text-text-secondary">
                                                            {modelDisplayName(video.model)}
                                                        </div>
                                                    </div>

                                                    {isSelected ? (
                                                        <div className="w-full py-2 bg-green-500/10 text-green-500 rounded-lg text-xs font-bold flex items-center justify-center gap-2 border border-green-500/20">
                                                            <Check size={14} /> {ta("selected")}
                                                        </div>
                                                    ) : (
                                                        <button
                                                            onClick={() => handleSelectVideo(selectedFrameId, video.id)}
                                                            disabled={Boolean(dubAction)}
                                                            className="w-full py-2 bg-hover-bg hover:bg-hover-bg rounded-lg text-xs font-medium transition-colors text-foreground"
                                                        >
                                                            {ta("selectThisVariant")}
                                                        </button>
                                                    )}
                                                    {isSelected && showDubControls ? (
                                                        <div className="mt-3 space-y-2 border-t border-border-subtle pt-3">
                                                            <div className="flex items-center justify-between gap-2">
                                                                <span className="font-mono text-[0.625rem] uppercase tracking-[0.14em] text-text-muted">
                                                                    {ta("dubSectionTitle")}
                                                                </span>
                                                                <span
                                                                    className={`text-[0.625rem] ${hasPreview || hasAppliedDub ? "text-green-400" : "text-text-secondary"}`}
                                                                    aria-live="polite"
                                                                >
                                                                    {hasPreview
                                                                        ? ta("dubPreviewReady")
                                                                        : hasAppliedDub
                                                                            ? ta("dubApplied")
                                                                            : ta("dubAudioReady")}
                                                                </span>
                                                            </div>
                                                            <div className="grid grid-cols-3 gap-1.5">
                                                                {(["preview", "apply", "revert"] as const).map((action) => {
                                                                    const unavailable = action === "apply"
                                                                        ? !hasPreview
                                                                        : action === "revert"
                                                                            ? !hasPreview && !hasAppliedDub
                                                                            : !canPreview;
                                                                    return (
                                                                        <button
                                                                            key={action}
                                                                            type="button"
                                                                            onClick={() => void handleDubAction(action, selectedFrame, video)}
                                                                            disabled={Boolean(dubAction) || unavailable}
                                                                            className="inline-flex min-h-8 items-center justify-center gap-1 rounded-md border border-glass-border bg-hover-bg px-2 text-[0.6875rem] font-medium text-foreground disabled:cursor-not-allowed disabled:opacity-40"
                                                                        >
                                                                            {dubAction === action
                                                                                ? <Loader2 size={11} className="animate-spin" />
                                                                                : action === "revert"
                                                                                    ? <RotateCcw size={11} />
                                                                                    : action === "apply"
                                                                                        ? <Check size={11} />
                                                                                        : <Film size={11} />}
                                                                            {ta(action === "preview" ? "previewDub" : action === "apply" ? "applyDub" : "revertDub")}
                                                                        </button>
                                                                    );
                                                                })}
                                                            </div>
                                                            {dubError ? (
                                                                <p role="alert" className="rounded-md border border-red-500/30 bg-red-500/10 px-2.5 py-2 text-[0.6875rem] text-red-300">
                                                                    {dubError}
                                                                </p>
                                                            ) : null}
                                                        </div>
                                                    ) : null}
                                                </div>
                                            </div>
                                        );
                                    })
                                ) : (
                                    <div className="text-center py-12 text-text-muted flex flex-col items-center">
                                        <AlertTriangle className="mb-3 opacity-50" size={32} />
                                        <p className="text-sm font-medium">{ta("noVideosGenerated")}</p>
                                        <p className="text-xs mt-1 max-w-[200px]">{ta("noVideosHint")}</p>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div className="h-full flex flex-col items-center justify-center text-text-muted gap-3">
                                <Layout size={48} className="opacity-10" />
                                <p className="text-sm">{ta("selectFrameHint")}</p>
                            </div>
                        )}
                    </div>
                </div>
            </ResizableSidePanel>
            )}
        </div>
    );
}

// ──────────────────────────────────────────────────────────────────
// PR-3k · Phase sub-components
// ──────────────────────────────────────────────────────────────────

function MixPhase({
    scriptId,
    bgmUrl,
    mergedVideoUrl,
    mixSettings,
    onChange,
}: {
    scriptId: string | null;
    bgmUrl: string | null;
    mergedVideoUrl: string | null;
    mixSettings: Record<string, number> | undefined;
    onChange: (updated: Partial<Project>) => void;
}) {
    const ta = useTranslations("assembly");
    const [presets, setPresets] = useState<BgmPreset[]>([]);
    const [loading, setLoading] = useState(true);
    const [savingBgm, setSavingBgm] = useState(false);
    const [uploadingBgm, setUploadingBgm] = useState(false);
    const [savingLevels, setSavingLevels] = useState(false);
    const [mixError, setMixError] = useState<string | null>(null);
    const pendingLevelWritesRef = useRef<Partial<Record<SupportedMixTrack, number>>>({});
    const levelSaveLoopRef = useRef<Promise<void> | null>(null);
    const bgmSaveInFlightRef = useRef(false);
    const customBgmInputRef = useRef<HTMLInputElement>(null);
    const mountedRef = useRef(true);
    const mixDefaults = { dialogue: 100, bgm: 35, sfx: 60 };
    const mix = { ...mixDefaults, ...mixSettings };
    const confirmedMixRef = useRef<Record<string, number>>({ ...mix });
    const confirmedMergedVideoUrlRef = useRef<string | null>(mergedVideoUrl);
    const selectedPreset = presets.find((preset) => preset.url === bgmUrl);
    const usesPresetNamespace = Boolean(
        bgmUrl?.replace(/\\/g, "/").startsWith("presets/bgm/"),
    );
    const customBgmSelected = Boolean(bgmUrl && !usesPresetNamespace && !selectedPreset);
    const customBgmFilename = customBgmSelected ? ta("mixCustomBgmSelected") : null;
    const hasMixableBgm = Boolean(
        bgmUrl
        && !loading
        && (usesPresetNamespace ? selectedPreset?.available === true : true),
    );

    useEffect(() => {
        let cancelled = false;
        api.listBgmPresets()
            .then((p) => { if (!cancelled) setPresets(p); })
            .catch(() => { if (!cancelled) setPresets([]); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
        };
    }, []);

    const handlePick = async (preset: BgmPreset | null) => {
        if (!scriptId || bgmSaveInFlightRef.current || levelSaveLoopRef.current) return;
        bgmSaveInFlightRef.current = true;
        setSavingBgm(true);
        setMixError(null);
        try {
            const updated = await api.updateAudioMix(scriptId, { bgm_url: preset ? preset.url : null });
            const confirmedMix = {
                ...mixDefaults,
                ...(updated.mix_settings ?? confirmedMixRef.current),
            };
            confirmedMixRef.current = confirmedMix;
            confirmedMergedVideoUrlRef.current = updated.merged_video_url ?? null;
            onChange({
                bgm_url: updated.bgm_url,
                mix_settings: {
                    ...confirmedMix,
                    ...pendingLevelWritesRef.current,
                },
                merged_video_url: updated.merged_video_url ?? null,
            });
        } catch (error) {
            console.error("Failed to update background music:", error);
            setMixError(extractErrorDetail(error, ta("mixSaveFailed")));
        } finally {
            bgmSaveInFlightRef.current = false;
            setSavingBgm(false);
        }
    };

    const handleCustomBgmUpload = async (event: ChangeEvent<HTMLInputElement>) => {
        const file = event.currentTarget.files?.[0];
        event.currentTarget.value = "";
        if (!file || !scriptId || bgmSaveInFlightRef.current || levelSaveLoopRef.current) return;
        if (file.size <= 0) {
            setMixError(ta("mixCustomBgmEmpty"));
            return;
        }
        if (file.size > 25 * 1024 * 1024) {
            setMixError(ta("mixCustomBgmTooLarge"));
            return;
        }

        bgmSaveInFlightRef.current = true;
        setSavingBgm(true);
        setUploadingBgm(true);
        setMixError(null);
        try {
            const updated = await api.uploadCustomBgm(scriptId, file);
            const confirmedMix = {
                ...mixDefaults,
                ...(updated.mix_settings ?? confirmedMixRef.current),
            };
            confirmedMixRef.current = confirmedMix;
            confirmedMergedVideoUrlRef.current = updated.merged_video_url ?? null;
            onChange({
                bgm_url: updated.bgm_url,
                mix_settings: confirmedMix,
                merged_video_url: updated.merged_video_url ?? null,
            });
        } catch (error) {
            console.error("Failed to upload custom background music:", error);
            setMixError(extractErrorDetail(error, ta("mixCustomBgmFailed")));
        } finally {
            bgmSaveInFlightRef.current = false;
            setSavingBgm(false);
            setUploadingBgm(false);
        }
    };

    const ensureLevelSaveLoop = () => {
        if (!scriptId || levelSaveLoopRef.current) return;
        const targetScriptId = scriptId;
        const loop = (async () => {
            if (mountedRef.current) setSavingLevels(true);
            while (Object.keys(pendingLevelWritesRef.current).length > 0) {
                const batch = pendingLevelWritesRef.current;
                pendingLevelWritesRef.current = {};
                const payload: {
                    dialogue_volume?: number;
                    bgm_volume?: number;
                } = {};
                if (batch.dialogue !== undefined) payload.dialogue_volume = batch.dialogue;
                if (batch.bgm !== undefined) payload.bgm_volume = batch.bgm;
                try {
                    const updated = await api.updateAudioMix(targetScriptId, payload);
                    const confirmedMix = {
                        ...confirmedMixRef.current,
                        ...batch,
                        ...(updated.mix_settings ?? {}),
                    };
                    confirmedMixRef.current = confirmedMix;
                    confirmedMergedVideoUrlRef.current = updated.merged_video_url ?? null;
                    onChange({
                        mix_settings: {
                            ...confirmedMix,
                            ...pendingLevelWritesRef.current,
                        },
                        merged_video_url: updated.merged_video_url ?? null,
                    });
                    if (mountedRef.current) {
                        setMixError(null);
                    }
                } catch (error) {
                    console.error("Failed to update audio mix levels:", error);
                    onChange({
                        mix_settings: {
                            ...confirmedMixRef.current,
                            ...pendingLevelWritesRef.current,
                        },
                        merged_video_url: confirmedMergedVideoUrlRef.current,
                    });
                    if (mountedRef.current) {
                        // The failed values are not durable. Revert them to the
                        // last server-confirmed mix while preserving any newer
                        // slider changes that are still queued.
                        setMixError(extractErrorDetail(error, ta("mixSaveFailed")));
                    }
                }
            }
        })();
        levelSaveLoopRef.current = loop;
        void loop.finally(() => {
            if (levelSaveLoopRef.current !== loop) return;
            levelSaveLoopRef.current = null;
            if (mountedRef.current) setSavingLevels(false);
            // A change can arrive after the loop observes an empty queue but
            // before this completion callback runs. Start another drain so the
            // user's final slider position is never dropped.
            if (Object.keys(pendingLevelWritesRef.current).length > 0) {
                ensureLevelSaveLoop();
            }
        });
    };

    const handleVolume = (track: SupportedMixTrack, value: number) => {
        if (!scriptId || !hasMixableBgm || bgmSaveInFlightRef.current) return;
        // Keep the slider responsive while serializing backend writes. New
        // values replace pending values for the same track, so the final value
        // is persisted last even when the user drags rapidly.
        pendingLevelWritesRef.current[track] = value;
        onChange({
            mix_settings: {
                ...confirmedMixRef.current,
                ...pendingLevelWritesRef.current,
            },
            merged_video_url: null,
        });
        setMixError(null);
        ensureLevelSaveLoop();
    };

    return (
        <div className="flex-1 overflow-y-auto custom-scrollbar p-8 space-y-8">
            {/* BGM picker */}
            <section>
                <h3 className="mb-3 flex items-center gap-2 font-mono text-[0.6875rem] uppercase tracking-[0.18em] text-text-muted">
                    <Music size={12} className="text-primary" />
                    {ta("mixBgmTitle")}
                    {savingBgm && <Loader2 size={12} className="animate-spin text-primary" />}
                </h3>
                {!loading && !presets.some((preset) => preset.available) && !customBgmSelected ? (
                    <div className="mb-3 flex items-start gap-2 rounded-md border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2">
                        <AlertTriangle size={13} className="mt-0.5 shrink-0 text-amber-400/85" aria-hidden="true" />
                        <p className="text-[0.71875rem] leading-relaxed text-amber-100/85">
                            {ta("mixBgmPreviewNotice")}
                        </p>
                    </div>
                ) : null}
                <input
                    ref={customBgmInputRef}
                    type="file"
                    accept="audio/*,.aac,.aif,.aiff,.flac,.m4a,.mp3,.ogg,.opus,.wav,.webm,.wma"
                    className="sr-only"
                    aria-label={ta("mixCustomBgm")}
                    onChange={handleCustomBgmUpload}
                />
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
                    <button
                        onClick={() => handlePick(null)}
                        disabled={savingBgm || savingLevels}
                        className={`rounded-lg border p-3 text-left transition-colors ${
                            !bgmUrl
                                ? "border-primary bg-[rgba(100,108,255,0.10)]"
                                : "border-glass-border bg-glass hover:border-foreground/30"
                        } disabled:cursor-not-allowed disabled:opacity-50`}
                    >
                        <p className="text-[0.8125rem] font-medium text-foreground">{ta("mixBgmNone")}</p>
                        <p className="mt-0.5 font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-text-muted">{ta("silent")}</p>
                    </button>
                    <button
                        type="button"
                        onClick={() => customBgmInputRef.current?.click()}
                        disabled={savingBgm || savingLevels}
                        aria-busy={uploadingBgm}
                        className={`rounded-lg border p-3 text-left transition-colors ${
                            customBgmSelected
                                ? "border-primary bg-[rgba(100,108,255,0.10)]"
                                : "border-glass-border bg-glass hover:border-foreground/30"
                        } disabled:cursor-not-allowed disabled:opacity-50`}
                    >
                        <span className="flex items-center gap-1.5 text-[0.8125rem] font-medium text-foreground">
                            {uploadingBgm
                                ? <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                                : <Upload size={12} aria-hidden="true" />}
                            {uploadingBgm ? ta("mixCustomBgmUploading") : ta("mixCustomBgm")}
                        </span>
                        <span className="mt-0.5 block truncate font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-text-muted">
                            {customBgmFilename ?? ta("mixCustomBgmHint")}
                        </span>
                    </button>
                    {loading ? (
                        <div className="col-span-3 grid place-items-center py-4 text-text-muted">
                            <Loader2 size={16} className="animate-spin" />
                        </div>
                    ) : (
                        presets.map((p) => {
                            const selected = bgmUrl === p.url;
                            return (
                                <button
                                    key={p.id}
                                    onClick={() => handlePick(p)}
                                    disabled={!p.available || savingBgm || savingLevels}
                                    aria-disabled={!p.available}
                                    title={!p.available ? ta("mixBgmUnavailable") : undefined}
                                    className={`rounded-lg border p-3 text-left transition-colors ${
                                        selected
                                            ? "border-primary bg-[rgba(100,108,255,0.10)]"
                                            : "border-glass-border bg-glass hover:border-foreground/30"
                                    } disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-glass-border`}
                                >
                                    <p className="text-[0.8125rem] font-medium text-foreground truncate">{ta(`bgmPresets.${p.id}`)}</p>
                                    <p className="mt-0.5 font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-text-muted">
                                        {p.available ? ta(`bgmMoods.${p.mood}`) : ta("mixBgmUnavailableShort")}
                                    </p>
                                </button>
                            );
                        })
                    )}
                </div>
            </section>

            {/* Volume sliders */}
            <section>
                <h3 className="mb-3 flex items-center gap-2 font-mono text-[0.6875rem] uppercase tracking-[0.18em] text-text-muted">
                    <Sliders size={12} className="text-primary" />
                    {ta("mixLevelsTitle")}
                    {savingLevels && <Loader2 size={12} className="animate-spin text-primary" />}
                </h3>
                <div className="space-y-3 max-w-lg">
                    {(["dialogue", "bgm", "sfx"] as const).map((track) => {
                        const unavailableReason = track === "sfx"
                            ? ta("mixSfxUnavailable")
                            : !hasMixableBgm
                                ? ta("mixLevelsRequireBgm")
                                : null;
                        const describedBy = track === "sfx"
                            ? "mix-sfx-unavailable"
                            : !hasMixableBgm
                                ? "mix-levels-require-bgm"
                                : undefined;
                        return (
                            <div key={track} className="flex items-center gap-3">
                                <span className="w-20 font-mono text-[0.6875rem] uppercase tracking-[0.16em] text-text-muted">{ta(`mixTrack.${track}`)}</span>
                                <input
                                    type="range"
                                    min={0}
                                    max={100}
                                    step={1}
                                    value={mix[track] ?? 0}
                                    aria-label={ta(`mixTrack.${track}`)}
                                    aria-describedby={describedBy}
                                    aria-busy={savingLevels}
                                    title={unavailableReason ?? undefined}
                                    disabled={Boolean(unavailableReason) || savingBgm}
                                    onChange={(event) => {
                                        if (track !== "sfx") {
                                            handleVolume(track, Number(event.target.value));
                                        }
                                    }}
                                    className="flex-1 accent-primary disabled:cursor-not-allowed disabled:opacity-40"
                                />
                                <span className="w-12 text-right font-mono text-[0.6875rem] text-text-secondary">{mix[track] ?? 0}</span>
                            </div>
                        );
                    })}
                </div>
                {!hasMixableBgm ? (
                    <p id="mix-levels-require-bgm" className="mt-3 max-w-lg text-[0.6875rem] text-amber-200/80">
                        {ta("mixLevelsRequireBgm")}
                    </p>
                ) : null}
                <p id="mix-sfx-unavailable" className="mt-2 max-w-lg text-[0.6875rem] text-text-muted">
                    {ta("mixSfxUnavailable")}
                </p>
                <p className="mt-2 text-[0.6875rem] text-text-muted max-w-lg">
                    {ta("mixHint")}
                </p>
                {mixError ? (
                    <p role="alert" className="mt-3 max-w-lg rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                        {mixError}
                    </p>
                ) : null}
            </section>
        </div>
    );
}

function ExportPhase({
    projectId,
    projectTitle,
    mergedVideoUrl,
    isMerging,
    isProjectExporting,
    isDownloading,
    mergeError,
    framesReady,
    framesTotal,
    onMerge,
    onDownload,
    onDismissError,
    onExportBusyChange,
}: {
    projectId: string | null;
    projectTitle: string;
    mergedVideoUrl: string | null;
    isMerging: boolean;
    isProjectExporting: boolean;
    isDownloading: boolean;
    mergeError: string | null;
    framesReady: number;
    framesTotal: number;
    onMerge: () => Promise<void>;
    onDownload: () => void;
    onDismissError: () => void;
    onExportBusyChange: (projectId: string, busy: boolean) => void;
}) {
    const ta = useTranslations("assembly");
    const allReady = framesTotal > 0 && framesReady === framesTotal;
    const [resolution, setResolution] = useState<ExportResolution>("1080p");
    const [format, setFormat] = useState<ExportFormat>("mp4");
    const [subtitles, setSubtitles] = useState<ExportSubtitleMode>("none");
    const [isExporting, setIsExporting] = useState(false);
    const [exportDownload, setExportDownload] = useState<"video" | "subtitles" | null>(null);
    const [exportedArtifact, setExportedArtifact] = useState<ExportArtifact | null>(null);
    const [exportError, setExportError] = useState<string | null>(null);
    // The parent keys this panel by project id, so each mounted instance owns
    // exactly one project's request lifecycle.
    const ownedProjectIdRef = useRef(projectId);
    const sourceMergedVideoUrlRef = useRef(mergedVideoUrl);
    const exportRequestSequenceRef = useRef(0);
    const operationRef = useRef<"merge" | "export" | null>(null);
    const exportBusy = isExporting || isProjectExporting;
    const availableSubtitles: ExportSubtitleMode[] = format === "mp4"
        ? ["none", "sidecar", "embedded", "burn-in"]
        : ["none", "sidecar", "burn-in"];
    const outputStem = `${safeMediaName(projectTitle || projectId || "enmotion")}_${resolution}`;

    useEffect(() => {
        return () => {
            // Invalidate a request owned by this project-specific panel before
            // its promise can publish into a later render.
            exportRequestSequenceRef.current += 1;
        };
    }, []);

    useEffect(() => {
        if (sourceMergedVideoUrlRef.current === mergedVideoUrl) return;
        sourceMergedVideoUrlRef.current = mergedVideoUrl;
        exportRequestSequenceRef.current += 1;
        if (operationRef.current !== "export") setIsExporting(false);
        setExportedArtifact(null);
        setExportError(null);
    }, [mergedVideoUrl]);

    const changeFormat = (nextFormat: ExportFormat) => {
        setFormat(nextFormat);
        if (nextFormat === "webm" && subtitles === "embedded") setSubtitles("none");
        setExportedArtifact(null);
        setExportError(null);
    };

    const handleExport = async () => {
        if (
            !projectId
            || !mergedVideoUrl
            || isMerging
            || exportBusy
            || operationRef.current
        ) return;
        const requestProjectId = projectId;
        const requestSourceMergedVideoUrl = mergedVideoUrl;
        const requestSequence = exportRequestSequenceRef.current + 1;
        exportRequestSequenceRef.current = requestSequence;
        operationRef.current = "export";
        const requestedArtifact = {
            projectId: requestProjectId,
            sourceMergedVideoUrl: requestSourceMergedVideoUrl,
            resolution,
            format,
            subtitles,
            outputStem,
        };
        setIsExporting(true);
        onExportBusyChange(requestProjectId, true);
        setExportError(null);
        setExportedArtifact(null);
        try {
            const result = await api.exportProject(requestProjectId, {
                resolution,
                format,
                subtitles,
            });
            if (
                exportRequestSequenceRef.current !== requestSequence
                || ownedProjectIdRef.current !== requestProjectId
                || sourceMergedVideoUrlRef.current !== requestSourceMergedVideoUrl
            ) {
                return;
            }
            if (!result || typeof result.url !== "string" || !result.url) {
                throw new Error(ta("invalidExportResponse"));
            }
            setExportedArtifact({ ...requestedArtifact, url: result.url });
        } catch (error) {
            if (
                exportRequestSequenceRef.current !== requestSequence
                || ownedProjectIdRef.current !== requestProjectId
                || sourceMergedVideoUrlRef.current !== requestSourceMergedVideoUrl
            ) {
                return;
            }
            console.error("Failed to export project:", error);
            setExportError(extractErrorDetail(error, ta("exportFailed")));
        } finally {
            if (operationRef.current === "export") {
                setIsExporting(false);
                operationRef.current = null;
            }
            onExportBusyChange(requestProjectId, false);
        }
    };

    const handleMergeRequest = async () => {
        if (isMerging || exportBusy || operationRef.current) return;
        operationRef.current = "merge";
        try {
            await onMerge();
        } finally {
            if (operationRef.current === "merge") operationRef.current = null;
        }
    };

    const handleExportDownload = async (kind: "video" | "subtitles") => {
        if (
            !exportedArtifact
            || exportedArtifact.sourceMergedVideoUrl !== mergedVideoUrl
            || exportDownload
        ) return;
        const artifact = exportedArtifact;
        setExportDownload(kind);
        setExportError(null);
        try {
            await saveAuthenticatedMedia(
                kind === "video" ? getAssetUrl(artifact.url) : subtitleUrlFor(artifact.url),
                kind === "video"
                    ? `${artifact.outputStem}.${artifact.format}`
                    : `${artifact.outputStem}.srt`,
            );
        } catch (error) {
            console.error("Failed to download exported media:", error);
            setExportError(extractErrorDetail(error, ta("exportDownloadFailed")));
        } finally {
            setExportDownload(null);
        }
    };

    return (
        <div className="space-y-6 max-w-3xl">
            <section className="rounded-xl border border-glass-border bg-glass p-6">
                <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                        <h3 className="text-display font-medium text-foreground flex items-center gap-2">
                            <Package size={16} className="text-primary" />
                            {ta("exportTitle")}
                        </h3>
                        <p className="mt-1 text-body-sm text-text-secondary">
                            {ta("exportSubtitle", { ready: framesReady, total: framesTotal })}
                        </p>
                    </div>
                    <button
                        onClick={() => void handleMergeRequest()}
                        disabled={isMerging || exportBusy || !allReady}
                        className="shrink-0 inline-flex items-center gap-2 bg-primary text-white border border-[rgba(100,108,255,0.65)] shadow-[inset_0_1.5px_0_rgba(255,255,255,0.14)] hover:bg-primary-hover disabled:opacity-40 disabled:cursor-not-allowed px-5 py-2.5 rounded-md font-semibold text-[0.8125rem]"
                    >
                        {isMerging ? <Loader2 size={14} className="animate-spin" /> : <Film size={14} />}
                        {ta("mergeAndProceed")}
                    </button>
                </div>
            </section>

            {mergeError && (
                <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4">
                    <div className="flex items-start gap-3">
                        <AlertTriangle className="text-red-500 flex-shrink-0 mt-0.5" size={20} />
                        <div className="flex-1 min-w-0">
                            <h4 className="text-sm font-bold text-red-400 mb-1">{ta("mergeFailed")}</h4>
                            <p className="text-xs text-red-300/90 whitespace-pre-wrap leading-relaxed font-mono break-all">
                                {mergeError}
                            </p>
                            {mergeError.toLowerCase().includes("ffmpeg") && (
                                <a href="https://ffmpeg.org/download.html" target="_blank" rel="noopener noreferrer" className="text-xs text-blue-400 hover:text-blue-300 underline mt-2 inline-block">
                                    {ta("downloadFfmpeg")}
                                </a>
                            )}
                            <button onClick={onDismissError} className="mt-3 text-xs text-text-secondary hover:text-foreground underline">
                                {ta("dismiss")}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            <AnimatePresence>
                {mergedVideoUrl && (
                    <motion.section
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        className="rounded-xl border border-glass-border bg-elevated overflow-hidden"
                    >
                        <div className="grid md:grid-cols-2 gap-0">
                            <div className="aspect-video bg-black">
                                <video src={getAssetUrl(mergedVideoUrl)} className="w-full h-full object-contain" controls />
                            </div>
                            <div className="p-5 flex flex-col justify-center gap-3">
                                <div>
                                    <h3 className="text-display font-medium text-foreground flex items-center gap-2">
                                        <Check className="text-green-500" size={16} />
                                        {ta("mergedVideoReady")}
                                    </h3>
                                    <p className="text-body-sm text-text-secondary mt-1">{ta("mergedVideoDesc")}</p>
                                </div>
                                <button
                                    onClick={onDownload}
                                    disabled={isDownloading}
                                    className="self-start inline-flex items-center gap-2 px-5 py-2.5 rounded-md bg-glass border border-glass-border text-foreground hover:bg-hover-bg transition-colors text-[0.8125rem] font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    <Download size={14} />
                                    {isDownloading ? ta("downloading") : ta("downloadMerged")}
                                </button>
                            </div>
                        </div>
                        <div className="border-t border-glass-border p-5">
                            <h4 className="font-mono text-[0.6875rem] uppercase tracking-[0.16em] text-text-muted">
                                {ta("exportSettings")}
                            </h4>
                            <div className="mt-3 grid gap-3 sm:grid-cols-3">
                                <label className="space-y-1 text-xs text-text-secondary">
                                    <span>{ta("resolutionLabel")}</span>
                                    <select
                                        aria-label={ta("resolutionLabel")}
                                        value={resolution}
                                        onChange={(event) => {
                                            setResolution(event.target.value as ExportResolution);
                                            setExportedArtifact(null);
                                            setExportError(null);
                                        }}
                                        disabled={exportBusy}
                                        className="w-full rounded-md border border-glass-border bg-surface px-2.5 py-2 text-foreground"
                                    >
                                        {EXPORT_RESOLUTIONS.map((value) => (
                                            <option key={value} value={value}>
                                                {value === "source" ? ta("resolutionSource") : value}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                                <label className="space-y-1 text-xs text-text-secondary">
                                    <span>{ta("formatLabel")}</span>
                                    <select
                                        aria-label={ta("formatLabel")}
                                        value={format}
                                        onChange={(event) => changeFormat(event.target.value as ExportFormat)}
                                        disabled={exportBusy}
                                        className="w-full rounded-md border border-glass-border bg-surface px-2.5 py-2 text-foreground"
                                    >
                                        <option value="mp4">{ta("format.mp4")}</option>
                                        <option value="webm">{ta("format.webm")}</option>
                                    </select>
                                </label>
                                <label className="space-y-1 text-xs text-text-secondary">
                                    <span>{ta("subtitleLabel")}</span>
                                    <select
                                        aria-label={ta("subtitleLabel")}
                                        value={subtitles}
                                        onChange={(event) => {
                                            setSubtitles(event.target.value as ExportSubtitleMode);
                                            setExportedArtifact(null);
                                            setExportError(null);
                                        }}
                                        disabled={exportBusy}
                                        className="w-full rounded-md border border-glass-border bg-surface px-2.5 py-2 text-foreground"
                                    >
                                        {availableSubtitles.map((value) => (
                                            <option key={value} value={value}>
                                                {ta(`subtitleMode.${value}`)}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                            </div>
                            <button
                                type="button"
                                onClick={() => void handleExport()}
                                disabled={exportBusy || isMerging || !projectId}
                                className="mt-4 inline-flex items-center gap-2 rounded-md bg-primary px-5 py-2.5 text-[0.8125rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                {exportBusy ? <Loader2 size={14} className="animate-spin" /> : <Package size={14} />}
                                {exportBusy ? ta("exporting") : ta("exportFinal")}
                            </button>
                            {exportError ? (
                                <p role="alert" className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                                    {exportError}
                                </p>
                            ) : null}
                            {exportedArtifact?.sourceMergedVideoUrl === mergedVideoUrl ? (
                                <div className="mt-4 rounded-lg border border-green-500/25 bg-green-500/[0.06] p-3">
                                    <p className="text-xs font-medium text-green-400">{ta("exportOutput")}</p>
                                    <code className="mt-1 block break-all text-[0.6875rem] text-text-secondary">{exportedArtifact.url}</code>
                                    <div className="mt-3 flex flex-wrap gap-2">
                                        <button
                                            type="button"
                                            onClick={() => void handleExportDownload("video")}
                                            disabled={Boolean(exportDownload)}
                                            className="inline-flex items-center gap-1.5 rounded-md border border-glass-border bg-glass px-3 py-2 text-xs text-foreground disabled:opacity-50"
                                        >
                                            {exportDownload === "video" ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
                                            {ta("downloadExport")}
                                        </button>
                                        {exportedArtifact.subtitles === "sidecar" ? (
                                            <button
                                                type="button"
                                                onClick={() => void handleExportDownload("subtitles")}
                                                disabled={Boolean(exportDownload)}
                                                className="inline-flex items-center gap-1.5 rounded-md border border-glass-border bg-glass px-3 py-2 text-xs text-foreground disabled:opacity-50"
                                            >
                                                {exportDownload === "subtitles" ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
                                                {ta("downloadSubtitle")}
                                            </button>
                                        ) : null}
                                    </div>
                                </div>
                            ) : null}
                        </div>
                    </motion.section>
                )}
            </AnimatePresence>
        </div>
    );
}
