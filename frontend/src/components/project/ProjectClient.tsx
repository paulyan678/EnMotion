"use client";

import { useEffect, useState, useMemo } from "react";
import dynamic from "next/dynamic";
import { Palette, Layout, Film, BookOpen, Users, Video, Clapperboard } from "lucide-react";
import { useTranslations } from "next-intl";
import { useProjectStore } from "@/store/projectStore";
import PipelineSidebar from "@/components/layout/PipelineSidebar";
import EpisodeMiniList from "@/components/layout/EpisodeMiniList";
import type { BreadcrumbSegment } from "@/components/layout/BreadcrumbBar";
import { useTopBarNavigation } from "@/components/layout/TopBarNavigationContext";
import { subscribeToAssetLibraryChanges } from "@/lib/assetLibrarySync";
import ResizableSidePanel, {
    EPISODE_EDITOR_PANEL_STORAGE_KEYS,
} from "@/components/layout/ResizableSidePanel";
import { useAuth } from "@/components/auth/AuthProvider";

const CreativeCanvas = dynamic(() => import("@/components/canvas/CreativeCanvas"), { ssr: false });
// Only the active workflow and opened dialogs are fetched. These modules carry
// editors, media controls, and provider-specific UI that the initial episode
// shell does not need.
const ScriptProcessor = dynamic(() => import("@/components/modules/ScriptProcessor"), { ssr: false });
const Cast = dynamic(() => import("@/components/modules/Cast"), { ssr: false });
const VideoGenerator = dynamic(() => import("@/components/modules/VideoGenerator"), { ssr: false });
const VideoAssembly = dynamic(() => import("@/components/modules/VideoAssembly"), { ssr: false });
const ConsistencyVault = dynamic(() => import("@/components/modules/ConsistencyVault"), { ssr: false });
const ArtDirection = dynamic(() => import("@/components/modules/ArtDirection"), { ssr: false });
const StoryboardComposer = dynamic(() => import("@/components/modules/StoryboardComposer"), { ssr: false });
const StoryboardR2V = dynamic(() => import("@/components/modules/StoryboardR2V"), { ssr: false });
const EntityConfirmModal = dynamic(() => import("@/components/modules/EntityConfirmModal"), { ssr: false });

// Audio mixing and export are handled by the Assembly step.
const LEGACY_STEPS = [
    { id: "script", labelKey: "script", icon: BookOpen },
    { id: "art_direction", labelKey: "artDirection", icon: Palette },
    { id: "assets", labelKey: "assets", icon: Users },
    { id: "storyboard", labelKey: "storyboard", icon: Layout },
    { id: "motion", labelKey: "motion", icon: Video },
    { id: "assembly", labelKey: "assembly", icon: Film },
];

// Unified workflow: five steps including the episode Cast view.
// The legacy backend enum remains accepted for persisted projects.
const UNIFIED_STEPS = [
    { id: "script", labelKey: "script", icon: BookOpen },
    { id: "art_direction", labelKey: "artDirection", icon: Palette },
    { id: "cast", labelKey: "cast", icon: Users },
    { id: "storyboard_r2v", labelKey: "storyboard", icon: Clapperboard },
    { id: "assembly", labelKey: "assembly", icon: Film },
];

