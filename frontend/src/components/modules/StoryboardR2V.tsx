"use client";

import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { motion } from "framer-motion";
import { Plus, Loader2, Sparkles, PanelBottomOpen, PanelBottomClose } from "lucide-react";
import StepPageHeader from "@/components/shared/StepPageHeader";
import { useLocale, useTranslations } from "next-intl";
import { useProjectStore } from "@/store/projectStore";
import { api, crudApi, type VideoTask, type RefineSSEEvent } from "@/lib/api";
import { extractErrorDetail, getAssetUrl } from "@/lib/utils";
import { debugLog } from "@/lib/debugLog";
import { readWorkspaceItem, removeWorkspaceItem, writeWorkspaceItem } from "@/lib/workspaceStorage";
import type { BatchSummary } from "./storyboard-r2v/shot-panel/CandidatesSection";
import { VIDEO_I2V_MODELS, DEFAULT_I2V_MODEL_ID } from "@/lib/modelCatalog";
import ShotCard, { type ShotNode } from "./storyboard-r2v/ShotCard";
import { buildAssembledPrompt } from "./storyboard-r2v/buildAssembledPrompt";
import StoryboardGenerateDialog from "./storyboard-r2v/StoryboardGenerateDialog";
import { toast } from "@/store/toastStore";
import { Wand2 } from "lucide-react";
import AssetDrawer from "./storyboard-r2v/AssetDrawer";
import { type VideoConfig, DEFAULT_VIDEO_CONFIG } from "./storyboard-r2v/VideoConfigModal";
import {
    migrateShotNode,
    appendT2IImage,
    setActiveT2IIndex,
    removeT2IImage,
    getActiveT2IImageUrl,
    frameToShotNode,
} from "./storyboard-r2v/shotNodeHelpers";
import { overridePanelSectionState } from "./storyboard-r2v/shot-panel/usePanelSectionState";
import ParamsSection, { type ParamsState } from "./storyboard-r2v/shot-panel/ParamsSection";
import T2ISubsection from "./storyboard-r2v/shot-panel/T2ISubsection";
import CandidatesSection from "./storyboard-r2v/shot-panel/CandidatesSection";
import CompareModal from "./storyboard-r2v/shot-panel/CompareModal";
import TaskQueueButton from "./storyboard-r2v/shot-panel/TaskQueueButton";
import TaskQueuePanel from "./storyboard-r2v/shot-panel/TaskQueuePanel";
import { GenerationBanner, type BannerState } from "./storyboard-r2v/GenerationBanner";
import { reconcileT2IWorkbenchAfterDelete } from "./storyboard-r2v/t2iDeleteReconcile";
import { useModelDisplayName } from "@/lib/useModelDisplayName";
import { clipFrameType, clipImageId } from "@/lib/clipStartFrame";
import type { AssetRef } from "@/components/assets/assetEditorTypes";

export function resolveStoryboardVideoModel(
    projectModel?: string | null,
    cachedModel?: string | null,
): string {
    const candidate = projectModel || cachedModel || DEFAULT_I2V_MODEL_ID;
    return VIDEO_I2V_MODELS.some((model) => model.id === candidate)
        ? candidate
        : DEFAULT_I2V_MODEL_ID;
}

