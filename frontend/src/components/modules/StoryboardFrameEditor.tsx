"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { ChevronDown, Loader2, Video, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import {
    FRAME_MOVEMENT_TYPES,
    frameMovementTypeFromFrame,
    type FrameMovementType,
} from "@/lib/frameMovement";
import { VariantSelector } from "../common/VariantSelector";
import { useProjectStore } from "@/store/projectStore";
import { selectedStoryboardImage } from "@/lib/clipStartFrame";
import { useModalFocusTrap } from "@/components/common/useModalFocusTrap";

interface StoryboardFrameEditorProps {
    frame: any;
    onClose: () => void;
}

export default function StoryboardFrameEditor({ frame: initialFrame, onClose }: StoryboardFrameEditorProps) {
    const ts = useTranslations("storyboard");
    const tc = useTranslations("common");
    const currentProject = useProjectStore(state => state.currentProject);
    const updateProject = useProjectStore(state => state.updateProject);

    // Get the latest frame data from the store (instead of using stale prop)
    const frame = currentProject?.frames?.find((f: any) => f.id === initialFrame.id) || initialFrame;

    const externalPrompt = frame.image_prompt || frame.action_description || "";
    const [prompt, setPrompt] = useState(externalPrompt);
    const [syncedPrompt, setSyncedPrompt] = useState(externalPrompt);
    const [isGenerating, setIsGenerating] = useState(false);
    const externalFrameType = frameMovementTypeFromFrame(frame) ?? "";
    const [frameType, setFrameType] = useState<FrameMovementType | "">(externalFrameType);
    const [syncedFrameType, setSyncedFrameType] = useState<FrameMovementType | "">(externalFrameType);
    const [isSavingFrameType, setIsSavingFrameType] = useState(false);
    const [frameTypeError, setFrameTypeError] = useState("");
    const dialogRef = useModalFocusTrap<HTMLDivElement>(onClose);

    // Adjust before rendering children when the store replaces this frame's
    // prompt. Local edits remain intact while the external value is stable.
    if (externalPrompt !== syncedPrompt) {
        setSyncedPrompt(externalPrompt);
        setPrompt(externalPrompt);
    }

    if (externalFrameType !== syncedFrameType) {
        setSyncedFrameType(externalFrameType);
        setFrameType(externalFrameType);
    }

    const handleGenerate = async (batchSize: number) => {
        if (!currentProject) return;

        setIsGenerating(true);
        try {
            // Construct composition data (simplified for now, ideally passed from parent or re-calculated)
            // For re-rendering, we might want to reuse existing composition data or just rely on prompt/I2I
            // The api.renderFrame expects compositionData.
            // If we don't pass it, pipeline uses existing.

            const updatedProject = await api.renderFrame(
                currentProject.id,
                frame.id,
                null, // Use existing composition data
                prompt,
                batchSize
            );
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to generate frame:", error);
            alert(ts("generateFailed"));
        } finally {
            setIsGenerating(false);
        }
    };

    const handleSelectVariant = async (variantId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.selectAssetVariant(currentProject.id, frame.id, "storyboard_frame", variantId);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to select variant:", error);
        }
    };

    const handleDeleteVariant = async (variantId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.deleteAssetVariant(currentProject.id, frame.id, "storyboard_frame", variantId);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete variant:", error);
        }
    };

    const handleFrameTypeChange = async (nextType: FrameMovementType) => {
        if (!currentProject) return;

        const previousType = frameType;
        setFrameType(nextType);
        setFrameTypeError("");
        setIsSavingFrameType(true);
        try {
            const updatedProject = await api.updateFrame(currentProject.id, frame.id, {
                camera_movement: nextType,
            });
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to update frame type:", error);
            setFrameType(previousType);
            setFrameTypeError(ts("frameTypeSaveFailed"));
        } finally {
            setIsSavingFrameType(false);
        }
    };

    return (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-md p-4 md:p-8">
            <motion.div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby="storyboard-frame-editor-title"
                tabIndex={-1}
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-elevated border border-glass-border rounded-2xl w-full max-w-6xl h-[85vh] flex flex-col overflow-hidden shadow-lg"
            >
                {/* Header */}
                <div className="h-16 border-b border-glass-border flex justify-between items-center px-6 bg-surface">
                    <div className="flex items-center gap-4">
                        <h2 id="storyboard-frame-editor-title" className="text-xl font-bold text-foreground">{ts("frameEditor")} <span className="text-text-muted font-normal text-sm ml-2">#{frame.id.substring(0, 8)}</span></h2>
                    </div>
                    <button onClick={onClose} aria-label={tc("close")} className="p-2 hover:bg-hover-bg rounded-full text-text-secondary hover:text-foreground transition-colors">
                        <X size={24} />
                    </button>
                </div>

                {/* Content */}
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
                    {/* Left: Variant Selector */}
                    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-surface p-4">
                        <VariantSelector
                            asset={frame.rendered_image_asset}
                            currentImageUrl={selectedStoryboardImage(frame)?.url}
                            onSelect={handleSelectVariant}
                            onDelete={handleDeleteVariant}
                            onGenerate={handleGenerate}
                            isGenerating={isGenerating}
                            aspectRatio="16:9"
                            className="h-full"
                        />
                    </div>

                    {/* Right: Controls & Prompt */}
                    <div className="flex max-h-[45%] w-full flex-col overflow-y-auto border-t border-glass-border bg-elevated md:max-h-none md:w-1/3 md:min-w-[350px] md:border-l md:border-t-0">
                        <div className="p-4 border-b border-border-subtle">
                            <h3 className="font-bold text-sm uppercase tracking-wider text-text-secondary mb-2">
                                {ts("sceneContext")}
                            </h3>
                            <p className="text-xs text-text-secondary mb-2">
                                <span className="font-bold text-text-muted">{ts("action")}:</span> {frame.action_description}
                            </p>
                            {frame.dialogue && (
                                <p className="text-xs text-text-secondary italic">
                                    <span className="font-bold text-text-muted not-italic">{ts("dialogue")}:</span> &quot;{frame.dialogue}&quot;
                                </p>
                            )}
                        </div>

                        <div className="p-4 border-b border-border-subtle">
                            <label
                                htmlFor={`frame-type-${frame.id}`}
                                className="mb-2 flex items-center gap-2 font-bold text-sm uppercase tracking-wider text-text-secondary"
                            >
                                <Video size={15} className="text-primary" />
                                {ts("frameType")}
                            </label>
                            <div className="relative">
                                <select
                                    id={`frame-type-${frame.id}`}
                                    value={frameType}
                                    onChange={(event) => {
                                        void handleFrameTypeChange(event.target.value as FrameMovementType);
                                    }}
                                    disabled={isSavingFrameType}
                                    className="w-full appearance-none rounded-lg border border-glass-border bg-surface px-4 py-3 pr-10 text-sm font-medium text-foreground outline-none transition-colors hover:border-primary/50 focus:border-primary disabled:cursor-wait disabled:opacity-70"
                                >
                                    <option value="" disabled>{ts("selectFrameType")}</option>
                                    {FRAME_MOVEMENT_TYPES.map((movementType) => (
                                        <option key={movementType} value={movementType}>
                                            {ts(`frameTypes.${movementType}`)}
                                        </option>
                                    ))}
                                </select>
                                {isSavingFrameType ? (
                                    <Loader2
                                        size={16}
                                        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-primary"
                                    />
                                ) : (
                                    <ChevronDown
                                        size={16}
                                        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-muted"
                                    />
                                )}
                            </div>
                            <p className={`mt-2 text-xs ${frameTypeError ? "text-red-400" : "text-text-muted"}`} aria-live="polite">
                                {frameTypeError || (isSavingFrameType ? ts("savingFrameType") : ts("frameTypeHint"))}
                            </p>
                        </div>

                        <div className="flex-1 p-4 flex flex-col">
                            <h3 className="font-bold text-sm uppercase tracking-wider text-text-secondary mb-2">
                                {ts("generationPrompt")}
                            </h3>
                            <textarea
                                value={prompt}
                                onChange={(e) => setPrompt(e.target.value)}
                                className="flex-1 w-full bg-surface border border-glass-border rounded-lg p-4 text-sm text-text-secondary resize-none focus:outline-none focus:border-primary/50 font-mono leading-relaxed"
                                placeholder={ts("promptPlaceholder")}
                            />
                            <p className="text-xs text-text-muted mt-2">
                                {ts("promptHint")}
                            </p>
                        </div>
                    </div>
                </div>
            </motion.div>
        </div>
    );
}