export default function ProjectClient({ id, breadcrumbSegments }: { id: string; breadcrumbSegments?: BreadcrumbSegment[] }) {
    const [activeStep, setActiveStep] = useState("script");
    const t = useTranslations("project");
    const tp = useTranslations("pipeline");
    const { serverMode } = useAuth();
    const { registerNavigation } = useTopBarNavigation();

    const selectProject = useProjectStore((state) => state.selectProject);
    const currentProject = useProjectStore((state) => state.currentProject);

    // R2V v2 Phase 6 — content_mode lives on the parent series; fetch on
    // mount when project has series_id, default to "scripted" otherwise.
    const [loadedSeriesContentMode, setLoadedSeriesContentMode] = useState<{
        seriesId: string;
        mode: "scripted" | "freeform";
    } | null>(null);
    const activeSeriesId = currentProject?.series_id;
    const seriesContentMode = activeSeriesId && loadedSeriesContentMode?.seriesId === activeSeriesId
        ? loadedSeriesContentMode.mode
        : "scripted";
    useEffect(() => {
        const sid = currentProject?.series_id;
        if (!sid) return;
        let cancelled = false;
        import("@/lib/api").then(({ api }) => api.getSeries(sid))
            .then((s: any) => {
                if (!cancelled) {
                    setLoadedSeriesContentMode({
                        seriesId: sid,
                        mode: s?.content_mode === "freeform" ? "freeform" : "scripted",
                    });
                }
            })
            .catch(() => {
                if (!cancelled) setLoadedSeriesContentMode({ seriesId: sid, mode: "scripted" });
            });
        return () => { cancelled = true; };
    }, [currentProject?.series_id]);

    const steps = useMemo(() => {
        // PR-3f routing: backend enum "r2v" → unified workbench (5 steps).
        // Anything else (i2v_legacy, missing) → legacy 9-step path. Old
        // projects without workflow_mode default to legacy for backward
        // compat (spec §3.2).
        let base;
        if (currentProject?.workflow_mode !== "r2v") {
            base = LEGACY_STEPS;
        } else if (seriesContentMode === "freeform") {
            // Phase 6 — freeform mode: skip Script step, episodes start at
            // Style. Re-number labels accordingly.
            base = UNIFIED_STEPS
                .filter(s => s.id !== "script");
        } else {
            // Scripted unified flow: Cast is always present (per-episode view
            // of frame-referenced assets). Series-level shared assets are
            // managed in SeriesDetailPage.
            base = UNIFIED_STEPS;
        }

        // Per-step stage status (conservative signals from project state —
        // NOT wizard done-checks; see storyboard-r2v-unified mock). Script
        // has no field on the episode, so it stays status-less (honest —
        // don't fabricate a "done" we can't verify). Assembly is soft-gated
        // (lock + label) when there are no shots yet, but stays CLICKABLE
        // (no navigation behavior change).
        const frames = currentProject?.frames ?? [];
        const chars = currentProject?.characters ?? [];
        const frameCount = frames.length;
        const hasArt = !!currentProject?.art_direction;
        const hasMerged = !!currentProject?.merged_video_url;
        const statusFor = (id: string): { status?: "ready" | "warn" | "idle" | "gated"; statusLabel?: string } => {
            switch (id) {
                case "art_direction":
                    return hasArt ? { status: "ready", statusLabel: tp("railArtReady") } : { status: "idle" };
                case "cast":
                    return chars.length > 0
                        ? { status: "ready", statusLabel: tp("railCast", { n: chars.length }) }
                        : { status: "idle" };
                case "storyboard_r2v":
                case "storyboard":
                    return frameCount > 0 ? { status: "ready", statusLabel: tp("railShots", { n: frameCount }) } : { status: "idle" };
                case "assembly":
                    return hasMerged
                        ? { status: "ready", statusLabel: tp("railAssembled") }
                        : (frameCount > 0 ? { status: "warn", statusLabel: tp("railAssemblyReady") } : { status: "gated", statusLabel: tp("railAssemblyGated") });
                default:
                    return {};
            }
        };
        return base.map((s, index) => ({
            ...s,
            label: `${index + 1}. ${tp(s.labelKey)}`,
            ...statusFor(s.id),
        }));
    }, [currentProject, seriesContentMode, tp]);

    const handleBackToHome = () => {
        window.location.hash = '';
    };

    // Cross-module step navigation event (used by intra-module
    // affordances like Storyboard's "画风" pill that wants to jump
    // to Art Direction without prop-drilling setActiveStep into
    // every leaf component).
    useEffect(() => {
        const handler = (e: Event) => {
            const detail = (e as CustomEvent<string>).detail;
            if (typeof detail !== "string") return;
            if (steps.some((s) => s.id === detail)) {
                setActiveStep(detail);
            }
        };
        document.addEventListener("enmotion:navigateStep", handler);
        return () => document.removeEventListener("enmotion:navigateStep", handler);
    }, [steps]);

    useEffect(() => {
        selectProject(id);
    }, [id, selectProject]);

    // The Home library owns global assets, but the episode editor renders the
    // backend's resolved Episode > Series > Global project response. Refresh
    // that canonical response when any relevant owner changes so newly added
    // images and metadata appear without reloading the browser page.
    useEffect(
        () => subscribeToAssetLibraryChanges((detail) => {
            const isUnscopedChange = !detail.source && !detail.projectId && !detail.seriesId;
            const affectsCurrentProject = detail.projectId === id;
            const affectsCurrentSeries = !!activeSeriesId && detail.seriesId === activeSeriesId;
            if (
                detail.source === "global"
                || isUnscopedChange
                || affectsCurrentProject
                || affectsCurrentSeries
            ) {
                void selectProject(id);
            }
        }),
        [activeSeriesId, id, selectProject],
    );

    const segments = useMemo(
        () => breadcrumbSegments || [{ label: "EnMotion", hash: "#/" }, { label: currentProject?.title || "" }],
        [breadcrumbSegments, currentProject?.title],
    );
    const currentProjectId = currentProject?.id;

    useEffect(() => {
        if (!serverMode || !currentProjectId) return;
        return registerNavigation({ segments });
    }, [currentProjectId, registerNavigation, segments, serverMode]);

    if (!currentProject) {
        return (
            <div className="flex h-[100dvh] items-center justify-center bg-background">
                <div className="text-center">
                    <p className="text-text-secondary mb-4">{t("notFound")}</p>
                    <button
                        onClick={handleBackToHome}
                        className="text-primary hover:underline"
                    >
                        {t("backToList")}
                    </button>
                </div>
            </div>
        );
    }

    return (
        <main className="relative flex h-[100dvh] w-full overflow-hidden bg-background">
            {/* Background Canvas */}
            <div className="absolute inset-0 z-0 pointer-events-auto">
                <CreativeCanvas />
            </div>

            {/* Left Sidebar — desktop keeps its breadcrumb fallback; server mode
                renders the path in the persistent account bar above the editor. */}
            <ResizableSidePanel
                side="left"
                storageKey={EPISODE_EDITOR_PANEL_STORAGE_KEYS.left}
                defaultWidth={256}
                minWidth={220}
                maxWidth={420}
                minRemainingWidth={640}
                className="z-20"
            >
                <PipelineSidebar
                    activeStep={activeStep}
                    onStepChange={setActiveStep}
                    steps={steps}
                    breadcrumbSegments={serverMode ? undefined : segments}
                    topSlot={
                        currentProject?.series_id ? (
                            <EpisodeMiniList
                                seriesId={currentProject.series_id}
                                currentProjectId={id}
                            />
                        ) : null
                    }
                />
            </ResizableSidePanel>

            {/* Main Content Area — no z-index to avoid trapping fixed modals in a stacking context */}
            <div className="flex-1 flex overflow-hidden relative">
                <div className="flex-1 overflow-hidden relative">
                    {/* Global Atelier atmosphere — shared across every step so the
                        pipeline reads as one surface (bloom + grain, pointer-events
                        none, content sits above on z-10). */}
                    <div className="atelier-page-bloom" aria-hidden="true" />
                    <div className="atelier-page-grain" aria-hidden="true" />
                    <div className="relative z-10 h-full flex flex-col overflow-hidden">
                        {activeStep === "script" && <ScriptProcessor />}
                        {activeStep === "art_direction" && <ArtDirection />}
                        {activeStep === "cast" && <Cast />}
                        {activeStep === "assets" && <ConsistencyVault />}  {/* legacy i2v only */}
                        {activeStep === "storyboard" && <StoryboardComposer />}
                        {activeStep === "storyboard_r2v" && <StoryboardR2V />}
                        {activeStep === "motion" && <VideoGenerator />}
                        {activeStep === "assembly" && <VideoAssembly />}
                    </div>
                </div>
            </div>

            <EntityExtractionConfirm />
        </main>
    );
}

function EntityExtractionConfirm() {
    const ts = useTranslations("script");
    const pendingExtraction = useProjectStore((s) => s.pendingExtraction);
    const currentProject = useProjectStore((s) => s.currentProject);
    const confirmExtraction = useProjectStore((s) => s.confirmExtraction);
    const discardExtraction = useProjectStore((s) => s.discardExtraction);
    const isApplying = useProjectStore((s) => s.isAnalyzing);

    const handleConfirm = async () => {
        try {
            await confirmExtraction();
        } catch {
            const { toast } = await import("@/store/toastStore");
            toast.error(ts("analysisFailedShort"));
        }
    };

    const handleDiscard = () => {
        discardExtraction();
        import("@/store/toastStore").then(({ toast }) => toast.info(ts("extractionDiscarded")));
    };

    return (
        pendingExtraction ? (
            <EntityConfirmModal
                isOpen
                preview={pendingExtraction}
                currentCounts={{
                    characters: currentProject?.characters?.length ?? 0,
                    scenes: currentProject?.scenes?.length ?? 0,
                    props: currentProject?.props?.length ?? 0,
                }}
                onConfirm={handleConfirm}
                onDiscard={handleDiscard}
                applying={isApplying}
            />
        ) : null
    );
}
