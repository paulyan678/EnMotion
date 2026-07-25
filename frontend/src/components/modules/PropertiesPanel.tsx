"use client";

import { motion } from "framer-motion";
import { useLocale, useTranslations } from "next-intl";
import { Settings, Sliders, Image as ImageIcon, Type, FileText, Users, Layout, Video, Music, Film, Info, Paintbrush, Wand2, Sparkles } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { getMaxReferenceImages } from "@/lib/modelCatalog";
import { primaryAssetDisplayUrl } from "@/lib/assetImage";

interface PropertiesPanelProps {
    activeStep: string;
}

export default function PropertiesPanel({ activeStep }: PropertiesPanelProps) {
    const tp = useTranslations("properties");
    const currentProject = useProjectStore((state) => state.currentProject);

    // Hide panel for Motion step as it has its own sidebar
    if (activeStep === "motion" || activeStep === "assembly") return null;

    const renderContent = () => {
        switch (activeStep) {
            case "script":
                return <ScriptInspector project={currentProject} />;
            case "assets":
                return <AssetsInspector project={currentProject} />;
            case "storyboard":
                return <StoryboardInspector />;
            case "motion":
                return <MotionInspector />;
            case "mix":
                return <MixInspector />;
            case "export":
                return <ExportInspector />;
            default:
                return <div className="p-4 text-text-muted">{tp("selectStep")}</div>;
        }
    };

    const activeStepLabels: Record<string, string> = {
        script: tp("stepScript"),
        assets: tp("stepAssets"),
        storyboard: tp("stepStoryboard"),
        motion: tp("stepMotion"),
        mix: tp("stepMix"),
        export: tp("stepExport"),
    };

    return (
        <motion.aside
            initial={{ x: 100, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            className="w-64 h-full border-l border-glass-border bg-surface flex flex-col z-50"
        >
            <div className="p-4 border-b border-glass-border flex items-center justify-between">
                <h2 className="font-display font-bold text-foreground flex items-center gap-2">
                    <Info size={16} className="text-primary" /> {tp("context")}
                </h2>
                <span className="text-xs font-mono text-text-muted uppercase">{activeStepLabels[activeStep] || tp("context")}</span>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-6">
                {renderContent()}
            </div>
        </motion.aside>
    );
}

// --- Sub-Inspectors ---

function ScriptInspector({ project }: { project: any }) {
    const tp = useTranslations("properties");
    if (!project) return null;
    const wordCount = project.originalText?.length || 0;
    const charCount = project.characters?.length || 0;
    const sceneCount = project.scenes?.length || 0;

    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-foreground flex items-center gap-2">
                    <FileText size={14} /> {tp("projectStats")}
                </h3>
                <div className="grid grid-cols-2 gap-2">
                    <StatBox label={tp("words")} value={wordCount} />
                    <StatBox label={tp("chars")} value={charCount} />
                    <StatBox label={tp("scenes")} value={sceneCount} />
                    <StatBox label={tp("estDur")} value={tp("estimatedDurationValue")} />
                </div>
            </div>

            <div className="pt-4 border-t border-glass-border">
                <ArtDirectionStyleDisplay project={project} />
            </div>
        </div>
    );
}

function AssetsInspector({ project }: { project: any }) {
    const tp = useTranslations("properties");
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    // Get art direction style from Step 2
    const artDirectionStyle = currentProject?.art_direction?.style_config;

    // Get aspect ratios from model settings
    const characterAspectRatio = currentProject?.model_settings?.character_aspect_ratio || '9:16';
    const sceneAspectRatio = currentProject?.model_settings?.scene_aspect_ratio || '16:9';
    const propAspectRatio = currentProject?.model_settings?.prop_aspect_ratio || '1:1';

    const handleUpdateAspectRatio = async (type: 'character' | 'scene' | 'prop', ratio: string) => {
        if (!currentProject) return;

        try {
            const updatePayload: any = {};
            if (type === 'character') updatePayload.character_aspect_ratio = ratio;
            else if (type === 'scene') updatePayload.scene_aspect_ratio = ratio;
            else if (type === 'prop') updatePayload.prop_aspect_ratio = ratio;

            const updated = await api.updateModelSettings(currentProject.id, {
                character_aspect_ratio: type === 'character' ? ratio : undefined,
                scene_aspect_ratio: type === 'scene' ? ratio : undefined,
                prop_aspect_ratio: type === 'prop' ? ratio : undefined,
            });
            updateProject(currentProject.id, updated);
        } catch (error) {
            console.error('Failed to update aspect ratio:', error);
        }
    };

    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-foreground flex items-center gap-2">
                    <Users size={14} /> {tp("assetOverview")}
                </h3>
                <div className="text-xs text-text-secondary">
                    {tp("assetOverviewDesc")}
                </div>
            </div>

            {/* Aspect Ratio Controls */}
            <div className="space-y-4 pt-4 border-t border-glass-border">
                <div className="flex items-center gap-2 mb-2">
                    <Layout className="text-primary" size={14} />
                    <h3 className="font-bold text-foreground text-xs">{tp("aspectRatios")}</h3>
                </div>

                {/* Character Aspect Ratio */}
                <div className="space-y-2">
                    <label className="text-[0.625rem] font-bold text-text-muted uppercase">{tp("characterLabel")}</label>
                    <div className="grid grid-cols-5 gap-1">
                        {['9:16', '3:4', '1:1', '4:3', '16:9'].map((ratio) => (
                            <button
                                key={ratio}
                                onClick={() => handleUpdateAspectRatio('character', ratio)}
                                className={`px-2 py-1.5 rounded text-[0.625rem] border transition-all font-medium ${characterAspectRatio === ratio
                                    ? 'bg-primary/20 text-primary border-primary/30'
                                    : 'bg-glass text-text-secondary border-glass-border hover:bg-hover-bg'
                                    }`}
                            >
                                {ratio}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Scene Aspect Ratio */}
                <div className="space-y-2">
                    <label className="text-[0.625rem] font-bold text-text-muted uppercase">{tp("sceneLabel")}</label>
                    <div className="grid grid-cols-5 gap-1">
                        {['9:16', '3:4', '1:1', '4:3', '16:9'].map((ratio) => (
                            <button
                                key={ratio}
                                onClick={() => handleUpdateAspectRatio('scene', ratio)}
                                className={`px-2 py-1.5 rounded text-[0.625rem] border transition-all font-medium ${sceneAspectRatio === ratio
                                    ? 'bg-primary/20 text-primary border-primary/30'
                                    : 'bg-glass text-text-secondary border-glass-border hover:bg-hover-bg'
                                    }`}
                            >
                                {ratio}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Prop Aspect Ratio */}
                <div className="space-y-2">
                    <label className="text-[0.625rem] font-bold text-text-muted uppercase">{tp("propLabel")}</label>
                    <div className="grid grid-cols-5 gap-1">
                        {['9:16', '3:4', '1:1', '4:3', '16:9'].map((ratio) => (
                            <button
                                key={ratio}
                                onClick={() => handleUpdateAspectRatio('prop', ratio)}
                                className={`px-2 py-1.5 rounded text-[0.625rem] border transition-all font-medium ${propAspectRatio === ratio
                                    ? 'bg-primary/20 text-primary border-primary/30'
                                    : 'bg-glass text-text-secondary border-glass-border hover:bg-hover-bg'
                                    }`}
                            >
                                {ratio}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            {/* Art Direction Style Display (Read-only) */}
            <div className="pt-4 border-t border-glass-border">
                <ArtDirectionStyleDisplay project={currentProject} />
            </div>
        </div>
    );
}

function ArtDirectionStyleDisplay({ project }: { project: any }) {
    const tp = useTranslations("properties");
    const artDirectionStyle = project?.art_direction?.style_config;

    return (
        <div className="space-y-4">
            <div className="flex items-center gap-2 mb-2">
                <Paintbrush className="text-primary" size={14} />
                <h3 className="font-bold text-foreground text-xs">{tp("artDirectionStyle")}</h3>
            </div>

            {artDirectionStyle ? (
                <div className="space-y-3">
                    <div>
                        <label className="text-[0.625rem] font-bold text-text-muted uppercase mb-1.5 block">{tp("styleName")}</label>
                        <div className="text-xs font-bold text-foreground bg-gradient-to-r from-blue-500/20 to-purple-500/20 p-2.5 rounded-lg border border-glass-border">
                            {artDirectionStyle.name}
                        </div>
                    </div>

                    <div>
                        <label className="text-[0.625rem] font-bold text-text-muted uppercase mb-1.5 block">{tp("positivePrompt")}</label>
                        <div className="bg-surface border border-border-subtle rounded-lg p-2.5 text-[0.625rem] text-text-secondary leading-relaxed max-h-20 overflow-y-auto">
                            {artDirectionStyle.positive_prompt || tp('noPositivePrompt')}
                        </div>
                    </div>

                    {artDirectionStyle.negative_prompt && (
                        <div>
                            <label className="text-[0.625rem] font-bold text-text-muted uppercase mb-1.5 block">{tp("negativePromptLabel")}</label>
                            <div className="bg-surface border border-border-subtle rounded-lg p-2.5 text-[0.625rem] text-text-secondary leading-relaxed max-h-16 overflow-y-auto">
                                {artDirectionStyle.negative_prompt}
                            </div>
                        </div>
                    )}

                    <div className="pt-2">
                        <p className="text-[0.5625rem] text-text-muted leading-relaxed">
                            {tp("tipEditStyle")}
                        </p>
                    </div>
                </div>
            ) : (
                <div className="bg-glass border border-glass-border rounded-lg p-3 text-center">
                    <p className="text-xs text-text-muted mb-2">{tp("noStyleConfigured")}</p>
                    <p className="text-[0.5625rem] text-text-muted">
                        {tp("goToArtDirection")}
                    </p>
                </div>
            )}
        </div>
    );
}

function StoryboardInspector() {
    const tp = useTranslations("properties");
    const locale = useLocale();
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const selectedFrameId = useProjectStore((state) => state.selectedFrameId);

    // State for bilingual polish results. Keep hooks above the project guard so
    // loading or clearing a project never changes this component's hook order.
    const [polishedPrompts, setPolishedPrompts] = useState<Record<string, { cn: string; en: string }>>({});
    const [isPolishing, setIsPolishing] = useState(false);
    const [feedbackText, setFeedbackText] = useState("");

    if (!currentProject) return null;

    const selectedFrame = currentProject?.frames?.find((f: any) => f.id === selectedFrameId);

    const updateFrame = async (data: any) => {
        if (!currentProject || !selectedFrame) return;

        // Optimistically update local state first
        const updatedFrames = currentProject.frames.map((f: any) =>
            f.id === selectedFrameId ? { ...f, ...data } : f
        );
        updateProject(currentProject.id, { frames: updatedFrames });

        // Sync to backend (fire and forget for speed, but log errors)
        try {
            await api.updateFrame(currentProject.id, selectedFrame.id, data);
        } catch (error) {
            console.error("Failed to sync frame to backend:", error);
            // Note: We don't revert optimistic update to keep UI responsive
        }
    };

    const handleComposePrompt = () => {
        if (!selectedFrame || !currentProject) return;

        const scene = currentProject.scenes?.find((s: any) => s.id === selectedFrame.scene_id);
        const characters = currentProject.characters?.filter((c: any) => selectedFrame.character_ids?.includes(c.id));

        // Construct prompt based on User Guide: Motion + Camera (+ Context)
        const promptParts = [];

        // 1. Motion / Action (Subject + Action)
        let motionPart = "";
        if (characters && characters.length > 0) {
            const charDescriptions = characters.map((c: any) => {
                let desc = `${c.name}${locale === "zh" ? "（" : " ("}${c.description}`;
                if (c.clothing) desc += locale === "zh" ? `，服装：${c.clothing}` : `, wearing ${c.clothing}`;
                desc += locale === "zh" ? `）` : `)`;
                return desc;
            }).join(locale === "zh" ? "，" : ", ");
            motionPart += locale === "zh"
                ? `角色：${charDescriptions}。`
                : `Characters: ${charDescriptions}. `;
        }
        motionPart += `${selectedFrame.action_description || ""}`;
        if (selectedFrame.facial_expression) motionPart += `, ${selectedFrame.facial_expression}`;
        if (motionPart.trim()) promptParts.push(motionPart.trim());

        // 2. Camera (Movement + Angle)
        let cameraPart = "";
        if (selectedFrame.camera_angle) cameraPart += `${selectedFrame.camera_angle}`;
        if (selectedFrame.camera_movement) {
            if (cameraPart) cameraPart += ", ";
            cameraPart += `${selectedFrame.camera_movement}`;
        }
        if (selectedFrame.composition) {
            if (cameraPart) cameraPart += ", ";
            cameraPart += `${selectedFrame.composition}`;
        }
        if (cameraPart.trim()) promptParts.push(cameraPart.trim());

        // 3. Scene / Context (Environment + Atmosphere)
        let scenePart = "";
        if (scene) {
            scenePart += `${scene.description || scene.name}`;
            if (scene.time_of_day) scenePart += `, ${scene.time_of_day}`;
            if (scene.lighting_mood) scenePart += `, ${scene.lighting_mood}`;
        }
        if (selectedFrame.atmosphere) {
            if (scenePart) scenePart += ", ";
            scenePart += `${selectedFrame.atmosphere}`;
        }
        if (scenePart.trim()) promptParts.push(scenePart.trim());

        // Join with periods for clear separation
        const finalPrompt = promptParts.join(" . ");
        updateFrame({ image_prompt: finalPrompt });
    };

    const toggleCharacter = (charId: string) => {
        const currentIds = selectedFrame.character_ids || [];
        const newIds = currentIds.includes(charId)
            ? currentIds.filter((id: string) => id !== charId)
            : [...currentIds, charId];
        updateFrame({ character_ids: newIds });
    };

    const polishedPrompt = selectedFrame ? polishedPrompts[selectedFrame.id] : null;

    const handlePolish = async (feedback: string = "") => {
        if (!selectedFrame || !currentProject) return;
        setIsPolishing(true);

        // Construct assets list for context
        const assets = [];
        if (selectedFrame.scene_id) {
            const scene = currentProject.scenes?.find((s: any) => s.id === selectedFrame.scene_id);
            if (scene) assets.push({ type: 'Scene', name: scene.name, description: scene.description });
        }
        if (selectedFrame.character_ids) {
            selectedFrame.character_ids.forEach((cid: string) => {
                const char = currentProject.characters?.find((c: any) => c.id === cid);
                if (char) assets.push({ type: 'Character', name: char.name, description: char.description });
            });
        }
        if (selectedFrame.prop_ids) {
            selectedFrame.prop_ids.forEach((pid: string) => {
                const prop = currentProject.props?.find((p: any) => p.id === pid);
                if (prop) assets.push({ type: 'Prop', name: prop.name, description: prop.description });
            });
        }

        // Use current polished result as draft when refining with feedback
        const draft = feedback
            ? (polishedPrompt?.en || selectedFrame.image_prompt || selectedFrame.action_description)
            : (selectedFrame.image_prompt || selectedFrame.action_description);

        try {
            // Use new bilingual refine API
            const res = await api.refineFramePrompt(currentProject.id, selectedFrame.id, draft, assets, feedback);
            if (res.prompt_cn && res.prompt_en) {
                setPolishedPrompts(prev => ({
                    ...prev,
                    [selectedFrame.id]: { cn: res.prompt_cn, en: res.prompt_en }
                }));
                setFeedbackText("");
            }
        } catch (err) {
            console.error("Polish failed", err);
            alert(tp("promptPolishFailed"));
        } finally {
            setIsPolishing(false);
        }
    };

    if (!selectedFrame) {
        return (
            <div className="space-y-6">
                <div className="p-4 bg-glass rounded-lg border border-glass-border text-center text-text-muted text-xs">
                    {tp("selectFrameToEdit")}
                </div>
                <p className="text-xs text-text-muted text-center">
                    {tp("tipConfigureAR")}
                </p>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-foreground flex items-center gap-2">
                    <Layout size={14} /> {tp("frameDetails")}
                </h3>
                <div className="text-xs text-text-secondary">
                    {tp("editingFrame", { index: currentProject?.frames?.findIndex((f: any) => f.id === selectedFrameId) + 1 })}
                </div>
            </div>

            {/* Action Description */}
            <div className="space-y-2">
                <label className="text-xs font-bold text-text-muted uppercase">{tp("actionDesc")}</label>
                <textarea
                    className="w-full h-24 bg-input-bg border border-glass-border rounded-lg p-3 text-xs text-text-secondary resize-none focus:outline-none focus:border-primary/50"
                    value={selectedFrame.action_description || ""}
                    onChange={(e) => updateFrame({ action_description: e.target.value })}
                    placeholder={tp("actionPlaceholder")}
                />
            </div>

            {/* Dialogue */}
            <div className="space-y-2">
                <label className="text-xs font-bold text-text-muted uppercase">{tp("dialogueContent")}</label>
                <textarea
                    className="w-full h-16 bg-input-bg border border-glass-border rounded-lg p-3 text-xs text-text-secondary resize-none focus:outline-none focus:border-primary/50"
                    value={selectedFrame.dialogue || ""}
                    onChange={(e) => updateFrame({ dialogue: e.target.value })}
                    placeholder={tp("dialoguePlaceholder")}
                />
            </div>

            {/* Reference Assets */}
            <div className="space-y-2">
                {(() => {
                    // Calculate current reference count
                    const selectedScene = currentProject?.scenes?.find((s: any) => s.id === selectedFrame.scene_id);
                    const sceneHasImage = selectedScene ? primaryAssetDisplayUrl(selectedScene, "scene") : "";

                    const selectedChars = currentProject?.characters?.filter((c: any) => selectedFrame.character_ids?.includes(c.id));
                    const charImageCount = selectedChars?.filter((c: any) => primaryAssetDisplayUrl(c, "character")).length || 0;

                    const selectedProps = currentProject?.props?.filter((p: any) => selectedFrame.prop_ids?.includes(p.id));
                    const propImageCount = selectedProps?.filter((p: any) => primaryAssetDisplayUrl(p, "prop")).length || 0;

                    const referenceCount = (sceneHasImage ? 1 : 0) + charImageCount + propImageCount;

                    const referenceLimit = getMaxReferenceImages(
                        currentProject?.model_settings?.i2i_model
                    );
                    const isLimitReached = referenceCount >= referenceLimit;

                    return (
                        <>
                            <div className="flex justify-between items-center">
                                <label className="text-xs font-bold text-text-muted uppercase">{tp("characters")}</label>
                                <span className={`text-[0.625rem] ${isLimitReached ? "text-yellow-500 font-bold" : "text-text-muted"}`}>
                                    {referenceCount}/{referenceLimit} {tp("imagesUnit")}
                                </span>
                            </div>

                            {/* Scene Selector */}
                            <div className="mb-2 space-y-2">
                                <label className="text-[0.625rem] font-bold text-text-muted uppercase">{tp("sceneLabel")}</label>
                                <select
                                    className="w-full bg-input-bg border border-glass-border rounded p-2 text-xs text-text-secondary focus:outline-none"
                                    value={selectedFrame.scene_id || ""}
                                    onChange={(e) => {
                                        // Check if selecting this scene would exceed limit
                                        // Actually, replacing a scene is always fine unless we treat scene as optional toggle.
                                        // Here it's a dropdown, so we always have 0 or 1 scene.
                                        // If we switch to a scene with image from one without, we might exceed limit.
                                        const newSceneId = e.target.value;
                                        const newScene = currentProject?.scenes?.find((s: any) => s.id === newSceneId);
                                        const newSceneHasImage = newScene ? primaryAssetDisplayUrl(newScene, "scene") : "";

                                        // Predicted count: (newScene ? 1 : 0) + charCount + propCount
                                        // If current scene had image, we lose 1, gain 1 (net 0).
                                        // If current didn't, we gain 1.
                                        const predictedCount = (newSceneHasImage ? 1 : 0) + charImageCount + propImageCount;

                                        if (predictedCount > referenceLimit) {
                                            alert(tp("referenceLimitExceeded", { limit: referenceLimit }));
                                            return;
                                        }
                                        updateFrame({ scene_id: newSceneId });
                                    }}
                                >
                                    <option value="">{tp("selectScene")}</option>
                                    {currentProject?.scenes?.map((scene: any) => (
                                        <option key={scene.id} value={scene.id}>{scene.name}</option>
                                    ))}
                                </select>

                                {/* Show Scene Description if selected */}
                                {selectedScene?.description && (
                                    <div className="bg-glass p-2 rounded text-[0.625rem] text-text-secondary italic border border-border-subtle">
                                        <span className="font-bold not-italic text-text-muted">{tp("scenePrefix")} </span>
                                        {selectedScene.description}
                                    </div>
                                )}
                            </div>

                            {/* Character Toggles */}
                            <div className="space-y-2">
                                <label className="text-[0.625rem] font-bold text-text-muted uppercase">{tp("characters")}</label>
                                <div className="grid grid-cols-2 gap-2">
                                    {currentProject?.characters?.map((char: any) => {
                                        const isSelected = selectedFrame.character_ids?.includes(char.id);
                                        const characterImageUrl = primaryAssetDisplayUrl(char, "character");
                                        const hasImage = !!characterImageUrl;
                                        // Disable if not selected, has image, and limit reached
                                        const isDisabled = !isSelected && hasImage && isLimitReached;

                                        return (
                                            <button
                                                key={char.id}
                                                disabled={isDisabled}
                                                onClick={() => {
                                                    if (isDisabled) return;
                                                    toggleCharacter(char.id);
                                                }}
                                                className={`flex items-center gap-2 p-2 rounded border text-xs transition-all ${isSelected
                                                    ? "bg-primary/20 border-primary text-foreground"
                                                    : isDisabled
                                                        ? "bg-surface border-border-subtle text-text-muted cursor-not-allowed opacity-50"
                                                        : "bg-surface border-glass-border text-text-secondary hover:bg-glass"
                                                    }`}
                                            >
                                                <div className="w-4 h-4 rounded-full bg-elevated overflow-hidden">
                                                    {characterImageUrl && <img src={characterImageUrl} alt="" className="w-full h-full object-cover" />}
                                                </div>
                                                <span className="truncate">{char.name}</span>
                                            </button>
                                        );
                                    })}
                                </div>

                                {/* Show Selected Characters Descriptions */}
                                {selectedChars && selectedChars.length > 0 && (
                                    <div className="space-y-1">
                                        {selectedChars.map((char: any) => (
                                            <div key={char.id} className="bg-glass p-2 rounded text-[0.625rem] text-text-secondary italic border border-border-subtle">
                                                <span className="font-bold not-italic text-text-muted">{char.name}: </span>
                                                {char.description}
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            {/* Prop Toggles */}
                            {currentProject?.props && currentProject.props.length > 0 && (
                                <div className="space-y-2">
                                    <label className="text-[0.625rem] font-bold text-text-muted uppercase">{tp("propLabel")}</label>
                                    <div className="grid grid-cols-2 gap-2">
                                        {currentProject.props.map((prop: any) => {
                                            const isSelected = selectedFrame.prop_ids?.includes(prop.id);
                                            const propImageUrl = primaryAssetDisplayUrl(prop, "prop");
                                            const hasImage = !!propImageUrl;
                                            const isDisabled = !isSelected && hasImage && isLimitReached;

                                            return (
                                                <button
                                                    key={prop.id}
                                                    disabled={isDisabled}
                                                    onClick={() => {
                                                        if (isDisabled) return;
                                                        // Toggle Prop Logic
                                                        const currentProps = selectedFrame.prop_ids || [];
                                                        const newProps = currentProps.includes(prop.id)
                                                            ? currentProps.filter((id: string) => id !== prop.id)
                                                            : [...currentProps, prop.id];
                                                        updateFrame({ prop_ids: newProps });
                                                    }}
                                                    className={`flex items-center gap-2 p-2 rounded border text-xs transition-all ${isSelected
                                                        ? "bg-primary/20 border-primary text-foreground"
                                                        : isDisabled
                                                            ? "bg-surface border-border-subtle text-text-muted cursor-not-allowed opacity-50"
                                                            : "bg-surface border-glass-border text-text-secondary hover:bg-glass"
                                                        }`}
                                                >
                                                    <div className="w-4 h-4 rounded bg-elevated overflow-hidden flex-shrink-0">
                                                        {propImageUrl && <img src={propImageUrl} alt="" className="w-full h-full object-cover" />}
                                                    </div>

                                                    <span className="truncate">{prop.name}</span>
                                                </button>
                                            );
                                        })}
                                    </div>

                                    {/* Show Selected Props Descriptions */}
                                    {(() => {
                                        const selectedProps = currentProject.props.filter((p: any) => selectedFrame.prop_ids?.includes(p.id));
                                        if (selectedProps && selectedProps.length > 0) {
                                            return (
                                                <div className="space-y-1">
                                                    {selectedProps.map((prop: any) => (
                                                        <div key={prop.id} className="bg-glass p-2 rounded text-[0.625rem] text-text-secondary italic border border-border-subtle">
                                                            <span className="font-bold not-italic text-text-muted">{prop.name}: </span>
                                                            {prop.description}
                                                        </div>
                                                    ))}
                                                </div>
                                            );
                                        }
                                        return null;
                                    })()}
                                </div>
                            )}
                        </>
                    );
                })()}
            </div>

            {/* Camera Controls */}
            <div className="space-y-2">
                <label className="text-xs font-bold text-text-muted uppercase">{tp("sceneContext")}</label>
                <div className="grid grid-cols-1 gap-2">
                    <select
                        className="bg-input-bg border border-glass-border rounded p-2 text-xs text-text-secondary focus:outline-none"
                        value={selectedFrame.camera_angle || ""}
                        onChange={(e) => updateFrame({ camera_angle: e.target.value })}
                    >
                        <option value="">{tp("cameraAnglePlaceholder")}</option>
                        <option value="Wide Shot">{tp("wideShot")}</option>
                        <option value="Medium Shot">{tp("mediumShot")}</option>
                        <option value="Close Up">{tp("closeUp")}</option>
                        <option value="Low Angle">{tp("lowAngle")}</option>
                        <option value="High Angle">{tp("highAngle")}</option>
                        <option value="Over the Shoulder">{tp("overShoulder")}</option>
                    </select>
                </div>
            </div>

            {/* Prompt */}
            <div className="space-y-2">
                <div className="flex justify-between items-center">
                    <label className="text-xs font-bold text-text-muted uppercase">{tp("imagePrompt")}</label>
                    <button
                        onClick={handleComposePrompt}
                        className="flex items-center gap-1 text-[0.625rem] bg-glass hover:bg-hover-bg px-2 py-1 rounded text-foreground transition-colors"
                        title={tp("autoComposeTitle")}
                    >
                        <Wand2 size={10} /> {tp("autoCompose")}
                    </button>
                    <button
                        onClick={() => handlePolish()}
                        disabled={isPolishing}
                        className="flex items-center gap-1 text-[0.625rem] bg-purple-600 hover:bg-purple-700 px-2 py-1 rounded text-white transition-colors ml-2 disabled:opacity-50"
                        title={tp("polishPromptTitle")}
                    >
                        {isPolishing ? <Sparkles size={10} className="animate-spin" /> : <Sparkles size={10} />} {tp("polish")}
                    </button>
                </div>
                <textarea
                    className="w-full h-32 bg-input-bg border border-glass-border rounded-lg p-3 text-xs text-text-secondary resize-none focus:outline-none focus:border-primary/50"
                    value={selectedFrame.image_prompt || ""}
                    onChange={(e) => updateFrame({ image_prompt: e.target.value })}
                    placeholder={tp("fullPromptPlaceholder")}
                />

                {/* Polished Result Display - Bilingual */}
                {polishedPrompt && (
                    <motion.div
                        initial={{ opacity: 0, y: -10 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -10 }}
                        className="bg-purple-900/20 border border-purple-500/30 rounded-lg p-3 mt-2 space-y-3"
                    >
                        <div className="flex justify-between items-start">
                            <span className="text-xs font-bold text-purple-400 flex items-center gap-1">
                                <Wand2 size={12} /> {tp("bilingualPolish")}
                            </span>
                            <button
                                onClick={() => {
                                    setPolishedPrompts(prev => {
                                        const newState = { ...prev };
                                        delete newState[selectedFrame.id];
                                        return newState;
                                    });
                                    setFeedbackText("");
                                }}
                                className="text-[0.625rem] text-text-secondary hover:text-foreground"
                            >
                                ✕
                            </button>
                        </div>

                        {/* Chinese Prompt */}
                        <div className="space-y-1">
                            <div className="flex justify-between items-center">
                                <span className="text-[0.625rem] font-bold text-text-muted uppercase">{tp("chinesePreview")}</span>
                                <button
                                    onClick={() => {
                                        navigator.clipboard.writeText(polishedPrompt.cn);
                                        alert(tp("chinesePromptCopied"));
                                    }}
                                    className="text-[0.625rem] text-text-secondary hover:text-foreground bg-surface px-2 py-0.5 rounded"
                                >
                                    复制
                                </button>
                            </div>
                            <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap bg-surface p-2 rounded">
                                {polishedPrompt.cn}
                            </p>
                        </div>

                        {/* English Prompt */}
                        <div className="space-y-1">
                            <div className="flex justify-between items-center">
                                <span className="text-[0.625rem] font-bold text-text-muted uppercase">{tp("englishGeneration")}</span>
                                <div className="flex gap-1">
                                    <button
                                        onClick={() => {
                                            navigator.clipboard.writeText(polishedPrompt.en);
                                            alert(tp("englishPromptCopied"));
                                        }}
                                        className="text-[0.625rem] text-text-secondary hover:text-foreground bg-surface px-2 py-0.5 rounded"
                                    >
                                        {tp("copy")}
                                    </button>
                                    <button
                                        onClick={() => {
                                            updateFrame({
                                                image_prompt: polishedPrompt.en,
                                                image_prompt_cn: polishedPrompt.cn,
                                                image_prompt_en: polishedPrompt.en
                                            });
                                            setPolishedPrompts(prev => {
                                                const newState = { ...prev };
                                                delete newState[selectedFrame.id];
                                                return newState;
                                            });
                                        }}
                                        className="text-[0.625rem] text-white bg-purple-600 hover:bg-purple-500 px-2 py-0.5 rounded font-bold"
                                    >
                                        应用
                                    </button>
                                </div>
                            </div>
                            <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap bg-surface p-2 rounded font-mono">
                                {polishedPrompt.en}
                            </p>
                        </div>

                        {/* Feedback for iterative refinement */}
                        <div className="space-y-2 pt-2 border-t border-purple-500/20">
                            <div className="flex gap-2">
                                <input
                                    type="text"
                                    value={feedbackText}
                                    onChange={(e) => setFeedbackText(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter" && feedbackText.trim() && !isPolishing) {
                                            handlePolish(feedbackText.trim());
                                        }
                                    }}
                                    placeholder={tp("feedbackPlaceholder")}
                                    className="flex-1 text-[0.625rem] bg-input-bg border border-purple-500/20 rounded px-2 py-1.5 text-foreground placeholder-text-muted focus:outline-none focus:border-purple-500/50"
                                />
                                <button
                                    onClick={() => handlePolish(feedbackText.trim())}
                                    disabled={isPolishing || !feedbackText.trim()}
                                    className="text-[0.625rem] text-white bg-purple-600 hover:bg-purple-500 px-2 py-1.5 rounded font-medium flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                                >
                                    {isPolishing ? <Sparkles size={8} className="animate-spin" /> : <Sparkles size={8} />}
                                    再润色
                                </button>
                            </div>
                        </div>
                    </motion.div>
                )}
            </div>
        </div >
    );
}

function MotionInspector() {
    const tp = useTranslations("properties");
    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-foreground flex items-center gap-2">
                    <Video size={14} /> {tp("motionParams")}
                </h3>
                <div className="space-y-4">
                    <div className="space-y-1">
                        <div className="flex justify-between text-xs text-text-secondary">
                            <span>{tp("motionBucket")}</span>
                            <span>127</span>
                        </div>
                        <input type="range" className="w-full h-1 bg-glass rounded-lg appearance-none cursor-pointer" />
                    </div>
                    <div className="space-y-1">
                        <div className="flex justify-between text-xs text-text-secondary">
                            <span>{tp("frameRate")}</span>
                            <span>24</span>
                        </div>
                        <input type="range" className="w-full h-1 bg-glass rounded-lg appearance-none cursor-pointer" />
                    </div>
                </div>
            </div>
        </div>
    );
}

function MixInspector() {
    const tp = useTranslations("properties");
    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-foreground flex items-center gap-2">
                    <Music size={14} /> {tp("trackInspector")}
                </h3>
                <div className="p-4 bg-glass rounded-lg border border-glass-border text-center text-xs text-text-muted">
                    {tp("selectClipHint")}
                </div>
            </div>
        </div>
    );
}

function ExportInspector() {
    const tp = useTranslations("properties");
    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-foreground flex items-center gap-2">
                    <Film size={14} /> {tp("exportHistory")}
                </h3>
                <div className="space-y-2">
                    <div className="p-2 bg-glass rounded border border-glass-border flex justify-between items-center">
                        <span className="text-xs text-text-secondary">{tp("sampleExportName")}</span>
                        <span className="text-[0.625rem] text-text-muted">{tp("twoHoursAgo")}</span>
                    </div>
                </div>
            </div>
        </div>
    );
}

function StatBox({ label, value }: { label: string, value: string | number }) {
    return (
        <div className="bg-glass border border-glass-border rounded p-2 text-center">
            <div className="text-lg font-bold text-foreground">{value}</div>
            <div className="text-[0.625rem] text-text-muted uppercase">{label}</div>
        </div>
    );
}
