"use client";

import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Settings, X, Image, Video, Layout, Check, User, Building, Box, Loader2, MessageSquare } from 'lucide-react';
import { ASPECT_RATIOS } from '@/store/projectStore';
import {
    SERIES_IMAGE_MODELS,
    SERIES_I2V_MODELS,
    SERIES_CHAT_MODELS,
    resolveModelSettings,
} from '@/lib/modelCatalog';
import { api } from '@/lib/api';
import { useTranslations } from "next-intl";
import GroupedModelGrid from '@/components/common/GroupedModelGrid';
import StableAsyncButtonContent from '@/components/shared/StableAsyncButtonContent';
import ModalPortal from '@/components/common/ModalPortal';
import ModelSettingInheritanceButton from '@/components/common/ModelSettingInheritanceButton';
import {
    buildModelSettingsPatch,
    normalizeModelSettingOverrides,
    setModelSettingOverride,
    type CanonicalModelSettingField,
} from '@/lib/modelSettingsOverrides';

interface SeriesModelSettingsModalProps {
    isOpen: boolean;
    onClose: () => void;
    seriesId: string;
    onSaved?: () => void;
}

export default function SeriesModelSettingsModal({ isOpen, onClose, seriesId, onSaved }: SeriesModelSettingsModalProps) {
    const t = useTranslations("models");
    const tc = useTranslations("common");
    const defaultSettings = resolveModelSettings(undefined, 'series_settings');
    const [chatModel, setChatModel] = useState(defaultSettings.chat_model);
    const [imageModel, setImageModel] = useState(defaultSettings.image_model);
    const [i2vModel, setI2vModel] = useState(defaultSettings.i2v_model);
    const [characterAspectRatio, setCharacterAspectRatio] = useState(defaultSettings.character_aspect_ratio);
    const [sceneAspectRatio, setSceneAspectRatio] = useState(defaultSettings.scene_aspect_ratio);
    const [propAspectRatio, setPropAspectRatio] = useState(defaultSettings.prop_aspect_ratio);
    const [storyboardAspectRatio, setStoryboardAspectRatio] = useState(defaultSettings.storyboard_aspect_ratio);
    const [isSaving, setIsSaving] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [initialSettings, setInitialSettings] = useState(defaultSettings);
    const [inheritedSettings, setInheritedSettings] = useState(defaultSettings);
    const [initialOverrides, setInitialOverrides] = useState<CanonicalModelSettingField[]>([]);
    const [draftOverrides, setDraftOverrides] = useState<CanonicalModelSettingField[]>([]);
    const [wasOpen, setWasOpen] = useState(isOpen);
    const [openRevision, setOpenRevision] = useState(0);
    const [loadedRequestKey, setLoadedRequestKey] = useState<string | null>(null);

    if (isOpen !== wasOpen) {
        setWasOpen(isOpen);
        if (isOpen) setOpenRevision((revision) => revision + 1);
    }

    const requestKey = `${seriesId}:${openRevision}`;
    const isLoading = isOpen && loadedRequestKey !== requestKey;

    useEffect(() => {
        if (!isOpen || !seriesId) return;
        let cancelled = false;
        api.getSeriesModelSettings(seriesId)
            .then((data) => {
                if (cancelled) return;
                const resolvedSettings = resolveModelSettings(data, 'series_settings');
                const resolvedInheritedSettings = resolveModelSettings(
                    data.inherited_model_settings,
                    'series_settings',
                );
                const overrides = normalizeModelSettingOverrides(data.model_settings_overrides);
                setLoadError(null);
                setInitialSettings(resolvedSettings);
                setInheritedSettings(resolvedInheritedSettings);
                setInitialOverrides(overrides);
                setDraftOverrides(overrides);
                setChatModel(resolvedSettings.chat_model);
                setImageModel(resolvedSettings.image_model);
                setI2vModel(resolvedSettings.i2v_model);
                setCharacterAspectRatio(resolvedSettings.character_aspect_ratio);
                setSceneAspectRatio(resolvedSettings.scene_aspect_ratio);
                setPropAspectRatio(resolvedSettings.prop_aspect_ratio);
                setStoryboardAspectRatio(resolvedSettings.storyboard_aspect_ratio);
            })
            .catch((err) => {
                if (cancelled) return;
                console.error("Failed to load series model settings:", err);
                setLoadError(t("loadSettingsFailed"));
            })
            .finally(() => {
                if (!cancelled) setLoadedRequestKey(requestKey);
            });
        return () => {
            cancelled = true;
        };
    }, [isOpen, requestKey, seriesId]);

    const chooseSetting = (
        field: CanonicalModelSettingField,
        setter: (value: string) => void,
        value: string,
    ) => {
        setter(value);
        setDraftOverrides((fields) => setModelSettingOverride(fields, field, true));
    };

    const inheritSetting = (
        field: CanonicalModelSettingField,
        setter: (value: string) => void,
    ) => {
        setter(inheritedSettings[field]);
        setDraftOverrides((fields) => setModelSettingOverride(fields, field, false));
    };

    const isOverridden = (field: CanonicalModelSettingField) =>
        draftOverrides.includes(field);

    const handleSave = async () => {
        const draftSettings = resolveModelSettings({
            chat_model: chatModel,
            image_model: imageModel,
            video_model: i2vModel,
            character_aspect_ratio: characterAspectRatio,
            scene_aspect_ratio: sceneAspectRatio,
            prop_aspect_ratio: propAspectRatio,
            storyboard_aspect_ratio: storyboardAspectRatio,
        }, 'series_settings');
        const patch = buildModelSettingsPatch(
            initialSettings,
            initialOverrides,
            draftSettings,
            draftOverrides,
        );
        if (Object.keys(patch).length === 0) {
            onClose();
            return;
        }
        setIsSaving(true);
        try {
            await api.updateSeriesModelSettings(seriesId, patch);
            onSaved?.();
            onClose();
        } catch (error) {
            console.error("Failed to save series model settings:", error);
            const errorCode = (error as { code?: string } | null)?.code;
            alert(t(errorCode === "ECONNABORTED" ? "saveSettingsTimeout" : "saveSettingsFailed"));
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
                            aria-labelledby="series-model-settings-dialog-title"
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
                                <h2 id="series-model-settings-dialog-title" className="text-lg font-bold text-foreground">{t("seriesGenSettings")}</h2>
                                <p className="text-xs text-text-secondary">{t("seriesGenSettingsDesc")}</p>
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
                        {isLoading ? (
                            <div className="flex items-center justify-center py-12">
                                <Loader2 size={24} className="animate-spin text-blue-400" />
                                <span className="ml-2 text-text-secondary">{t("loadingSettings")}</span>
                            </div>
                        ) : loadError ? (
                            <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-sm text-red-300">
                                {loadError}
                            </div>
                        ) : (
                            <>
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between gap-3">
                                        <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                                            <MessageSquare size={16} className="text-primary" />
                                            <span>{t("chatModel")}</span>
                                        </div>
                                        <ModelSettingInheritanceButton
                                            overridden={isOverridden("chat_model")}
                                            onInherit={() => inheritSetting("chat_model", setChatModel)}
                                        />
                                    </div>
                                    <GroupedModelGrid
                                        models={SERIES_CHAT_MODELS}
                                        selectedId={chatModel}
                                        onSelect={(id) => chooseSetting("chat_model", setChatModel, id)}
                                    />
                                </div>

                                <div className="border-t border-glass-border" />

                                {/* Assets Section */}
                                <div className="space-y-5">
                                    <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                                        <Image size={16} className="text-green-400" />
                                        <span>{t("assetsT2I")}</span>
                                    </div>

                                    <div className="space-y-2">
                                        <div className="flex items-center justify-between gap-3">
                                            <span className="text-xs text-text-secondary">{t("model")}</span>
                                            <ModelSettingInheritanceButton
                                                overridden={isOverridden("image_model")}
                                                onInherit={() => inheritSetting("image_model", setImageModel)}
                                            />
                                        </div>
                                        <GroupedModelGrid
                                            models={SERIES_IMAGE_MODELS}
                                            selectedId={imageModel}
                                            onSelect={(id) => chooseSetting("image_model", setImageModel, id)}
                                        />
                                    </div>

                                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                                        {([
                                            { key: 'character', label: t("character"), icon: User, value: characterAspectRatio, setter: setCharacterAspectRatio },
                                            { key: 'scene', label: t("scene"), icon: Building, value: sceneAspectRatio, setter: setSceneAspectRatio },
                                            { key: 'prop', label: t("prop"), icon: Box, value: propAspectRatio, setter: setPropAspectRatio },
                                        ]).map(({ key, label, icon: Icon, value, setter }) => (
                                            <div key={key} className="space-y-2">
                                                <div className="flex items-center justify-between gap-2">
                                                    <div className="flex items-center gap-1 text-xs text-text-secondary">
                                                        <Icon size={12} />
                                                        <span>{label}</span>
                                                    </div>
                                                    <ModelSettingInheritanceButton
                                                        overridden={isOverridden(`${key}_aspect_ratio` as CanonicalModelSettingField)}
                                                        onInherit={() => inheritSetting(`${key}_aspect_ratio` as CanonicalModelSettingField, setter)}
                                                    />
                                                </div>
                                                <div className="space-y-1">
                                                    {ASPECT_RATIOS.map((ratio) => (
                                                        <button
                                                            key={ratio.id}
                                                            type="button"
                                                            aria-pressed={value === ratio.id}
                                                            onClick={() => chooseSetting(`${key}_aspect_ratio` as CanonicalModelSettingField, setter, ratio.id)}
                                                            className={`flex min-h-10 w-full flex-col items-center rounded border px-2 py-2 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 ${value === ratio.id
                                                                ? 'border-green-500/50 bg-green-500/10'
                                                                : 'border-glass-border hover:border-glass-border bg-glass'
                                                            }`}
                                                        >
                                                            <span className="text-xs font-medium text-foreground">{ratio.name}</span>
                                                        </button>
                                                    ))}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                <div className="border-t border-glass-border" />

                                {/* Storyboard Section */}
                                <div className="space-y-4">
                                    <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                                        <Layout size={16} className="text-blue-400" />
                                        <span>{t("storyboardI2I")}</span>
                                    </div>

                                    <div className="space-y-2">
                                        <div className="flex items-center justify-between gap-3">
                                            <span className="text-xs text-text-secondary">{t("aspectRatio")}</span>
                                            <ModelSettingInheritanceButton
                                                overridden={isOverridden("storyboard_aspect_ratio")}
                                                onInherit={() => inheritSetting("storyboard_aspect_ratio", setStoryboardAspectRatio)}
                                            />
                                        </div>
                                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                                            {ASPECT_RATIOS.map((ratio) => (
                                                <button
                                                    key={ratio.id}
                                                    type="button"
                                                    aria-pressed={storyboardAspectRatio === ratio.id}
                                                    onClick={() => chooseSetting("storyboard_aspect_ratio", setStoryboardAspectRatio, ratio.id)}
                                                    className={`flex min-h-11 flex-col items-center rounded-lg border p-3 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 ${storyboardAspectRatio === ratio.id
                                                        ? 'border-blue-500/50 bg-blue-500/10'
                                                        : 'border-glass-border hover:border-glass-border bg-glass'
                                                    }`}
                                                >
                                                    <span className="text-sm font-medium text-foreground">{ratio.name}</span>
                                                    <span className="text-[0.625rem] text-text-secondary">{ratio.description}</span>
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
                                    <p className="text-xs text-text-secondary">{t("motionFollowsAR")}</p>

                                    <div className="space-y-2">
                                        <div className="flex items-center justify-between gap-3">
                                            <span className="text-xs text-text-secondary">{t("model")}</span>
                                            <ModelSettingInheritanceButton
                                                overridden={isOverridden("video_model")}
                                                onInherit={() => inheritSetting("video_model", setI2vModel)}
                                            />
                                        </div>
                                        <GroupedModelGrid
                                            models={SERIES_I2V_MODELS}
                                            selectedId={i2vModel}
                                            onSelect={(id) => chooseSetting("video_model", setI2vModel, id)}
                                        />
                                    </div>
                                </div>
                            </>
                        )}
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
                            disabled={isSaving || isLoading || !!loadError}
                            aria-busy={isSaving}
                            className="flex min-h-11 items-center justify-center rounded-lg bg-gradient-to-r from-blue-600 to-purple-600 px-4 py-2 text-sm font-medium text-foreground transition-[border-color,box-shadow] hover:from-blue-500 hover:to-purple-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:opacity-50"
                        >
                            <StableAsyncButtonContent
                                loading={isSaving}
                                idleLabel={t("saveSettings")}
                                loadingLabel={t("saving")}
                                idleIcon={<Check size={16} aria-hidden="true" />}
                                iconSize={16}
                            />
                        </button>
                    </div>
                        </motion.div>
                    </motion.div>
                </AnimatePresence>
            )}
        </ModalPortal>
    );
}
