"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useTranslations } from "next-intl";
import { useProjectStore, type Project } from "@/store/projectStore";
import VideoCreator from "./VideoCreator";
import VideoSidebar from "./VideoSidebar";
import { VideoTask } from "@/lib/api";
import { observeProjectTasks } from "@/lib/projectTaskObserver";
import { resolveModelId } from "@/lib/modelCatalog";
import StepPageHeader from "@/components/shared/StepPageHeader";
import ResizableSidePanel from "@/components/layout/ResizableSidePanel";

const MOTION_RIGHT_PANEL_STORAGE_KEY = "enmotion:motion:right-panel";

export default function VideoGenerator() {
    const tStep = useTranslations("stepHeader");
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const currentProjectId = currentProject?.id;
    const tasks = useMemo(
        () => (currentProject?.video_tasks ?? []) as VideoTask[],
        [currentProject?.video_tasks],
    );

    // Shared state for Remix functionality
    const [remixData, setRemixData] = useState<Partial<VideoTask> | null>(null);

    // Get default model from project settings
    const defaultI2vModel = resolveModelId(
        'i2v',
        currentProject?.model_settings?.video_model ?? currentProject?.model_settings?.i2v_model,
        'video_sidebar',
    );

    // Generation Params (Lifted State)
    const [params, setParams] = useState({
        resolution: "720p",
        duration: 5,
        seed: undefined as number | undefined,
        generateAudio: true,
        batchSize: 1,
        model: defaultI2vModel,
        ratio: "16:9",
        watermark: false,
    });
    const [syncedDefaultModel, setSyncedDefaultModel] = useState(defaultI2vModel);

    // Update the lifted form state before rendering when project defaults
    // change; user selection remains untouched while the default is stable.
    if (defaultI2vModel !== syncedDefaultModel) {
        setSyncedDefaultModel(defaultI2vModel);
        setParams((p) => ({
            ...p,
            model: defaultI2vModel,
        }));
    }

    const handleRemixClear = useCallback(() => setRemixData(null), []);

    // Share one visibility-aware refresh loop with every workflow observing
    // this project. The coordinator prevents overlapping or duplicate GETs.
    useEffect(() => {
        const hasActiveTasks = tasks.some(t => t.status === "pending" || t.status === "processing");
        if (!hasActiveTasks || !currentProjectId) return;

        return observeProjectTasks(currentProjectId, {
            onProject: (project) => {
                updateProject(currentProjectId, {
                    video_tasks: project.video_tasks ?? [],
                    frames: project.frames ?? [],
                });
            },
            onError: (error) => {
                console.error("Failed to poll project status:", error);
            },
        });
    }, [tasks, currentProjectId, updateProject]);

    const handleTaskCreated = (updatedProject: Project) => {
        if (currentProject && updatedProject.video_tasks) {
            updateProject(currentProject.id, {
                video_tasks: updatedProject.video_tasks,
                ...(updatedProject.frames ? { frames: updatedProject.frames } : {}),
            });
        }
    };

    const handleRemix = (task: VideoTask) => {
        setRemixData({
            image_url: task.image_url,
            prompt: task.prompt,
            frame_id: task.frame_id,
            source_image_id: task.source_image_id,
            frame_type: task.frame_type,
            generation_mode: task.generation_mode,
            seed: task.seed,
            duration: task.duration,
        });

        // Update params state
        setParams(p => ({
            ...p,
            duration: task.duration || 5,
            seed: task.seed,
            resolution: task.resolution || "720p",
            generateAudio: task.generate_audio,
        }));
    };

    return (
        <div className="flex flex-col h-full w-full overflow-hidden">
            <StepPageHeader
                title={tStep("motionTitle")}
            />
            <div className="flex min-h-0 flex-1 overflow-hidden">
                <div className="min-w-0 flex-1">
                    <VideoCreator
                        onTaskCreated={handleTaskCreated}
                        remixData={remixData}
                        onRemixClear={handleRemixClear}
                        params={params}
                    />
                </div>

                <ResizableSidePanel
                    side="right"
                    storageKey={MOTION_RIGHT_PANEL_STORAGE_KEY}
                    defaultWidth={360}
                    minWidth={280}
                    maxWidth={560}
                    minRemainingWidth={360}
                >
                    <VideoSidebar
                        tasks={tasks}
                        onRemix={handleRemix}
                        params={params}
                        setParams={setParams}
                    />
                </ResizableSidePanel>
            </div>
        </div>
    );
}