export default function StoryboardR2V() {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const selectedFrameId = useProjectStore((state) => state.selectedFrameId);
    const setSelectedFrameId = useProjectStore((state) => state.setSelectedFrameId);
    const removeRenderingFrame = useProjectStore((state) => state.removeRenderingFrame);
    const t = useTranslations("storyboardR2V");
    const tStep = useTranslations("stepHeader");
    const modelDisplayName = useModelDisplayName();
    const locale = useLocale();

    // Derive shots from project frames. Workbench state (T2I 抽卡
    // history, last-active tab, batch count) now comes from backend-
    // persisted frame fields (added in commit 9149b06) instead of
    // React-only state, so cross-refresh / cross-device users see the
    // same panel state. migrateShotNode still runs as a defensive
    // belt-and-suspenders for very old localStorage drafts.
    const [shots, setShots] = useState<ShotNode[]>(() => {
        if (currentProject?.frames && currentProject.frames.length > 0) {
            const videoTasks: any[] = (currentProject as any).video_tasks ?? [];
            return currentProject.frames.map((frame: any) => ({
                ...frameToShotNode(frame, videoTasks, "t2i_i2v"),
                tabMode: "t2i_i2v" as const,
            }));
        }
        return [migrateShotNode({ id: `shot_${Date.now()}`, prompt: "", tabMode: "t2i_i2v" })];
    });

    // Global video config (with localStorage persistence for model selection)
    const [videoConfig, setVideoConfig] = useState<VideoConfig>(() => {
        const savedI2v = readWorkspaceItem('storyboard-newapi-video-model')
            ?? readWorkspaceItem('storyboard-r2v-model')
            ?? null;
        const projectI2v = currentProject?.model_settings?.video_model
            || currentProject?.model_settings?.i2v_model
            || null;

        // A cached model may have been removed from the approved list.
        // Normalize stale values to the explicit New API default.
        const i2vModelId = resolveStoryboardVideoModel(projectI2v, savedI2v);
        const savedI2vIsValid = !savedI2v
            || VIDEO_I2V_MODELS.some((model) => model.id === savedI2v);
        if (!savedI2vIsValid) {
            removeWorkspaceItem('storyboard-r2v-model');
            removeWorkspaceItem('storyboard-newapi-video-model');
            debugLog.warn(
                "Studio",
                `Cached I2V model "${savedI2v}" is no longer in the visible I2V list; ` +
                `falling back to "${DEFAULT_I2V_MODEL_ID}".`,
            );
        }

        removeWorkspaceItem('storyboard-r2v-model');
        removeWorkspaceItem('storyboard-r2v-r2v-model');

        const finalConfig = VIDEO_I2V_MODELS.find(m => m.id === i2vModelId);
        const dc = finalConfig?.duration;
        const defaultDuration = dc ? (dc.type === 'fixed' ? dc.value : dc.default) : 5;
        return {
            ...DEFAULT_VIDEO_CONFIG,
            model: i2vModelId,
            duration: defaultDuration,
        };
    });
    const effectiveProjectVideoModel = currentProject?.model_settings?.video_model
        || currentProject?.model_settings?.i2v_model
        || null;

    // Backend project payloads contain the live project -> Series -> global
    // resolution. Refreshes or project switches must replace any device-local
    // recovery value; the cache is only a fallback when no project value exists.
    useEffect(() => {
        if (!effectiveProjectVideoModel) return;
        const model = resolveStoryboardVideoModel(effectiveProjectVideoModel);
        let cancelled = false;
        queueMicrotask(() => {
            if (cancelled) return;
            setVideoConfig((current) => {
                if (current.model === model) return current;
                const config = VIDEO_I2V_MODELS.find((item) => item.id === model);
                const duration = config?.duration;
                return {
                    ...current,
                    model,
                    duration: duration
                        ? (duration.type === "fixed" ? duration.value : duration.default)
                        : current.duration,
                };
            });
        });
        return () => {
            cancelled = true;
        };
    }, [effectiveProjectVideoModel]);

    // Modal & drawer state (configModalOpen retired with the gear; the
    // old VideoConfigModal mount is gone, replaced by per-shot
    // ParamsSection panels under each ShotCard. handleConfigChange is
    // also gone — model writes now flow through handleShotParamsChange
    // below, which mirrors them to localStorage.)
    const [drawerState, setDrawerState] = useState<{ isOpen: boolean; targetShotIndex: number | null }>({
        isOpen: false,
        targetShotIndex: null,
    });

    // Task-queue side panel state. Persisted across renders only; we
    // intentionally don't localStorage this — it's transient "I want
    // to peek at queue" UI affordance, not a saved layout preference.
    const [queueOpen, setQueueOpen] = useState(false);

    // Compare-mode selection: a Set of task ids the user shift-clicked
    // in any shot's candidate panel. Multi-shot compare is a future
    // feature; for now the same Set is shared across shots so user
    // can only effectively compare within one shot at a time. Cleared
    // on Compare modal close.
    const [compareSelectedIds, setCompareSelectedIds] = useState<Set<string>>(() => new Set());
    const [compareModalOpen, setCompareModalOpen] = useState(false);

    // Refs map for textareas (for asset insertion from drawer)
    const textareaRefs = useRef<Map<number, HTMLTextAreaElement>>(new Map());
    // Refs to each shot's outer wrapper so the task-queue panel can
    // jump-scroll the canvas to a specific frame.
    const shotWrapperRefs = useRef<Map<string, HTMLDivElement | null>>(new Map());
    // Per-shot submission lockout (Issue 17) — debounce double-clicks and
    // strict-mode double-effects. Holds shot.id strings; entries auto-expire
    // after 500ms via setTimeout in generateVideoBatch.
    const submittingShotsRef = useRef<Set<string>>(new Set());
    const deletingShotsRef = useRef<Set<string>>(new Set());
    const [deletingShotIds, setDeletingShotIds] = useState<Set<string>>(() => new Set());
    const deletingT2IImagesRef = useRef<Set<string>>(new Set());
    const workbenchDuringT2IDeleteRef = useRef<Map<
        string,
        Parameters<typeof api.updateFrameWorkbench>[2]
    >>(new Map());
    const workbenchInFlightRef = useRef<Map<string, Set<Promise<void>>>>(new Map());
    const fieldPendingRef = useRef<Map<string, { timer: number; fields: Record<string, any> }>>(new Map());
    const t2iRenderControllersRef = useRef<Map<string, AbortController>>(new Map());

    useEffect(() => {
        const controllers = t2iRenderControllersRef.current;
        return () => {
            for (const controller of controllers.values()) {
                controller.abort();
            }
            controllers.clear();
        };
    }, [currentProject?.id]);

    // Inline per-shot validation errors are shown before submission.
    const [shotErrors, setShotErrors] = useState<Record<string, string>>({});

    // Per-shot seed override. The Seed advanced param doesn't live in
    // videoConfig (seeds are inherently per-generation; sharing one
    // across shots would defeat the "different shots = different
    // creative takes" expectation). Without this state the seed
    // input + dice button would appear to do nothing because
    // ParamsSection.set("seed", N) flowed up to handleShotParamsChange,
    // which silently dropped it, so the next paramsStateForShot()
    // call would always rebuild params.seed = undefined.
    //
    // `undefined` means "no explicit seed" (provider picks). Any
    // number means "use this exact seed" — same for all takes in a
    // batch (intentional: ×N with a fixed seed = N runs at that seed
    // for ablation testing). Users who want N varied takes leave it
    // empty.
    const [shotSeeds, setShotSeeds] = useState<Record<string, number | undefined>>({});

    // Per-shot batch count (the "抽卡 ×N" knob). Decoupled from
    // videoConfig because users typically pick the model + duration
    // once and vary count per shot. Keyed by shot.id so insert/move
    // don't shuffle counts onto the wrong shot. Seeded from backend
    // workbench_generate_count so user choices survive refresh.
    const [shotCounts, setShotCounts] = useState<Record<string, number>>(() => {
        const out: Record<string, number> = {};
        const frames: any[] = currentProject?.frames ?? [];
        for (const f of frames) {
            if (typeof f.workbench_generate_count === "number") {
                out[f.id] = f.workbench_generate_count;
            }
        }
        return out;
    });

    // Issue 16 — per-shot expand state (P plan). Default: all collapsed
    // (browse mode). Set persists per project to localStorage so coming back
    // to the project restores the user's last working layout.
    const expandStorageKey = currentProject ? `storyboard-r2v-expanded-${currentProject.id}` : null;
    const [expandedShots, setExpandedShots] = useState<Set<string>>(() => {
        if (typeof window === "undefined" || !expandStorageKey) return new Set();
        try {
            const raw = readWorkspaceItem(expandStorageKey);
            if (raw) {
                const arr = JSON.parse(raw);
                if (Array.isArray(arr)) return new Set(arr.filter(x => typeof x === "string"));
            }
        } catch { /* corrupt localStorage value — ignore */ }
        return new Set();
    });
    // Persist on change.
    useEffect(() => {
        if (typeof window === "undefined" || !expandStorageKey) return;
        try {
            writeWorkspaceItem(expandStorageKey, JSON.stringify(Array.from(expandedShots)));
        } catch { /* quota exceeded — ignore */ }
    }, [expandedShots, expandStorageKey]);

    const toggleShotExpanded = useCallback((shotId: string) => {
        setExpandedShots(prev => {
            const next = new Set(prev);
            if (next.has(shotId)) next.delete(shotId);
            else next.add(shotId);
            return next;
        });
    }, []);
    const expandAllShots = useCallback(() => {
        const ids = shots.map(s => s.id);
        // Force every inner section open — overrides each shot's sticky
        // preference. Section keys must match what ParamsSection /
        // CandidatesSection register inside their SectionShells.
        overridePanelSectionState(ids, ["params", "candidates"], true);
        setExpandedShots(new Set(ids));
    }, [shots]);
    const collapseAllShots = useCallback(() => {
        // Don't reset section preferences here — sticky memory should
        // survive a global collapse so re-expanding a shot returns to
        // the user's chosen drawer state.
        setExpandedShots(new Set());
    }, []);

    // Debounced backend writer for workbench state. Coalesces rapid
    // changes (e.g. user clicking through T2I thumbs) into one PATCH
    // per shot per second. Per-shot map ensures one shot's pending
    // write doesn't get overwritten by another's.
    const workbenchPendingRef = useRef<Map<string, {
        timer: number;
        patch: Parameters<typeof api.updateFrameWorkbench>[2];
    }>>(new Map());
    const persistWorkbench = useCallback((
        shotId: string,
        patch: Parameters<typeof api.updateFrameWorkbench>[2],
    ) => {
        if (!currentProject?.id) return;
        // Synthetic shot id (not yet materialized on backend) — skip; the
        // workbench state will be re-applied after createFrame swaps the id.
        if (shotId.startsWith("shot_")) return;
        if (deletingT2IImagesRef.current.has(shotId)) {
            const queued = workbenchDuringT2IDeleteRef.current.get(shotId) ?? {};
            workbenchDuringT2IDeleteRef.current.set(shotId, { ...queued, ...patch });
            return;
        }
        const projectId = currentProject.id;
        const map = workbenchPendingRef.current;
        const existing = map.get(shotId);
        const merged = { ...(existing?.patch ?? {}), ...patch };
        if (existing) {
            window.clearTimeout(existing.timer);
        }
        const timer = window.setTimeout(() => {
            map.delete(shotId);
            const write = api.updateFrameWorkbench(projectId, shotId, merged)
                .then(() => {
                    // Sync store so other tabs (or remount on tab switch)
                    // see the latest workbench state. Read store live to
                    // avoid stale closure.
                    const proj = useProjectStore.getState().currentProject;
                    if (!proj || proj.id !== projectId) return;
                    const nextFrames = (proj.frames ?? []).map((f: any) =>
                        f.id === shotId ? { ...f, ...merged } : f,
                    );
                    updateProject(projectId, { frames: nextFrames });
                })
                .catch((err) => {
                    debugLog.warn("Studio", "Failed to persist workbench state:", err);
                });
            const writes = workbenchInFlightRef.current.get(shotId) ?? new Set<Promise<void>>();
            writes.add(write);
            workbenchInFlightRef.current.set(shotId, writes);
            void write.then(() => {
                const activeWrites = workbenchInFlightRef.current.get(shotId);
                activeWrites?.delete(write);
                if (activeWrites?.size === 0) {
                    workbenchInFlightRef.current.delete(shotId);
                }
            });
        }, 1000);
        map.set(shotId, { timer, patch: merged });
    }, [currentProject, updateProject]);

    // Prompt edits hit a different endpoint (POST /frames/update with
    // action_description) — debounced separately from workbench so a
    // user typing fast doesn't push 6 PATCH /workbench every keystroke.
    const promptPendingRef = useRef<Map<string, { timer: number; prompt: string }>>(new Map());
    const persistPrompt = useCallback((shotId: string, prompt: string) => {
        if (!currentProject?.id) return;
        if (shotId.startsWith("shot_")) return;
        const projectId = currentProject.id;
        const map = promptPendingRef.current;
        const existing = map.get(shotId);
        if (existing) window.clearTimeout(existing.timer);
        const timer = window.setTimeout(() => {
            map.delete(shotId);
            api.updateFrame(projectId, shotId, { action_description: prompt })
                .then(() => {
                    const proj = useProjectStore.getState().currentProject;
                    if (!proj || proj.id !== projectId) return;
                    const nextFrames = (proj.frames ?? []).map((f: any) =>
                        f.id === shotId ? { ...f, action_description: prompt } : f,
                    );
                    updateProject(projectId, { frames: nextFrames });
                })
                .catch((err) => debugLog.warn("Studio", "persistPrompt failed", err));
        }, 800);
        map.set(shotId, { timer, prompt });
    }, [currentProject, updateProject]);

    // Flush all pending writes on unmount (e.g. user switches step tab)
    // so the last keystroke / param change isn't stranded in the debounce
    // window. All queues (workbench, prompt, field) drain in parallel.
    useEffect(() => {
        const wbMap = workbenchPendingRef.current;
        const pMap = promptPendingRef.current;
        const fMap = fieldPendingRef.current;
        return () => {
            const projectId = currentProject?.id;
            if (!projectId) return;
            for (const [shotId, entry] of Array.from(wbMap.entries())) {
                window.clearTimeout(entry.timer);
                api.updateFrameWorkbench(projectId, shotId, entry.patch).catch(() => {});
            }
            wbMap.clear();
            for (const [shotId, entry] of Array.from(pMap.entries())) {
                window.clearTimeout(entry.timer);
                api.updateFrame(projectId, shotId, { action_description: entry.prompt }).catch(() => {});
            }
            pMap.clear();
            for (const [shotId, entry] of Array.from(fMap.entries())) {
                window.clearTimeout(entry.timer);
                api.updateFrame(projectId, shotId, entry.fields).catch(() => {});
            }
            fMap.clear();
        };
    }, [currentProject?.id]);

    // beforeunload guard: warn when structured field edits are pending
    useEffect(() => {
        const handler = (e: BeforeUnloadEvent) => {
            if (fieldPendingRef.current.size > 0 || promptPendingRef.current.size > 0) {
                e.preventDefault();
            }
        };
        window.addEventListener("beforeunload", handler);
        return () => window.removeEventListener("beforeunload", handler);
    }, []);

    const characters = currentProject?.characters || [];
    const scenes = currentProject?.scenes || [];
    const props = currentProject?.props || [];

    // ────────────────────────────────────────────────────────────────────
    // Shot mutations — Optimistic UI + 异步同步后端 + store 更新
    //   Pattern: 立即改本地 state（无闪烁），后台 fire-and-forget call
    //   到 backend，成功后 swap synthetic id with real id（addShot/duplicate）
    //   并 updateProject(store) 让 currentProject.frames 保持权威。
    //   失败仅 log warn，不回滚（避免 UI 闪烁；用户可重试）。
    //   切 step tab → unmount 时 useEffect cleanup 已经 flush pending
    //   debounce writes，所以打字到一半切走也不丢字。
    // ────────────────────────────────────────────────────────────────────

    // Add a new shot after the given index
    const addShot = useCallback(async (afterIndex: number) => {
        const synthId = `shot_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        const newShot: ShotNode = {
            id: synthId,
            prompt: "",
            tabMode: "t2i_i2v",
        };
        setShots(prev => {
            const updated = [...prev];
            updated.splice(afterIndex + 1, 0, newShot);
            return updated;
        });
        // Issue 16 — newly-created shots default to expanded so the user
        // can immediately operate on them. Existing shots keep their state.
        setExpandedShots(prev => {
            const next = new Set(prev);
            next.add(synthId);
            return next;
        });
        if (!currentProject?.id) return;
        try {
            const resp = await crudApi.createFrame(currentProject.id, {
                scene_id: "",
                action_description: "",
                insert_at: afterIndex + 1,
            });
            const frames = Array.isArray(resp?.frames) ? resp.frames : null;
            const realFrame = frames?.[Math.min(afterIndex + 1, frames.length - 1)];
            if (realFrame?.id) {
                setShots(prev => prev.map(s => s.id === synthId ? { ...s, id: realFrame.id } : s));
                setExpandedShots(prev => {
                    if (!prev.has(synthId)) return prev;
                    const next = new Set(prev);
                    next.delete(synthId);
                    next.add(realFrame.id);
                    return next;
                });
            }
            if (frames) updateProject(currentProject.id, { frames });
        } catch (err) {
            debugLog.warn("Studio", "addShot backend persist failed", err);
        }
    }, [currentProject, updateProject]);

    // PR-3 followup · LLM storyboard generation. State + handler live at
    // the StoryboardR2V level (not in a sub-component) because the toast
    // lifecycle survives the dialog closing and we need the parent to
    // setShots() when the new frames come back.
    const [genDialogOpen, setGenDialogOpen] = useState(false);
    const [generating, setGenerating] = useState(false);
    const [bannerState, setBannerState] = useState<BannerState>(
        () => (currentProject?.frames?.length ?? 0) > 0 ? "summary" : "idle"
    );
    const [refineProgress, setRefineProgress] = useState<{ current: number; total: number } | null>(null);

    const PHASE1_CAPTIONS = useMemo(() => [
        "正在分析剧本结构…",
        "识别场景切换点…",
        "拆分镜头与动作…",
        "琢磨每帧的构图和节奏…",
        "快了，安排景别和运镜…",
        "最后润色一下…",
    ], []);

    const bannerSummary = useMemo(() => {
        if (!currentProject?.frames?.length) return null;
        const frames = currentProject.frames as any[];
        const frameCount = frames.length;
        return { frameCount };
    }, [currentProject]);

    const handleSmartGenerate = useCallback(async () => {
        if (!currentProject?.id) return;
        const projectId = currentProject.id;
        const scriptText = (currentProject as any).originalText || (currentProject as any).original_text || "";
        if (!scriptText.trim()) {
            toast.warning(t("genToastNoScript"));
            return;
        }
        setGenerating(true);
        setBannerState("phase1");
        setShots([]);
        try {
            // Phase 1: generate coarse frames
            const updated = await api.analyzeToStoryboard(projectId, scriptText);
            const newFrameCount = Array.isArray(updated?.frames) ? updated.frames.length : 0;
            updateProject(projectId, updated);
            if (Array.isArray(updated?.frames)) {
                const videoTasks: any[] = (updated as any).video_tasks ?? [];
                setShots(updated.frames.map((frame: any) => ({ ...frameToShotNode(frame, videoTasks, "t2i_i2v"), tabMode: "t2i_i2v" as const })));
            }

            // Phase 2: batch refine (SSE)
            if (newFrameCount > 0) {
                setBannerState("phase2");
                setRefineProgress({ current: 0, total: newFrameCount });
                await api.refineBatchFrames(projectId, (event: RefineSSEEvent) => {
                    if (event.type === "frame_refine_start") {
                        setRefineProgress({ current: (event.frame_index ?? 0) + 1, total: event.total ?? newFrameCount });
                    }
                });
                const refreshed = await api.getProject(projectId);
                if (refreshed?.frames) {
                    updateProject(projectId, { frames: refreshed.frames });
                    const videoTasks: any[] = (refreshed as any).video_tasks ?? [];
                    setShots(refreshed.frames.map((frame: any) => ({ ...frameToShotNode(frame, videoTasks, "t2i_i2v"), tabMode: "t2i_i2v" as const })));
                }
            }
            setBannerState("summary");
            toast.success(t("genToastDone", { count: newFrameCount }));
        } catch (err: any) {
            const detail = err?.response?.data?.detail || err?.message || t("genToastErrUnknown");
            toast.error(locale === "zh" ? t("genToastErr") : `${t("genToastErr")}: ${String(detail).slice(0, 200)}`);
        } finally {
            setGenerating(false);
            setRefineProgress(null);
            // Determine final banner state based on actual current shots
            setShots(currentShots => {
                setBannerState(currentShots.length > 0 ? "summary" : "idle");
                return currentShots;
            });
        }
    }, [currentProject, locale, updateProject, t]);

    const handleRefineFrame = useCallback(async (frameId: string) => {
        if (!currentProject?.id) return;
        try {
            await api.refineSingleFrame(currentProject.id, frameId);
            const updated = await api.getProject(currentProject.id);
            if (updated?.frames) {
                updateProject(currentProject.id, { frames: updated.frames });
                const videoTasks: any[] = (updated as any).video_tasks ?? [];
                setShots(updated.frames.map((frame: any) => ({ ...frameToShotNode(frame, videoTasks, "t2i_i2v"), tabMode: "t2i_i2v" as const })));
            }
            toast.success(t("refineDoneToast"));
        } catch (err) {
            toast.error(t("refineFailedToast"));
            debugLog.warn("Studio", "single frame refine failed", err);
        }
    }, [currentProject, updateProject]);

    // Delete a shot
    const deleteShot = useCallback(async (index: number) => {
        const target = shots[index];
        if (!target) return;
        if (deletingShotsRef.current.has(target.id)) return;
        if (!confirm(t("confirmDeleteShot"))) return;

        const removeConfirmedShot = (remainingShots: ShotNode[]) => {
            setShots(remainingShots);
            setExpandedShots(prev => {
                if (!prev.has(target.id)) return prev;
                const next = new Set(prev);
                next.delete(target.id);
                return next;
            });
            const discardTimedWrite = (map: Map<string, { timer: number }>) => {
                const pending = map.get(target.id);
                if (pending) window.clearTimeout(pending.timer);
                map.delete(target.id);
            };
            discardTimedWrite(workbenchPendingRef.current);
            discardTimedWrite(promptPendingRef.current);
            discardTimedWrite(fieldPendingRef.current);
            workbenchDuringT2IDeleteRef.current.delete(target.id);
            workbenchInFlightRef.current.delete(target.id);
            shotWrapperRefs.current.delete(target.id);
            removeRenderingFrame(target.id);
            setShotErrors((current) => {
                const next = { ...current };
                delete next[target.id];
                return next;
            });
            setShotSeeds((current) => {
                const next = { ...current };
                delete next[target.id];
                return next;
            });
            setShotCounts((current) => {
                const next = { ...current };
                delete next[target.id];
                return next;
            });

            const taskIds = new Set([
                target.t2iTaskId,
                target.videoTaskId,
                ...(target.videoTaskIdsByTab?.t2i_i2v ?? []),
            ].filter((value): value is string => Boolean(value)));
            if (taskIds.size > 0) {
                setCompareSelectedIds((current) => new Set(
                    Array.from(current).filter((id) => !taskIds.has(id)),
                ));
            }
            if (selectedFrameId === target.id) {
                const nearest = remainingShots.length > 0
                    ? remainingShots[Math.min(index, remainingShots.length - 1)]
                    : null;
                setSelectedFrameId(nearest?.id ?? null);
            }
        };

        deletingShotsRef.current.add(target.id);
        setDeletingShotIds((current) => new Set(current).add(target.id));
        // Synthetic ids never reached the backend, so there is no remote
        // record or file to confirm before removing them locally.
        if (!currentProject?.id || target.id.startsWith("shot_")) {
            removeConfirmedShot(shots.filter((shot) => shot.id !== target.id));
            deletingShotsRef.current.delete(target.id);
            setDeletingShotIds((current) => {
                const next = new Set(current);
                next.delete(target.id);
                return next;
            });
            return;
        }
        try {
            const resp = await crudApi.deleteFrame(currentProject.id, target.id);
            updateProject(currentProject.id, resp);
            const videoTasks: any[] = (resp as any).video_tasks ?? [];
            const remainingShots = (resp.frames ?? []).map((frame: any) => ({
                ...frameToShotNode(frame, videoTasks, "t2i_i2v"),
                tabMode: "t2i_i2v" as const,
            }));
            removeConfirmedShot(remainingShots);
        } catch (err) {
            debugLog.warn("Studio", "deleteShot failed", err);
            const detail = extractErrorDetail(err, t("deleteShotFailed"));
            toast.error(locale === "zh" ? t("deleteShotFailed") : t("deleteShotFailedDetail", { detail }));
        } finally {
            deletingShotsRef.current.delete(target.id);
            setDeletingShotIds((current) => {
                if (!current.has(target.id)) return current;
                const next = new Set(current);
                next.delete(target.id);
                return next;
            });
        }
    }, [
        shots,
        currentProject,
        updateProject,
        selectedFrameId,
        setSelectedFrameId,
        removeRenderingFrame,
        locale,
        t,
    ]);

    const deleteT2IImage = useCallback(async (shotIndex: number, imageIndex: number) => {
        const target = shots[shotIndex];
        if (!target) return;

        if (!currentProject?.id || target.id.startsWith("shot_")) {
            setShots(prev => prev.map((shot) =>
                shot.id === target.id ? removeT2IImage(shot, imageIndex) : shot,
            ));
            return;
        }
        if (deletingT2IImagesRef.current.has(target.id)) return;
        deletingT2IImagesRef.current.add(target.id);
        const pendingWorkbench = workbenchPendingRef.current.get(target.id);
        let pendingWorkbenchFlushed = false;
        let retryPatch: Parameters<typeof api.updateFrameWorkbench>[2] | null = null;
        const removedUrl = target.t2iImageUrls?.[imageIndex];

        // Freeze the debounce queue synchronously. Otherwise its timer could
        // fire while we wait for an older request and create another stale
        // PATCH behind the DELETE.
        if (pendingWorkbench) {
            window.clearTimeout(pendingWorkbench.timer);
            workbenchPendingRef.current.delete(target.id);
        }

        const applyFrame = (frame: {
            id: string;
            t2i_image_urls?: string[];
            t2i_selected_index?: number;
        }) => {
            const urls = Array.isArray(frame?.t2i_image_urls) ? frame.t2i_image_urls : [];
            const selectedIndex = typeof frame?.t2i_selected_index === "number"
                ? Math.max(0, Math.min(frame.t2i_selected_index, Math.max(0, urls.length - 1)))
                : 0;
            setShots(prev => prev.map((shot) => shot.id === target.id
                ? {
                    ...shot,
                    t2iImageUrls: urls,
                    t2iSelectedIndex: selectedIndex,
                    t2iImageUrl: urls[selectedIndex],
                }
                : shot));

            const latestProject = useProjectStore.getState().currentProject;
            if (latestProject?.id === currentProject.id) {
                updateProject(currentProject.id, {
                    frames: (latestProject.frames ?? []).map((existingFrame) =>
                        existingFrame.id === target.id ? frame : existingFrame,
                    ),
                });
            }
        };
        try {
            // A debounce timer may already have dispatched one or more PATCHes.
            // Wait for every request for this frame before DELETE; merely
            // clearing the pending timer cannot stop an HTTP request in flight.
            const inFlightWrites = Array.from(
                workbenchInFlightRef.current.get(target.id) ?? [],
            );
            if (inFlightWrites.length > 0) {
                await Promise.all(inFlightWrites);
            }

            // Flush the frozen pending patch before DELETE so it cannot
            // resurrect a removed URL after the server reclaims that file.
            if (pendingWorkbench) {
                await api.updateFrameWorkbench(
                    currentProject.id,
                    target.id,
                    pendingWorkbench.patch,
                );
                pendingWorkbenchFlushed = true;
            }
            let frame = await api.deleteFrameT2IImage(
                currentProject.id,
                target.id,
                imageIndex,
            );
            applyFrame(frame);

            // Workbench writes created while DELETE was in flight are queued
            // above. Replay them against the authoritative post-delete list,
            // stripping the removed URL, and keep draining until no write can
            // race behind this deletion.
            while (true) {
                const queued = workbenchDuringT2IDeleteRef.current.get(target.id);
                if (!queued) break;
                workbenchDuringT2IDeleteRef.current.delete(target.id);

                const reconciled = reconcileT2IWorkbenchAfterDelete(
                    frame,
                    queued,
                    removedUrl,
                    target.t2iImageUrls ?? [],
                );
                try {
                    frame = await api.updateFrameWorkbench(
                        currentProject.id,
                        target.id,
                        reconciled,
                    );
                    applyFrame(frame);
                } catch (syncError) {
                    // Edits may have arrived while this replay request was in
                    // flight. Merge them into the retry instead of dropping
                    // them when the delete lock is released below.
                    const newerQueued = workbenchDuringT2IDeleteRef.current.get(target.id);
                    workbenchDuringT2IDeleteRef.current.delete(target.id);
                    retryPatch = reconcileT2IWorkbenchAfterDelete(
                        frame,
                        { ...reconciled, ...(newerQueued ?? {}) },
                        removedUrl,
                        target.t2iImageUrls ?? [],
                    );
                    debugLog.warn("Studio", "post-delete workbench sync failed", syncError);
                    toast.error(t("workbenchSyncFailed"));
                    break;
                }
            }
        } catch (err) {
            retryPatch = {
                ...(pendingWorkbench && !pendingWorkbenchFlushed
                    ? pendingWorkbench.patch
                    : {}),
                ...(workbenchDuringT2IDeleteRef.current.get(target.id) ?? {}),
            };
            debugLog.warn("Studio", "deleteT2IImage failed", err);
            toast.error(t("deleteImageFailed"));
        } finally {
            workbenchDuringT2IDeleteRef.current.delete(target.id);
            deletingT2IImagesRef.current.delete(target.id);
        }
        if (retryPatch && Object.keys(retryPatch).length > 0) {
            persistWorkbench(target.id, retryPatch);
        }
    }, [shots, currentProject, updateProject, persistWorkbench, t]);

    // Move shot up/down
    const moveShot = useCallback(async (index: number, direction: "up" | "down") => {
        const targetIndex = direction === "up" ? index - 1 : index + 1;
        if (targetIndex < 0 || targetIndex >= shots.length) return;
        const updated = [...shots];
        [updated[index], updated[targetIndex]] = [updated[targetIndex], updated[index]];
        setShots(updated);
        if (!currentProject?.id) return;
        const ids = updated.map(s => s.id);
        // Reorder requires every id to be backed on backend — if any
        // are still synthetic (createFrame in-flight), defer; the next
        // move after createFrame settles will reconcile.
        if (ids.some(id => id.startsWith("shot_"))) return;
        try {
            const resp = await crudApi.reorderFrames(currentProject.id, ids);
            const frames = Array.isArray(resp?.frames) ? resp.frames : null;
            if (frames) updateProject(currentProject.id, { frames });
        } catch (err) {
            debugLog.warn("Studio", "moveShot backend persist failed", err);
        }
    }, [shots, currentProject, updateProject]);

    // Duplicate a shot
    const duplicateShot = useCallback(async (index: number) => {
        const source = shots[index];
        if (!source) return;
        const synthId = `shot_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        const newShot: ShotNode = {
            ...source,
            id: synthId,
            // Generated artifacts don't carry over; user duplicates
            // the *intent* of the shot, not the output.
            videoUrl: undefined,
            videoTaskId: undefined,
            videoStatus: undefined,
            t2iImageUrl: undefined,
            t2iTaskId: undefined,
            t2iStatus: undefined,
        };
        setShots(prev => {
            const updated = [...prev];
            updated.splice(index + 1, 0, newShot);
            return updated;
        });
        setExpandedShots(prev => {
            const next = new Set(prev);
            next.add(synthId);
            return next;
        });
        if (!currentProject?.id) return;
        // Source itself isn't on backend yet — best-effort: skip remote
        // copy, the next workbench/prompt write will materialize it.
        if (source.id.startsWith("shot_")) return;
        try {
            const resp = await crudApi.copyFrame(currentProject.id, source.id, index + 1);
            const frames = Array.isArray(resp?.frames) ? resp.frames : null;
            const realFrame = frames?.[index + 1];
            if (realFrame?.id) {
                setShots(prev => prev.map(s => s.id === synthId ? { ...s, id: realFrame.id } : s));
                setExpandedShots(prev => {
                    if (!prev.has(synthId)) return prev;
                    const next = new Set(prev);
                    next.delete(synthId);
                    next.add(realFrame.id);
                    return next;
                });
            }
            if (frames) updateProject(currentProject.id, { frames });
        } catch (err) {
            debugLog.warn("Studio", "duplicateShot backend persist failed", err);
        }
    }, [shots, currentProject, updateProject]);

    // Update shot prompt — local immediate + debounced backend write
    const updatePrompt = useCallback((index: number, prompt: string) => {
        setShots(prev => prev.map((s, i) => {
            if (i !== index) return s;
            persistPrompt(s.id, prompt);
            return { ...s, prompt };
        }));
    }, [persistPrompt]);

    // Structured field updates — local immediate + debounce 3s auto-save
    const handleUpdateField = useCallback((index: number, field: string, value: string | number | null) => {
        setShots(prev => prev.map((s, i) => {
            if (i !== index) return s;
            if (field === "duration") return { ...s, duration: typeof value === "number" ? value : null };
            if (field === "shotSize") return { ...s, shotSize: typeof value === "string" ? value : null };
            if (field === "cameraAngle") return { ...s, cameraAngle: typeof value === "string" ? value : null };
            if (field === "cameraMovement") {
                const desc = typeof value === "string" ? value : "固定镜头";
                return {
                    ...s,
                    cameraMovementStructured: {
                        primary: desc,
                        speed: s.cameraMovementStructured?.speed ?? "normal",
                        description: desc,
                        secondary: s.cameraMovementStructured?.secondary ?? null,
                    },
                };
            }
            if (field === "transitionHint") return { ...s, transitionHint: typeof value === "string" ? value : null };
            return s;
        }));
        // Debounce 3s persist to backend
        const shotId = shots[index]?.id;
        if (!shotId || shotId.startsWith("shot_") || !currentProject?.id) return;
        const projectId = currentProject.id;
        const map = fieldPendingRef.current;
        const existing = map.get(shotId);
        if (existing) window.clearTimeout(existing.timer);
        const backendField: Record<string, any> = {};
        if (field === "duration") backendField.duration = typeof value === "number" ? value : undefined;
        if (field === "shotSize") backendField.shot_size = typeof value === "string" ? value : undefined;
        if (field === "cameraAngle") backendField.camera_angle = typeof value === "string" ? value : undefined;
        if (field === "cameraMovement") backendField.camera_movement_description = typeof value === "string" ? value : undefined;
        if (field === "transitionHint") backendField.transition_hint = typeof value === "string" ? value : undefined;
        const merged = { ...(existing?.fields ?? {}), ...backendField };
        const timer = window.setTimeout(() => {
            map.delete(shotId);
            api.updateFrame(projectId, shotId, merged)
                .then(() => {
                    const proj = useProjectStore.getState().currentProject;
                    if (!proj || proj.id !== projectId) return;
                    const nextFrames = (proj.frames ?? []).map((f: any) =>
                        f.id === shotId ? { ...f, ...merged } : f,
                    );
                    updateProject(projectId, { frames: nextFrames });
                })
                .catch((err) => debugLog.warn("Studio", "persistField failed", err));
        }, 3000);
        map.set(shotId, { timer, fields: merged });
    }, [shots, currentProject, updateProject]);

    // Duration editor config follows the active approved Seedance model.
    const durationEditorCfg = useMemo(() => {
        const activeModel = VIDEO_I2V_MODELS.find(m => m.id === videoConfig.model);
        const dc = activeModel?.duration;
        if (!dc) return { min: 3, max: 15, step: 1 };
        if (dc.type === "slider") return { min: dc.min, max: dc.max, step: dc.step };
        if (dc.type === "buttons") return { min: Math.min(...dc.options), max: Math.max(...dc.options), step: 1 };
        return { min: dc.value, max: dc.value, step: 1 };
    }, [videoConfig.model]);

    // Strip tags from prompt for clean text
    const cleanPrompt = (prompt: string): string => {
        return prompt.replace(/\[character\d+:[^\]]+\]/g, "").replace(/\s+/g, " ").trim();
    };

    // Generate T2I image for a shot (t2i_i2v mode stage 1)
    const generateT2I = useCallback(async (index: number) => {
        const shot = shots[index];
        if (!currentProject || !shot.prompt.trim()) return;
        const projectId = currentProject.id;
        const controller = new AbortController();
        t2iRenderControllersRef.current.get(shot.id)?.abort();
        t2iRenderControllersRef.current.set(shot.id, controller);

        setShots(prev => prev.map((s, i) =>
            i === index ? { ...s, t2iTaskId: undefined, t2iStatus: "pending" } : s
        ));

        try {
            const result = await api.renderFrame(
                projectId,
                shot.id,
                {},  // compositionData (empty for now)
                cleanPrompt(shot.prompt),
                1,   // batchSize
                { signal: controller.signal },
            );
            if (controller.signal.aborted) return;

            const generatedFrame = result?.frames?.find(
                (frame: { id?: string }) => frame.id === shot.id,
            );
            if (!generatedFrame) {
                throw new Error(`Rendered frame ${shot.id} is missing from the project response`);
            }
            const canonicalShot = frameToShotNode(
                generatedFrame,
                result.video_tasks ?? [],
                "t2i_i2v",
            );
            const activeImageUrl = getActiveT2IImageUrl(canonicalShot);
            if (!activeImageUrl) {
                throw new Error(`Rendered frame ${shot.id} has no selected first-frame image`);
            }

            updateProject(projectId, result);
            setShots(prev => prev.map((item) =>
                item.id === shot.id
                    ? {
                        ...item,
                        imageUrl: canonicalShot.imageUrl,
                        t2iImageUrl: activeImageUrl,
                        t2iImageUrls: canonicalShot.t2iImageUrls,
                        t2iSelectedIndex: canonicalShot.t2iSelectedIndex,
                        t2iTaskId: undefined,
                        t2iStatus: "completed",
                    }
                    : item,
            ));
        } catch (error) {
            if (controller.signal.aborted) return;
            debugLog.error("Studio", "Failed to generate T2I for shot:", error);
            setShots(prev => prev.map((s, i) =>
                i === index ? { ...s, t2iTaskId: undefined, t2iStatus: "failed" } : s
            ));
        } finally {
            if (t2iRenderControllersRef.current.get(shot.id) === controller) {
                t2iRenderControllersRef.current.delete(shot.id);
            }
        }
    }, [shots, currentProject, updateProject]);

    // Generate one I2V task for a shot using the selected New API model.
    const generateVideo = useCallback(async (index: number) => {
        const shot = shots[index];
        if (!currentProject || !shot?.prompt.trim()) return;
        const imageUrl = getActiveT2IImageUrl(shot) || shot.imageUrl || "";
        if (!imageUrl) {
            const message = t("i2vNeedsFirstFrame");
            setShotErrors((current) => ({ ...current, [shot.id]: message }));
            toast.warning(message);
            return;
        }
        if (!VIDEO_I2V_MODELS.some((model) => model.id === videoConfig.model)) {
            setVideoConfig((current) => ({ ...current, model: DEFAULT_I2V_MODEL_ID }));
            setShotErrors((current) => ({
                ...current,
                [shot.id]: t("unsupportedVideoModel"),
            }));
            return;
        }

        setShots((current) => current.map((item, shotIndex) =>
            shotIndex === index ? { ...item, videoStatus: "pending" } : item,
        ));

        try {
            const persistedFrame = currentProject.frames?.find((frame: any) => frame.id === shot.id) ?? shot;
            const response = await api.createVideoTask(currentProject.id, {
                image_url: imageUrl,
                prompt: buildAssembledPrompt(shot),
                frame_id: shot.id,
                source_image_id: clipImageId(imageUrl),
                frame_type: clipFrameType(persistedFrame),
                duration: videoConfig.duration,
                resolution: videoConfig.resolution,
                generate_audio: videoConfig.generateAudio,
                batch_size: 1,
                model: videoConfig.model,
                generation_mode: "i2v",
                ratio: videoConfig.ratio,
                watermark: videoConfig.watermark,
                workbench_tab: "t2i_i2v",
            });
            const task = Array.isArray(response) ? response[0] : response;
            if (task?.id) {
                setShots((current) => current.map((item, shotIndex) =>
                    shotIndex === index
                        ? { ...item, videoTaskId: task.id, videoStatus: "processing" }
                        : item,
                ));
            }
        } catch (error: any) {
            debugLog.error("Studio", "Failed to generate video for shot:", error);
            const detail = error?.response?.data?.detail || error?.message || t("unknownErrorFallback");
            toast.error(locale === "zh"
                ? t("videoGenFailedToast", { detail: "" })
                : t("videoGenFailedToast", { detail: String(detail).slice(0, 150) }));
            setShots((current) => current.map((item, shotIndex) =>
                shotIndex === index ? { ...item, videoStatus: "failed" } : item,
            ));
        }
    }, [shots, currentProject, locale, videoConfig, t]);

    // Batch generation submits independent strict-schema I2V requests.
    const generateVideoBatch = useCallback(async (
        index: number,
        count: number,
        params?: Partial<ParamsState>,
    ) => {
        const shot = shots[index];
        if (!currentProject || !shot?.prompt.trim()) return;

        const imageUrl = getActiveT2IImageUrl(shot) || shot.imageUrl || "";
        if (!imageUrl) {
            const message = t("i2vNeedsFirstFrame");
            setShotErrors((current) => ({ ...current, [shot.id]: message }));
            toast.warning(message);
            return;
        }

        const modelId = params?.model ?? videoConfig.model;
        if (!VIDEO_I2V_MODELS.some((model) => model.id === modelId)) {
            const message = t("unsupportedVideoModel");
            setShotErrors((current) => ({ ...current, [shot.id]: message }));
            toast.error(message);
            return;
        }

        if (submittingShotsRef.current.has(shot.id)) return;
        submittingShotsRef.current.add(shot.id);
        window.setTimeout(() => submittingShotsRef.current.delete(shot.id), 500);
        setShotErrors((current) => {
            const next = { ...current };
            delete next[shot.id];
            return next;
        });
        setShots((current) => current.map((item, shotIndex) =>
            shotIndex === index ? { ...item, videoStatus: "pending" } : item,
        ));

        const effectiveCount = Math.max(1, Math.min(6, count || 1));
        try {
            const createOne = async (): Promise<string | null> => {
                const persistedFrame = currentProject.frames?.find((frame: any) => frame.id === shot.id) ?? shot;
                const response = await api.createVideoTask(currentProject.id, {
                    image_url: imageUrl,
                    prompt: buildAssembledPrompt(shot),
                    frame_id: shot.id,
                    source_image_id: clipImageId(imageUrl),
                    frame_type: clipFrameType(persistedFrame),
                    duration: params?.duration ?? videoConfig.duration,
                    seed: params?.seed,
                    resolution: params?.resolution ?? videoConfig.resolution,
                    generate_audio: params?.generateAudio ?? videoConfig.generateAudio,
                    batch_size: 1,
                    model: modelId,
                    generation_mode: "i2v",
                    ratio: params?.ratio ?? videoConfig.ratio,
                    watermark: params?.watermark ?? videoConfig.watermark,
                    workbench_tab: "t2i_i2v",
                });
                const task = Array.isArray(response) ? response[0] : response;
                return task?.id ?? null;
            };

            const taskIds = (await Promise.all(
                Array.from({ length: effectiveCount }, createOne),
            )).filter((id): id is string => Boolean(id));

            if (taskIds.length === 0) throw new Error(t("videoGenSubmitFailedToast"));
            setShots((current) => current.map((item, shotIndex) =>
                shotIndex === index
                    ? {
                        ...item,
                        videoTaskId: taskIds[taskIds.length - 1],
                        videoStatus: "processing",
                    }
                    : item,
            ));
        } catch (error: any) {
            debugLog.error("Studio", "Batch generate failed for shot:", error);
            const detail = error?.response?.data?.detail || error?.message || t("unknownErrorFallback");
            setShotErrors((current) => ({
                ...current,
                [shot.id]: locale === "zh" ? t("videoGenFailedToast", { detail: "" }) : String(detail),
            }));
            toast.error(locale === "zh"
                ? t("videoGenFailedToast", { detail: "" })
                : t("videoGenFailedToast", { detail: String(detail).slice(0, 150) }));
            setShots((current) => current.map((item, shotIndex) =>
                shotIndex === index ? { ...item, videoStatus: "failed" } : item,
            ));
        }
    }, [shots, currentProject, locale, videoConfig, t]);

    // Project-level task refresh: when any task on any shot is in
    // flight, refetch the whole project every 5s. The candidates
    // panel + queue read from currentProject.video_tasks for canonical
    // state. Cheap because it's just a GET; cancels when nothing is
    // in flight. This is independent of the per-shot poll above (the
    // per-shot poll updates shot.videoStatus / videoUrl which drives
    // the ShotCard preview; the project refresh fills in candidate
    // metadata like is_starred / label / error / final video_url).
    useEffect(() => {
        if (!currentProject?.id) return;
        const allTasks: any[] = (currentProject as any).video_tasks ?? [];
        const anyInFlight = allTasks.some(
            (t) => t.status === "pending" || t.status === "processing",
        );
        // Also poll if any shot's locally-tracked videoTaskId is not
        // yet reflected in the project record (closes the just-created
        // window). With the Phase-2 derive-from-tasks model, we only
        // care about the legacy single-id mirror on the shot.
        const localInFlight = shots.some((s) => {
            const id = s.videoTaskId;
            if (!id) return false;
            const t = allTasks.find((tt) => tt.id === id);
            return !t || t.status === "pending" || t.status === "processing";
        });
        if (!anyInFlight && !localInFlight) return;
        const projectId = currentProject.id;
        const id = window.setInterval(async () => {
            try {
                const fresh = await api.getProject(projectId);
                updateProject(projectId, fresh);
            } catch {
                /* swallow — network blips are fine, next tick retries */
            }
        }, 5000);
        return () => window.clearInterval(id);
    }, [currentProject?.id, (currentProject as any)?.video_tasks, shots, updateProject]);

    // Poll for task completion (both T2I and video)
    useEffect(() => {
        const processingShots = shots.filter(s =>
            (s.videoTaskId && (s.videoStatus === "processing" || s.videoStatus === "pending")) ||
            (s.t2iTaskId && (s.t2iStatus === "processing" || s.t2iStatus === "pending"))
        );
        if (processingShots.length === 0) return;

        const interval = setInterval(async () => {
            for (const shot of processingShots) {
                // Poll video task
                if (shot.videoTaskId && (shot.videoStatus === "processing" || shot.videoStatus === "pending")) {
                    try {
                        const status = await api.getTaskStatus(shot.videoTaskId);
                        if (status.status === "completed" && status.video_url) {
                            setShots(prev => prev.map(s =>
                                s.id === shot.id ? { ...s, videoStatus: "completed", videoUrl: status.video_url } : s
                            ));
                            // Persist as the frame's active take so reloads, refines, and
                            // cross-device opens see the same hero video. Backend skips
                            // the update when the user has pinned a take. Fire-and-forget
                            // — UI already updated above, so failure here is non-fatal.
                            // Sync shot.videoUrl from backend response too: if a sibling
                            // task in the same batch completed first (so backend picked
                            // that one as active) the hero stays in sync.
                            const projectId = currentProject?.id;
                            if (projectId) {
                                api.autoSelectLatestVideo(projectId, shot.id)
                                    .then(updated => {
                                        updateProject(projectId, { frames: updated.frames });
                                        const refreshed = updated.frames?.find((f: any) => f.id === shot.id);
                                        if (refreshed?.video_url) {
                                            setShots(prev => prev.map(s =>
                                                s.id === shot.id
                                                    ? { ...s, videoUrl: refreshed.video_url, isVideoPinned: Boolean(refreshed.is_video_pinned) }
                                                    : s
                                            ));
                                        }
                                    })
                                    .catch(err => debugLog.warn("Studio", "autoSelectLatestVideo failed:", err));
                            }
                        } else if (status.status === "failed" || status.status === "canceled") {
                            setShots(prev => prev.map(s =>
                                s.id === shot.id ? { ...s, videoStatus: status.status } : s
                            ));
                        }
                    } catch (error) {
                        debugLog.error("Studio", "Video poll failed for shot:", shot.id, error);
                    }
                }
                // Poll T2I task
                if (shot.t2iTaskId && (shot.t2iStatus === "processing" || shot.t2iStatus === "pending")) {
                    try {
                        const status = await api.getTaskStatus(shot.t2iTaskId);
                        if (status.status === "completed") {
                            const imageUrl = status.image_url || status.video_url || status.result_url;
                            if (imageUrl) {
                                setShots(prev => prev.map(s => {
                                    if (s.id !== shot.id) return s;
                                    const updated = appendT2IImage({ ...s, t2iStatus: "completed" }, imageUrl);
                                    persistWorkbench(s.id, {
                                        t2i_image_urls: updated.t2iImageUrls ?? [],
                                        t2i_selected_index: updated.t2iSelectedIndex ?? 0,
                                    });
                                    return updated;
                                }));
                            }
                        } else if (status.status === "failed") {
                            setShots(prev => prev.map(s =>
                                s.id === shot.id ? { ...s, t2iStatus: "failed" } : s
                            ));
                        }
                    } catch (error) {
                        debugLog.error("Studio", "T2I poll failed for shot:", shot.id, error);
                    }
                }
            }
        }, 5000);

        return () => clearInterval(interval);
    }, [shots, persistWorkbench]);

    // Insert asset tag from drawer into target shot
    const insertAssetFromDrawer = useCallback((type: string, name: string, _ref: AssetRef) => {
        const shotIndex = drawerState.targetShotIndex;
        if (shotIndex === null || shotIndex === undefined) return;

        const tag = `[${type}:${name}]`;
        const textarea = textareaRefs.current.get(shotIndex) ?? null;
        if (textarea) {
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            const currentPrompt = shots[shotIndex].prompt;
            const newPrompt = currentPrompt.slice(0, start) + tag + currentPrompt.slice(end);
            updatePrompt(shotIndex, newPrompt);
            setTimeout(() => {
                textarea.selectionStart = textarea.selectionEnd = start + tag.length;
                textarea.focus();
            }, 0);
        } else {
            updatePrompt(shotIndex, shots[shotIndex].prompt + " " + tag);
        }
    }, [drawerState.targetShotIndex, shots, updatePrompt]);

    // ---- Project-level task derivations (drive Queue + Candidates) ----
    // We derive these via useMemo so per-render allocation is cheap and
    // children can rely on referentially-stable arrays (set-membership
    // tests in CompareModal etc. are correctness-sensitive).
    const allVideoTasks: VideoTask[] = useMemo(
        () => ((currentProject as any)?.video_tasks ?? []) as VideoTask[],
        [currentProject],
    );

    const tasksById = useMemo(() => {
        const map = new Map<string, VideoTask>();
        for (const t of allVideoTasks) map.set(t.id, t);
        return map;
    }, [allVideoTasks]);
    const autoSelectRequestsRef = useRef<Set<string>>(new Set());

    // Map shot.id → human label for the queue panel's frame column.
    const shotLabelByFrameId = useMemo(() => {
        const out: Record<string, string> = {};
        shots.forEach((s, i) => { out[s.id] = `Shot ${i + 1}`; });
        return out;
    }, [shots]);

    // In-flight aggregate count drives the TaskQueueButton badge.
    const inFlightTaskCount = useMemo(
        () => allVideoTasks.filter(t => t.status === "pending" || t.status === "processing").length,
        [allVideoTasks],
    );

    // Sync shot.videoStatus from project.video_tasks when the
    // project-level poll refreshes. Video task status is only visible
    // via project refresh (GET /tasks/ only covers asset tasks).
    useEffect(() => {
        if (!tasksById.size) return;
        const transitions: Array<{
            frameId: string;
            task: VideoTask;
            status: "completed" | "failed" | "canceled";
        }> = [];
        for (const shot of shots) {
            if (!shot.videoTaskId) continue;
            const task = tasksById.get(shot.videoTaskId);
            if (!task) continue;
            if (task.status === "completed" && shot.videoStatus !== "completed") {
                transitions.push({ frameId: shot.id, task, status: "completed" });
            } else if (task.status === "failed" && shot.videoStatus !== "failed") {
                transitions.push({ frameId: shot.id, task, status: "failed" });
            } else if (task.status === "canceled" && shot.videoStatus !== "canceled") {
                transitions.push({ frameId: shot.id, task, status: "canceled" });
            }
        }
        if (!transitions.length) return;

        const transitionByFrame = new Map(transitions.map((transition) => [transition.frameId, transition]));
        setShots(prev => {
            return prev.map(shot => {
                const transition = transitionByFrame.get(shot.id);
                if (!transition || shot.videoTaskId !== transition.task.id) return shot;
                if (transition.status === "completed") {
                    return { ...shot, videoStatus: "completed" as const, videoUrl: transition.task.video_url };
                }
                return { ...shot, videoStatus: transition.status };
            });
        });

        // Persist newly-completed videos as the frame's active take so the
        // hero survives reload / refine / cross-device opens. Backend skips
        // pinned frames; failures here are non-fatal (UI already updated).
        const projectId = currentProject?.id;
        const autoSelectFrameIds = transitions
            .filter((transition) => transition.status === "completed")
            .filter((transition) => {
                if (!projectId) return false;
                const requestKey = `${projectId}:${transition.frameId}:${transition.task.id}`;
                if (autoSelectRequestsRef.current.has(requestKey)) return false;
                autoSelectRequestsRef.current.add(requestKey);
                return true;
            })
            .map((transition) => transition.frameId);
        if (projectId && autoSelectFrameIds.length > 0) {
            const autoSelectFrameIdSet = new Set(autoSelectFrameIds);
            Promise.all(
                autoSelectFrameIds.map(frameId =>
                    api.autoSelectLatestVideo(projectId, frameId).catch(err => {
                        debugLog.warn("Studio", "autoSelectLatestVideo failed for frame:", frameId, err);
                        return null;
                    })
                )
            ).then(results => {
                // Use the last successful response's frames (all calls
                // converge on the same script state — last write wins).
                const last = results.filter(Boolean).pop() as any;
                if (last?.frames) {
                    updateProject(projectId, { frames: last.frames });
                    // Sync the hero on every auto-selected frame: backend may
                    // have picked a sibling take in the same batch, so the
                    // optimistic videoUrl set above could be stale by a hop.
                    setShots(prev => prev.map(s => {
                        if (!autoSelectFrameIdSet.has(s.id)) return s;
                        const refreshed = last.frames.find((f: any) => f.id === s.id);
                        if (!refreshed?.video_url) return s;
                        return { ...s, videoUrl: refreshed.video_url, isVideoPinned: Boolean(refreshed.is_video_pinned) };
                    }));
                }
            });
        }
    }, [tasksById, shots, currentProject?.id, updateProject]);

    // Compare modal needs the actual VideoTask objects for the
    // currently-selected ids (in whatever order they were selected).
    const compareTasks = useMemo(() => {
        const out: VideoTask[] = [];
        Array.from(compareSelectedIds).forEach((id) => {
            const t = tasksById.get(id);
            if (t) out.push(t);
        });
        return out;
    }, [compareSelectedIds, tasksById]);

    // Per-shot candidate tasks — derived directly from the project-
    // level video_tasks. New API exposes one I2V workbench per shot.
    const tasksForShot = useCallback((shot: ShotNode): VideoTask[] => {
        return allVideoTasks.filter((t) => {
            if (t.frame_id !== shot.id) return false;
            return t.workbench_tab == null || t.workbench_tab === "t2i_i2v";
        });
    }, [allVideoTasks]);

    // Build a ParamsState from videoConfig + per-shot overrides.
    // Single source of truth strategy:
    //  - Per-shot overrides (shotCounts, shotSeeds) for params whose
    //    "right value" naturally differs by shot.
    //  - videoConfig for shared knobs the user typically picks once
    //    and uses across all shots in a project.
    const paramsStateForShot = useCallback((shot: ShotNode): ParamsState => {
        return {
            model: videoConfig.model,
            duration: shot.duration ?? videoConfig.duration,
            count: shotCounts[shot.id] ?? 1,
            // Per-shot seed override (Sweep G fix); undefined means
            // "random per generation".
            seed: shotSeeds[shot.id],
            resolution: videoConfig.resolution,
            ratio: videoConfig.ratio,
            generateAudio: videoConfig.generateAudio,
            watermark: videoConfig.watermark,
        };
    }, [videoConfig, shotCounts, shotSeeds]);

    // ParamsSection.onChange handler: per-shot overrides (count, seed)
    // go into their dedicated maps; everything else writes back to
    // the shared videoConfig (so the user's most-recent picks become
    // the new default for siblings). videoConfig is mirrored to
    // localStorage as a recovery cache only — the authoritative model
    // selection lives in project.model_settings, written via the
    // 生成设置 modal.
    const handleShotParamsChange = useCallback((shot: ShotNode, next: ParamsState) => {
        if ((shotCounts[shot.id] ?? 1) !== next.count) {
            persistWorkbench(shot.id, { workbench_generate_count: next.count });
        }
        setShotCounts(prev => ({ ...prev, [shot.id]: next.count }));
        // Sync duration back to structured field (single source of truth)
        if (next.duration !== (shot.duration ?? videoConfig.duration)) {
            const idx = shots.findIndex(s => s.id === shot.id);
            if (idx >= 0) handleUpdateField(idx, "duration", next.duration);
        }
        // Seed: track per-shot. Undefined ↔ "random" — stored as
        // delete-from-map so the entry doesn't accrete forever.
        setShotSeeds(prev => {
            const wasSet = prev[shot.id] !== undefined;
            const isSet = next.seed !== undefined && !Number.isNaN(next.seed);
            if (!wasSet && !isSet) return prev;
            if (wasSet && !isSet) {
                const out = { ...prev };
                delete out[shot.id];
                return out;
            }
            if (prev[shot.id] === next.seed) return prev;
            return { ...prev, [shot.id]: next.seed };
        });
        setVideoConfig(prev => {
            const updated: VideoConfig = {
                ...prev,
                duration: next.duration,
                resolution: next.resolution ?? prev.resolution,
                ratio: next.ratio ?? prev.ratio,
                generateAudio: next.generateAudio ?? prev.generateAudio,
                watermark: next.watermark ?? prev.watermark,
                model: next.model,
            };
            writeWorkspaceItem("storyboard-newapi-video-model", next.model);
            return updated;
        });
    }, [persistWorkbench, shotCounts]);

    // Annotate handlers wire CandidateThumb's star/label CTAs to the
    // backend PATCH endpoint. We refresh the project after each call
    // so the candidate cell re-renders with the new flag without
    // waiting for the 5s polling tick.
    const refreshProject = useCallback(async () => {
        if (!currentProject?.id) return;
        try {
            const fresh = await api.getProject(currentProject.id);
            updateProject(currentProject.id, fresh);
        } catch { /* swallow */ }
    }, [currentProject, updateProject]);

    const handleToggleStar = useCallback(async (task: VideoTask, next: boolean) => {
        if (!currentProject?.id) return;
        try {
            await api.annotateVideoTask(currentProject.id, task.id, { is_starred: next });
            await refreshProject();
        } catch (err) {
            debugLog.error("Studio", "Failed to toggle star:", err);
        }
    }, [currentProject, refreshProject]);

    // Manual pin: user explicitly chose this take as the frame's active
    // video. Backend sets is_video_pinned=true so subsequent auto-selects
    // (polling completion) skip this frame. Locally we update the shot's
    // videoUrl + videoStatus immediately so the hero swaps without waiting
    // for the project refresh round-trip.
    const handleSetActive = useCallback(async (frameId: string, task: VideoTask) => {
        if (!currentProject?.id) return;
        const taskVideoUrl = (task as any).video_url as string | undefined;
        setShots(prev => prev.map(s =>
            s.id === frameId
                ? { ...s, videoUrl: taskVideoUrl, videoStatus: "completed" as const, isVideoPinned: true }
                : s
        ));
        try {
            const updated = await api.selectVideo(currentProject.id, frameId, task.id);
            updateProject(currentProject.id, { frames: updated.frames });
        } catch (err) {
            debugLog.error("Studio", "Failed to set active take:", err);
        }
    }, [currentProject, updateProject]);

    // Unpin: clear the manual pin so auto-select resumes on next
    // completion. Selected_video_id / video_url stay put — user keeps
    // seeing the current take until a newer one arrives.
    const handleUnpinVideo = useCallback(async (frameId: string) => {
        if (!currentProject?.id) return;
        setShots(prev => prev.map(s =>
            s.id === frameId ? { ...s, isVideoPinned: false } : s
        ));
        try {
            const updated = await api.unpinVideo(currentProject.id, frameId);
            updateProject(currentProject.id, { frames: updated.frames });
        } catch (err) {
            debugLog.error("Studio", "Failed to unpin video:", err);
        }
    }, [currentProject, updateProject]);

    const handleSetLabel = useCallback(async (task: VideoTask, next: string | null) => {
        if (!currentProject?.id) return;
        try {
            if (next === null || next === "") {
                await api.annotateVideoTask(currentProject.id, task.id, { clear_label: true });
            } else {
                await api.annotateVideoTask(currentProject.id, task.id, { label: next });
            }
            await refreshProject();
        } catch (err) {
            debugLog.error("Studio", "Failed to set label:", err);
        }
    }, [currentProject, refreshProject]);

    const handleCancelTask = useCallback(async (task: VideoTask) => {
        if (!currentProject?.id) return;
        try {
            await api.cancelVideoTask(currentProject.id, task.id);
            await refreshProject();
        } catch (err) {
            debugLog.error("Studio", "Failed to cancel task:", err);
        }
    }, [currentProject, refreshProject]);

    // Retry the same durable task so its original prompt, input image,
    // model and output parameters are preserved exactly.
    const handleRetryTask = useCallback(async (task: VideoTask) => {
        if (!currentProject?.id) return;
        try {
            const retried = await api.retryVideoTask(currentProject.id, task.id);
            updateProject(currentProject.id, {
                video_tasks: allVideoTasks.map((item) => item.id === task.id ? retried : item),
            });
            if (task.frame_id) {
                setShots((current) => current.map((shot) =>
                    shot.id === task.frame_id
                        ? { ...shot, videoTaskId: task.id, videoStatus: "pending" }
                        : shot
                ));
            }
        } catch (error) {
            debugLog.error("Studio", "Failed to retry task:", error);
            toast.error(t("retryFailed"));
        }
    }, [allVideoTasks, currentProject, t, updateProject]);

    // Click on a candidate thumb: plain click = preview (open new
    // window for v1), shift-click = toggle compare-selection.
    const handleCandidateClick = useCallback((task: VideoTask, mods: { shift: boolean; meta: boolean }) => {
        if (mods.shift) {
            setCompareSelectedIds(prev => {
                const next = new Set(prev);
                if (next.has(task.id)) next.delete(task.id);
                else next.add(task.id);
                return next;
            });
            return;
        }
        const frame = currentProject?.frames?.find((f: any) => f.dubbed_video_task_id === task.id);
        const url = frame?.dubbed_video_url || task.video_url;
        if (url) {
            window.open(getAssetUrl(url), "_blank", "noopener");
        }
    }, [currentProject]);

    // Reuse the selected New API model and output settings from a batch.
    // so the next Generate uses the same recipe. We don't change count
    // here — count remains the per-shot knob the user chose.
    const handleReuseBatchParams = useCallback((batch: BatchSummary) => {
        const first = batch.tasks[0];
        if (!first) return;
        setVideoConfig(prev => {
            const updated = { ...prev };
            if (VIDEO_I2V_MODELS.some(m => m.id === first.model)) {
                updated.model = first.model!;
            }
            if (first.duration) updated.duration = first.duration;
            if (first.resolution) updated.resolution = first.resolution;
            return updated;
        });
    }, []);

    // Queue's jump-to-shot: scroll the shot's wrapper into view AND
    // expand the shot panel + its sections (otherwise jumping to a
    // collapsed shot lands the user on a 1-line strip and they have
    // to expand manually).
    const handleJumpToShot = useCallback((frameId: string) => {
        setExpandedShots(prev => {
            if (prev.has(frameId)) return prev;
            const next = new Set(prev);
            next.add(frameId);
            return next;
        });
        // Force inner sections open too — feels right when arriving from
        // a queue task: you want to see Params + Candidates for that shot.
        overridePanelSectionState([frameId], ["params", "candidates"], true);
        // Scroll after the next paint so the newly-expanded body is
        // measured correctly. RAF is sufficient — we don't need the
        // full layout effect cycle.
        requestAnimationFrame(() => {
            const el = shotWrapperRefs.current.get(frameId);
            if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    }, []);

    // Active candidate URL resolver — many backend video URLs are
    // relative paths needing the asset prefix to render in <video>.
    const resolveAssetUrl = useCallback((u: string) => getAssetUrl(u), []);

    // In-flight shot count for trailing slot stat
    const totalInFlight = useMemo(
        () => Object.values(shotCounts).reduce((acc: number, c: any) => acc + (c?.processing ?? 0) + (c?.pending ?? 0), 0),
        [shotCounts],
    );

    return (
        // Layout v4: outer horizontal split. Custom page header belongs
        // to main column (not page-wide), so the right TaskQueuePanel can
        // be a true floor-to-ceiling sidebar with its own SidePanelHeader.
        <div className="h-full flex overflow-hidden relative">
        {/* Main column — pushed (compressed) when the queue panel opens
            so the queue doesn't overlay content. Bloom/grain now global in
            ProjectClient so the whole pipeline shares one atmosphere. */}
        <div className="relative z-10 flex-1 flex flex-col overflow-hidden min-w-0">
            {/* Unified page header (shared StepPageHeader) */}
            <StepPageHeader
                title={tStep("storyboardTitle")}
                trailing={(
                    <>
                        <TaskQueueButton
                            inFlightCount={inFlightTaskCount}
                            open={queueOpen}
                            onToggle={() => setQueueOpen(v => !v)}
                        />
                        <button
                            type="button"
                            onClick={() => setGenDialogOpen(true)}
                            disabled={generating}
                            className="inline-flex h-8 items-center gap-1.5 rounded-full bg-primary px-4 py-1.5 font-sans text-[0.8125rem] font-semibold text-on-accent shadow-[var(--btn-pri-glow),inset_0_1.5px_0_rgba(255,255,255,0.14)] transition-all duration-fast ease-out-quart hover:bg-primary-hover disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            {generating ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                            <span>{generating ? t("genInFlight") : t("genShots")}</span>
                        </button>
                    </>
                )}
            />
            {/* Top Toolbar — mock-aligned: count on the left, expand/collapse pills on the right */}
            <div className="flex flex-wrap items-center gap-3 px-4 py-3 shrink-0 sm:px-6">
                <div className="flex items-center gap-3">
                    <span className="font-mono text-[11px] tracking-[0.04em] text-text-secondary">
                        <span className="text-foreground font-medium">{shots.length}</span>
                        <span className="ml-1.5 uppercase">{shots.length === 1 ? t("shot") : t("shots")}</span>
                        {totalInFlight > 0 ? <span className="ml-2 text-status-processing-fg">· {totalInFlight} {t("inFlightShort")}</span> : null}
                    </span>
                    <motion.button
                        whileHover={{ scale: 1.04 }}
                        whileTap={{ scale: 0.96 }}
                        onClick={() => addShot(shots.length - 1)}
                        className="flex items-center gap-1 px-2 py-1 text-xs font-medium text-primary hover:text-primary/80 transition-colors"
                    >
                        <Plus size={13} strokeWidth={2} />
                        {t("addShot")}
                    </motion.button>
                </div>
                {shots.length > 1 ? (
                    <div className="ml-auto flex items-center gap-2">
                        <button
                            type="button"
                            onClick={expandAllShots}
                            title={t("expandAll")}
                            className="inline-flex h-8 items-center gap-1.5 rounded-full border border-glass-border bg-transparent px-3.5 font-mono text-[13px] uppercase tracking-[0.06em] text-text-secondary transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            <PanelBottomOpen size={12} strokeWidth={1.8} />
                            {t("expandAll")}
                        </button>
                        <button
                            type="button"
                            onClick={collapseAllShots}
                            title={t("collapseAll")}
                            className="inline-flex h-8 items-center gap-1.5 rounded-full border border-glass-border bg-transparent px-3.5 font-mono text-[13px] uppercase tracking-[0.06em] text-text-secondary transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            <PanelBottomClose size={12} strokeWidth={1.8} />
                            {t("collapseAll")}
                        </button>
                    </div>
                ) : null}
            </div>

            <GenerationBanner
                state={bannerState}
                phase1Captions={PHASE1_CAPTIONS}
                refineProgress={refineProgress}
                summary={bannerSummary}
            />

            <div className="flex-1 overflow-y-auto px-5 pt-1.5 pb-10 space-y-5 sm:px-7">
                {shots.length === 0 && (
                    <div className="h-full min-h-[300px] flex flex-col items-center justify-center text-center px-6">
                        <div className="rounded-2xl border border-glass-border bg-glass p-8 max-w-lg">
                            <div className="mx-auto w-12 h-12 grid place-items-center rounded-full bg-primary/10 border border-primary/30 mb-4">
                                <Wand2 size={20} className="text-primary" />
                            </div>
                            <h3 className="text-display font-medium text-foreground">{t("emptyTitle")}</h3>
                            <p className="text-body-sm text-text-secondary mt-1.5 max-w-md mx-auto leading-relaxed">
                                {t("emptyBody")}
                            </p>
                            <div className="mt-5 flex items-center justify-center gap-2">
                                <button
                                    type="button"
                                    onClick={() => setGenDialogOpen(true)}
                                    disabled={generating}
                                    className="inline-flex items-center gap-1.5 px-5 py-2.5 rounded-md bg-primary text-white border border-primary/65 shadow-[inset_0_1.5px_0_rgba(255,255,255,0.14)] hover:bg-primary-hover disabled:opacity-40 transition-colors text-[0.8125rem] font-semibold"
                                >
                                    {generating ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                                    {generating ? t("genInFlight") : t("emptyCTA")}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => addShot(-1)}
                                    className="inline-flex items-center gap-1.5 px-4 py-2.5 rounded-md bg-glass border border-glass-border text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors text-[0.75rem]"
                                >
                                    <Plus size={12} />
                                    {t("emptyManualAdd")}
                                </button>
                            </div>
                        </div>
                    </div>
                )}
                {shots.map((shot, index) => {
                    const shotTasks = tasksForShot(shot);
                    const shotInFlight = shotTasks.filter(
                        (t) => t.status === "pending" || t.status === "processing",
                    ).length;
                    const paramsState = paramsStateForShot(shot);
                    const isI2vTab = true;
                    const modelList = VIDEO_I2V_MODELS;
                    return (
                    /* Plain div (was motion.div) — staggered enter
                       animation re-fired every time the user switched
                       step tabs and came back, causing a noticeable
                       全 list opacity flicker. ShotCard hover micro-
                       motion is kept inside the card itself. */
                    <div
                        key={shot.id}
                        ref={(el) => { shotWrapperRefs.current.set(shot.id, el); }}
                    >
                        <ShotCard
                            shot={shot}
                            index={index}
                            totalShots={shots.length}
                            characters={characters}
                            scenes={scenes}
                            props={props}
                            onUpdatePrompt={(prompt) => updatePrompt(index, prompt)}
                            onUpdateField={(field, value) => handleUpdateField(index, field, value)}
                            durationEditorConfig={durationEditorCfg}
                            onGenerateT2I={() => generateT2I(index)}
                            onGenerateVideo={() => generateVideo(index)}
                            onDelete={() => deleteShot(index)}
                            isDeleting={deletingShotIds.has(shot.id)}
                            onMoveUp={() => moveShot(index, "up")}
                            onMoveDown={() => moveShot(index, "down")}
                            onDuplicate={() => duplicateShot(index)}
                            onOpenDrawer={() => setDrawerState({ isOpen: true, targetShotIndex: index })}
                            onCancelVideo={
                                shot.videoTaskId && currentProject
                                    ? async () => {
                                        const projectId = currentProject.id;
                                        const taskId = shot.videoTaskId!;
                                        try {
                                            await api.cancelVideoTask(projectId, taskId);
                                        } finally {
                                            // Optimistic local flip — backend has
                                            // already marked failed, but the next
                                            // refetch may take a beat. Failed state
                                            // surfaces the existing Retry button.
                                            setShots(prev => prev.map((s, i) =>
                                                i === index ? { ...s, videoStatus: "failed" as const } : s,
                                            ));
                                        }
                                    }
                                    : undefined
                            }
                            expanded={expandedShots.has(shot.id)}
                            onToggleExpanded={() => toggleShotExpanded(shot.id)}
                            /* The New API workbench is keyframe + I2V only. */
                            generateCount={paramsState.count}
                            genSummary={`${modelDisplayName(videoConfig.model)} · ${paramsState.duration}秒`}
                            canGenerate={
                                shot.prompt.trim().length > 0
                                && (!!shot.t2iImageUrl || (shot.t2iImageUrls?.length ?? 0) > 0)
                            }
                            onSetGenerateCount={(n) => handleShotParamsChange(shot, { ...paramsState, count: n })}
                            onGenerateBatch={(n) => generateVideoBatch(index, n, paramsState)}
                            inFlightCount={shotInFlight}
                            onRefineFrame={() => handleRefineFrame(shot.id)}
                            onUnpinVideo={() => handleUnpinVideo(shot.id)}
                            onUpdateDialogue={async (text: string) => {
                                if (!currentProject) return;
                                try {
                                    await api.updateFrame(currentProject.id, shot.id, { dialogue: text });
                                    const updated = await api.getProject(currentProject.id);
                                    if (updated?.frames) updateProject(currentProject.id, { frames: updated.frames });
                                } catch (e) {
                                    debugLog.error("Studio", "update dialogue failed", e);
                                }
                            }}
                        />
                        {/* New API workbench: first frame, I2V params, candidates. */}
                        {expandedShots.has(shot.id) ? (
                        <div className="mx-5 mb-[18px] motion-safe:animate-[shotPanelIn_220ms_cubic-bezier(0.22,1,0.36,1)_both]">
                            {isI2vTab ? (
                                <div>
                                    <T2ISubsection
                                        imageUrls={shot.t2iImageUrls ?? []}
                                        selectedIndex={shot.t2iSelectedIndex ?? 0}
                                        storyboardFrameUrl={shot.imageUrl || undefined}
                                        promptIsEmpty={!shot.prompt.trim()}
                                        generating={shot.t2iStatus === "pending" || shot.t2iStatus === "processing"}
                                        inFlightTaskId={shot.t2iTaskId}
                                        inFlightStatus={shot.t2iStatus}
                                        onSelect={(i) => setShots(prev => prev.map((s, j) => {
                                            if (j !== index) return s;
                                            const next = setActiveT2IIndex(s, i);
                                            persistWorkbench(s.id, {
                                                t2i_selected_index: next.t2iSelectedIndex ?? 0,
                                            });
                                            return next;
                                        }))}
                                        onRemove={(i) => { void deleteT2IImage(index, i); }}
                                        onGenerate={() => generateT2I(index)}
                                        onUpload={async (file) => {
                                            // Issue 10: upload an external image as a T2I首帧 candidate.
                                            // Backend appends + auto-selects; we mirror state from the
                                            // returned frame (single source of truth for the URL the
                                            // server actually persisted).
                                            //
                                            // Frontend may hold a synthetic shot id (`shot_<ts>_<rand>`)
                                            // for shots created via the + button that haven't been
                                            // persisted yet. The backend has no such frame_id → 404.
                                            // Lazy-create the frame on backend first, then upload.
                                            if (!currentProject) return { code: "network", detail: "no current project" };
                                            try {
                                                let effectiveFrameId = shot.id;
                                                const isSynthetic = effectiveFrameId.startsWith("shot_");
                                                if (isSynthetic) {
                                                    // Materialize the shot on backend before any
                                                    // frame-scoped op. Send minimum viable payload —
                                                    // the prompt + tab mode survives via separate
                                                    // workbench PATCH calls already triggered elsewhere.
                                                    try {
                                                        const created = await crudApi.createFrame(currentProject.id, {
                                                            scene_id: "",
                                                            action_description: shot.prompt || "",
                                                            insert_at: index,
                                                        } as any);
                                                        // Find the newly inserted frame by index in the response
                                                        const newFrame = Array.isArray(created?.frames)
                                                            ? created.frames[Math.min(index, created.frames.length - 1)]
                                                            : null;
                                                        if (newFrame?.id) {
                                                            effectiveFrameId = newFrame.id;
                                                            // Swap synthetic id → backend id locally so
                                                            // subsequent ops (workbench persist, generate, etc.)
                                                            // hit the real frame.
                                                            setShots(prev => prev.map((s, j) =>
                                                                j === index ? { ...s, id: newFrame.id } : s,
                                                            ));
                                                        }
                                                    } catch (createErr: any) {
                                                        debugLog.error("Studio", "Lazy createFrame failed", createErr);
                                                        const cdetail = createErr?.response?.data?.detail || createErr?.message || "create frame failed";
                                                        return { code: "server", detail: `先创建镜头失败：${cdetail}` };
                                                    }
                                                }

                                                const updatedFrame = await api.uploadT2IFrame(
                                                    currentProject.id,
                                                    effectiveFrameId,
                                                    file,
                                                );
                                                if (!updatedFrame) return { code: "network", detail: "empty response" };
                                                const nextUrls: string[] = updatedFrame.t2i_image_urls ?? [];
                                                const nextIdx: number = typeof updatedFrame.t2i_selected_index === "number"
                                                    ? updatedFrame.t2i_selected_index
                                                    : Math.max(0, nextUrls.length - 1);
                                                setShots(prev => prev.map((s, j) => {
                                                    if (j !== index) return s;
                                                    return {
                                                        ...s,
                                                        t2iImageUrls: nextUrls,
                                                        t2iSelectedIndex: nextIdx,
                                                        t2iImageUrl: nextUrls[nextIdx],
                                                        t2iStatus: "completed",
                                                    };
                                                }));
                                                return undefined;
                                            } catch (err: any) {
                                                debugLog.error("Studio", "T2I upload failed", err);
                                                const status = err?.response?.status;
                                                // Always surface the backend detail string so the
                                                // user can self-diagnose ("frame not found", "OSS
                                                // write denied", etc.) instead of "请重试".
                                                const detail = err?.response?.data?.detail
                                                    || err?.message
                                                    || `HTTP ${status ?? "?"}`;
                                                if (status === 413) return { code: "size", detail: String(detail) };
                                                if (status === 415) return { code: "type", detail: String(detail) };
                                                if (status === 404) return { code: "not_found", detail: String(detail) };
                                                if (status && status >= 500) return { code: "server", detail: String(detail) };
                                                return { code: "network", detail: String(detail) };
                                            }
                                        }}
                                        resolveUrl={resolveAssetUrl}
                                    />
                                </div>
                            ) : null}
                            {/* Step 2 · video generation params. */}
                            <div className={isI2vTab ? "border-t border-glass-border" : ""}>
                                <ParamsSection
                                    shotId={shot.id}
                                    modelList={modelList}
                                    title={t("i2vParams")}
                                    params={paramsState}
                                    onChange={(next) => handleShotParamsChange(shot, next)}
                                    inFlightCount={shotInFlight}
                                    errorMessage={shotErrors[shot.id] ?? null}
                                />
                            </div>
                            <div className="border-t border-glass-border">
                                <CandidatesSection
                                    shotId={shot.id}
                                    tasks={shotTasks}
                                    activeModel={paramsState.model}
                                    compareSelectedIds={compareSelectedIds}
                                    activeTaskId={currentProject?.frames?.find((f: any) => f.id === shot.id)?.selected_video_id ?? null}
                                    dubbedVideoUrl={currentProject?.frames?.find((f: any) => f.id === shot.id)?.dubbed_video_url}
                                    dubbedVideoTaskId={currentProject?.frames?.find((f: any) => f.id === shot.id)?.dubbed_video_task_id}
                                    onClickThumb={handleCandidateClick}
                                    onToggleStar={handleToggleStar}
                                    onSetLabel={handleSetLabel}
                                    onSetActive={(task) => handleSetActive(shot.id, task)}
                                    onCancel={handleCancelTask}
                                    onRetry={handleRetryTask}
                                    onReuseBatchParams={handleReuseBatchParams}
                                    onOpenCompare={() => setCompareModalOpen(true)}
                                    resolveUrl={resolveAssetUrl}
                                />
                            </div>
                        </div>
                        ) : null}
                    </div>
                    );
                })}

                {/* Add shot at end */}
                <motion.button
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: Math.min(shots.length * 0.03, 0.3) }}
                    whileHover={{ scale: 1.005 }}
                    whileTap={{ scale: 0.995 }}
                    onClick={() => addShot(shots.length - 1)}
                    className="w-full py-3.5 border border-dashed border-glass-border hover:border-primary/40 rounded-xl text-text-secondary hover:text-primary text-sm font-medium transition-all duration-300 flex items-center justify-center gap-2 bg-glass hover:bg-hover-bg"
                >
                    <Plus size={16} strokeWidth={1.5} />
                    {t("addShot")}
                </motion.button>
            </div>

            {/* Asset Drawer (fixed overlay) */}
            <AssetDrawer
                isOpen={drawerState.isOpen}
                onClose={() => setDrawerState({ isOpen: false, targetShotIndex: null })}
                characters={characters}
                scenes={scenes}
                props={props}
                onSelectAsset={insertAssetFromDrawer}
            />
        </div>
        {/* Right-side Task Queue — pushes (does not overlay) the main
            column. Mounted in the layout flex, not as a fixed overlay,
            so width compression is automatic when it opens. */}
        <TaskQueuePanel
            open={queueOpen}
            onClose={() => setQueueOpen(false)}
            tasks={allVideoTasks}
            shotLabelByFrameId={shotLabelByFrameId}
            onJumpToShot={handleJumpToShot}
            onCancel={handleCancelTask}
            onRetry={handleRetryTask}
        />
        {/* Compare modal — portaled to body to escape clipped/transformed
            ancestors. Shows once user has shift-selected ≥2 and clicked
            the floating Compare button in any CandidatesSection. */}
        {compareModalOpen && compareTasks.length >= 2 ? (
            <CompareModal
                tasks={compareTasks}
                onClose={() => setCompareModalOpen(false)}
                resolveUrl={resolveAssetUrl}
            />
        ) : null}
        {/* LLM-generate frames dialog */}
        <StoryboardGenerateDialog
            isOpen={genDialogOpen}
            onClose={() => setGenDialogOpen(false)}
            project={currentProject as any}
            existingShotCount={shots.length}
            onConfirm={handleSmartGenerate}
            onJumpToScript={() => {
                setGenDialogOpen(false);
                window.dispatchEvent(new CustomEvent("navigateStep", { detail: "script" }));
            }}
        />
        </div>
    );
}
