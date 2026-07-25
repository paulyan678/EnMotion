"use client";

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Settings, X, Image, Video, MessageSquare, Check, Layout, User, Building, Box } from 'lucide-react';
import { useProjectStore, IMAGE_MODELS, I2V_MODELS, ASPECT_RATIOS } from '@/store/projectStore';
import { CHAT_MODELS, resolveModelSettings } from '@/lib/modelCatalog';
import { api } from '@/lib/api';
import { useTranslations } from "next-intl";
import GroupedModelGrid from '@/components/common/GroupedModelGrid';
import ModalPortal from '@/components/common/ModalPortal';

interface ModelSettingsModalProps {
    isOpen: boolean;
    onClose: () => void;
}

export default function ModelSettingsModal({ isOpen, onClose }: ModelSettingsModalProps) {
    const currentProject = useProjectStore((state) => state.currentProject);
    const t = useTranslations("models");
    const tc = useTranslations("common");
    const updateProject = useProjectStore((state) => state.updateProject);
    const resolvedSettings = resolveModelSettings(currentProject?.model_settings, 'project_settings');

    const [chatModel, setChatModel] = useState(resolvedSettings.chat_model);
    const [imageModel, setImageModel] = useState(resolvedSettings.image_model);
    const [i2vModel, setI2vModel] = useState(resolvedSettings.i2v_model);
    const [characterAspectRatio, setCharacterAspectRatio] = useState(resolvedSettings.character_aspect_ratio);
    const [sceneAspectRatio, setSceneAspectRatio] = useState(resolvedSettings.scene_aspect_ratio);
    const [propAspectRatio, setPropAspectRatio] = useState(resolvedSettings.prop_aspect_ratio);
    const [storyboardAspectRatio, setStoryboardAspectRatio] = useState(resolvedSettings.storyboard_aspect_ratio);
    const [isSaving, setIsSaving] = useState(false);
    const [syncedSettings, setSyncedSettings] = useState(currentProject?.model_settings);
    const [wasOpen, setWasOpen] = useState(isOpen);

    // Reset the draft before rendering when the backing settings object is
    // replaced or the modal is reopened after a cancelled edit. Stable open
    // settings leave in-progress user edits alone.
    const reopened = isOpen && !wasOpen;
    if (isOpen !== wasOpen) setWasOpen(isOpen);
    if (currentProject?.model_settings !== syncedSettings || reopened) {
        setSyncedSettings(currentProject?.model_settings);
        const normalizedSettings = resolveModelSettings(currentProject?.model_settings, 'project_settings');
        setChatModel(normalizedSettings.chat_model);
        setImageModel(normalizedSettings.image_model);
        setI2vModel(normalizedSettings.i2v_model);
        setCharacterAspectRatio(normalizedSettings.character_aspect_ratio);
        setSceneAspectRatio(normalizedSettings.scene_aspect_ratio);
        setPropAspectRatio(normalizedSettings.prop_aspect_ratio);
        setStoryboardAspectRatio(normalizedSettings.storyboard_aspect_ratio);
    }

    const handleSave = async () => {
        if (!currentProject) return;
        setIsSaving(true);
        try {
            const updated = await api.updateModelSettings(currentProject.id, {
                chat_model: chatModel,
                image_model: imageModel,
                t2i_model: imageModel,
                i2i_model: imageModel,
                video_model: i2vModel,
                i2v_model: i2vModel,
                character_aspect_ratio: characterAspectRatio,
                scene_aspect_ratio: sceneAspectRatio,
                prop_aspect_ratio: propAspectRatio,
                storyboard_aspect_ratio: storyboardAspectRatio,
            });
            updateProject(currentProject.id, updated);
            onClose();
        } catch (error) {
            console.error("Failed to save model settings:", error);
            alert(t("saveSettingsFailed"));
        } finally {
            setIsSaving(false);
        }
    };

    if (!isOpen) return null;

    return (
        <ModalPortal isOpen={isOpen} onClose={onClose}>
            {(dialogRef) => (
                <AnimatePresence>
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-overlay p-3 backdrop-blur-sm sm:p-4"
                        onClick={onClose}
                    >
                        <motion.div
                            ref={dialogRef}
                            role="dialog"
                            aria-modal="true"
                            aria-labelledby="model-settings-dialog-title"
                            tabIndex={-1}
                            initial={{ opacity: 0, scale: 0.95 }}
                            animate={{ opacity: 1, scale: 1 }}
                            exit={{ opacity: 0, scale: 0.95 }}
                            className="flex max-h-[calc(100dvh-1.5rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-glass-border bg-elevated sm:max-h-[calc(100dvh-2rem)]"
                            onClick={(e) => e.stopPropagation()}
                        >
                    {/* Header */}
                    <div className="flex shrink-0 items-start justify-between gap-4 border-b border-glass-border p-4 sm:items-center sm:p-5">
                        <div className="flex min-w-0 items-center gap-3">
                            <div className="p-2 bg-gradient-to-br from-blue-500/20 to-purple-500/20 rounded-lg">
                                <Settings size={20} className="text-blue-400" />
                            </div>
                            <div className="min-w-0">
                                <h2 id="model-settings-dialog-title" className="text-lg font-bold text-foreground">{t("genSettings")}</h2>
                                <p className="text-xs text-text-muted">{t("genSettingsDesc")}</p>
                            </div>
                        </div>
                        <button
                            type="button"
                            onClick={onClose}
                            aria-label={tc("close")}
                            className="grid h-10 w-10 shrink-0 place-items-center rounded-lg transition-colors hover:bg-hover-bg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                        >
                            <X size={20} className="text-text-secondary" />
                        </button>
                    </div>

                    {/* Content */}
                    <div className="space-y-6 overflow-y-auto p-4 sm:p-5">
                        <div className="space-y-3">
                            <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                                <MessageSquare size={16} className="text-primary" />
                                <span>{t("chatModel")}</span>
                            </div>
                            <GroupedModelGrid models={CHAT_MODELS} selectedId={chatModel} onSelect={setChatModel} />
                        </div>

                        <div className="border-t border-glass-border" />

                        {/* Assets Section */}
                        <div className="space-y-5">
                            <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                                <Image size={16} className="text-green-400" />
                                <span>{t("assetsT2I")}</span>
                            </div>

                            {/* T2I Model */}
                            <div className="space-y-2">
                                <label className="text-xs text-text-secondary">{t("model")}</label>
                                <GroupedModelGrid
                                    models={IMAGE_MODELS}
                                    selectedId={imageModel}
                                    onSelect={setImageModel}
                                />
                            </div>

                            {/* Asset Aspect Ratios */}
                            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                                {/* Character Aspect Ratio */}
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1 text-xs text-text-secondary">
                                        <User size={12} />
                                        <label>{t("character")}</label>
                                    </div>
                                    <div className="space-y-1">
                                        {ASPECT_RATIOS.map((ratio) => (
                                            <button
                                                key={ratio.id}
                                                type="button"
                                                aria-pressed={characterAspectRatio === ratio.id}
                                                onClick={() => setCharacterAspectRatio(ratio.id)}
                                                className={`flex min-h-10 w-full flex-col items-center rounded border px-2 py-1.5 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 ${characterAspectRatio === ratio.id
                                                        ? 'border-green-500/50 bg-green-500/10'
                                                        : 'border-glass-border hover:border-glass-border bg-glass'
                                                    }`}
                                            >
                                                <span className="text-xs font-medium text-foreground">{ratio.name}</span>
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Scene Aspect Ratio */}
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1 text-xs text-text-secondary">
                                        <Building size={12} />
                                        <label>{t("scene")}</label>
                                    </div>
                                    <div className="space-y-1">
                                        {ASPECT_RATIOS.map((ratio) => (
                                            <button
                                                key={ratio.id}
                                                type="button"
                                                aria-pressed={sceneAspectRatio === ratio.id}
                                                onClick={() => setSceneAspectRatio(ratio.id)}
                                                className={`flex min-h-10 w-full flex-col items-center rounded border px-2 py-1.5 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 ${sceneAspectRatio === ratio.id
                                                        ? 'border-green-500/50 bg-green-500/10'
                                                        : 'border-glass-border hover:border-glass-border bg-glass'
                                                    }`}
                                            >
                                                <span className="text-xs font-medium text-foreground">{ratio.name}</span>
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Prop Aspect Ratio */}
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1 text-xs text-text-secondary">
                                        <Box size={12} />
                                        <label>{t("prop")}</label>
                                    </div>
                                    <div className="space-y-1">
                                        {ASPECT_RATIOS.map((ratio) => (
                                            <button
                                                key={ratio.id}
                                                type="button"
                                                aria-pressed={propAspectRatio === ratio.id}
                                                onClick={() => setPropAspectRatio(ratio.id)}
                                                className={`flex min-h-10 w-full flex-col items-center rounded border px-2 py-1.5 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 ${propAspectRatio === ratio.id
                                                        ? 'border-green-500/50 bg-green-500/10'
                                                        : 'border-glass-border hover:border-glass-border bg-glass'
                                                    }`}
                                            >
                                                <span className="text-xs font-medium text-foreground">{ratio.name}</span>
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="border-t border-glass-border" />

                        {/* Storyboard Section */}
                        <div className="space-y-4">
                            <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                                <Layout size={16} className="text-blue-400" />
                                <span>{t("storyboardI2I")}</span>
                            </div>

                            {/* Storyboard Aspect Ratio */}
                            <div className="space-y-2">
                                <label className="text-xs text-text-secondary">{t("aspectRatio")}</label>
                                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                                    {ASPECT_RATIOS.map((ratio) => (
                                        <button
                                            key={ratio.id}
                                            type="button"
                                            aria-pressed={storyboardAspectRatio === ratio.id}
                                            onClick={() => setStoryboardAspectRatio(ratio.id)}
                                            className={`flex min-h-11 flex-col items-center rounded-lg border p-3 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 ${storyboardAspectRatio === ratio.id
                                                    ? 'border-blue-500/50 bg-blue-500/10'
                                                    : 'border-glass-border hover:border-glass-border bg-glass'
                                                }`}
                                        >
                                            <span className="text-sm font-medium text-foreground">{ratio.name}</span>
                                            <span className="text-[0.625rem] text-text-muted">{ratio.description}</span>
                                        </button>
                                    ))}
                                </div>
                            </div>
                        </div>

                        <div className="border-t border-glass-border" />

                        {/* Motion Section */}
                        <div className="space-y-4">
                            <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                                <Video size={16} className="text-purple-400" />
                                <span>{t("motionI2V")}</span>
                            </div>
                            <p className="text-xs text-text-muted">{t("motionFollowsAR")}</p>

                            {/* I2V Model */}
                            <div className="space-y-2">
                                <label className="text-xs text-text-secondary">{t("model")}</label>
                                <GroupedModelGrid
                                    models={I2V_MODELS}
                                    selectedId={i2vModel}
                                    onSelect={(id) => setI2vModel(id)}
                                />
                            </div>
                        </div>

                    </div>

                    {/* Footer */}
                    <div className="flex shrink-0 flex-col-reverse gap-3 border-t border-glass-border bg-surface p-4 sm:flex-row sm:justify-end sm:p-5">
                        <button
                            type="button"
                            onClick={onClose}
                            className="min-h-11 rounded-lg px-4 py-2 text-sm text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                        >
                            {tc("cancel")}
                        </button>
                        <button
                            type="button"
                            onClick={handleSave}
                            disabled={isSaving}
                            className="flex min-h-11 items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-on-accent transition-colors hover:bg-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:opacity-50"
                        >
                            {isSaving ? (
                                <>
                                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />
                                    {t("saving")}
                                </>
                            ) : (
                                <>
                                    <Check size={16} />
                                    {t("saveSettings")}
                                </>
                            )}
                        </button>
                    </div>
                        </motion.div>
                    </motion.div>
                </AnimatePresence>
            )}
        </ModalPortal>
    );
}
