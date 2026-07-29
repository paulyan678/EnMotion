"use client";

import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, FileText, RotateCcw, ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { api } from '@/lib/api';
import { useTranslations } from "next-intl";
import { getApprovedModels, getModelTranslationKey } from '@/lib/newApiModels';
import ModalPortal from '@/components/common/ModalPortal';

interface SeriesPromptConfigModalProps {
    isOpen: boolean;
    onClose: () => void;
    seriesId: string;
    onSaved?: () => void;
}

interface PromptDefaults {
    storyboard_polish: string;
    video_polish: string;
}

const SECTIONS = [
    {
        key: 'storyboard_polish' as const,
        labelKey: 'storyboardPolishTitle',
        descriptionKey: 'storyboardPolishDescription',
    },
    {
        key: 'video_polish' as const,
        labelKey: 'videoPolishTitle',
        descriptionKey: 'videoPolishDescription',
    },
];

const CHAT_MODELS = getApprovedModels('chat');

export default function SeriesPromptConfigModal({ isOpen, onClose, seriesId, onSaved }: SeriesPromptConfigModalProps) {
    const t = useTranslations("series");
    const tc = useTranslations("common");
    const tm = useTranslations("models");
    const [config, setConfig] = useState<{ storyboard_polish: string; video_polish: string; polish_model: string }>({ storyboard_polish: '', video_polish: '', polish_model: '' });
    const [defaults, setDefaults] = useState<PromptDefaults | null>(null);
    const [expandedDefault, setExpandedDefault] = useState<string | null>(null);
    const [isSaving, setIsSaving] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
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
        api.getSeriesPromptConfig(seriesId)
            .then((data) => {
                if (cancelled) return;
                setLoadError(null);
                setExpandedDefault(null);
                setConfig({
                    storyboard_polish: data.prompt_config?.storyboard_polish ?? '',
                    video_polish: data.prompt_config?.video_polish ?? '',
                    // Empty means inherit the Series/global chat model.
                    // Preserve it through an open/save round trip.
                    polish_model: data.prompt_config?.polish_model ?? '',
                });
                setDefaults({
                    storyboard_polish: data.defaults?.storyboard_polish ?? '',
                    video_polish: data.defaults?.video_polish ?? '',
                });
            })
            .catch((err) => {
                if (cancelled) return;
                console.error("Failed to load series prompt config:", err);
                setLoadError(t("promptLoadFailed"));
            })
            .finally(() => {
                if (!cancelled) setLoadedRequestKey(requestKey);
            });
        return () => {
            cancelled = true;
        };
    }, [isOpen, requestKey, seriesId]);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            await api.updateSeriesPromptConfig(seriesId, config);
            onSaved?.();
            onClose();
        } catch (error) {
            console.error("Failed to save series prompt config:", error);
            alert(t("promptSaveFailed"));
        } finally {
            setIsSaving(false);
        }
    };

    const handleReset = (key: keyof PromptDefaults) => {
        setConfig(prev => ({ ...prev, [key]: '' }));
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
                            aria-labelledby="series-prompt-config-title"
                            tabIndex={-1}
                            initial={{ opacity: 0, scale: 0.95 }}
                            animate={{ opacity: 1, scale: 1 }}
                            exit={{ opacity: 0, scale: 0.95 }}
                            className="flex max-h-[calc(100dvh-1.5rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-glass-border bg-elevated sm:max-h-[calc(100dvh-2rem)]"
                            onClick={(e) => e.stopPropagation()}
                        >
                    {/* Header */}
                    <div className="flex shrink-0 items-start justify-between gap-4 border-b border-glass-border p-4 sm:items-center sm:p-6">
                        <div className="flex min-w-0 items-center gap-3">
                            <div className="p-2 bg-purple-500/20 rounded-lg">
                                <FileText size={20} className="text-purple-400" />
                            </div>
                            <div className="min-w-0">
                                <h2 id="series-prompt-config-title" className="text-lg font-bold text-foreground">{t("seriesPromptConfig")}</h2>
                                <p className="text-xs text-text-secondary">{t("seriesPromptConfigSub")}</p>
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
                    <div className="custom-scrollbar flex-1 space-y-6 overflow-y-auto p-4 sm:p-6">
                        {isLoading ? (
                            <div className="flex items-center justify-center py-12">
                                <Loader2 size={24} className="animate-spin text-purple-400" />
                                <span className="ml-2 text-text-secondary">{t("loadingConfig")}</span>
                            </div>
                        ) : loadError ? (
                            <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-sm text-red-300">
                                {loadError}
                            </div>
                        ) : (
                            <>
                                <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3 text-xs text-blue-300">
                                    {t("seriesPromptEmptyHint")}
                                </div>

                                <div className="space-y-2">
                                    <div>
                                        <h3 id="series-polish-model-label" className="text-sm font-bold text-foreground">{t("polishModelTitle")}</h3>
                                        <p className="text-[0.625rem] text-text-secondary mt-0.5">
                                            {t("polishModelDesc")}
                                        </p>
                                    </div>
                                    <select
                                        aria-labelledby="series-polish-model-label"
                                        value={config.polish_model}
                                        onChange={(e) => setConfig(prev => ({ ...prev, polish_model: e.target.value }))}
                                        className="min-h-11 w-full rounded-lg border border-glass-border bg-input-bg px-3 py-2 text-sm text-text-secondary focus:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/40"
                                    >
                                        <option value="">{t("polishModelInherit")}</option>
                                        {CHAT_MODELS.map((model) => (
                                            <option key={model.id} value={model.id}>{tm(`${getModelTranslationKey(model.id)}.name`)}</option>
                                        ))}
                                    </select>
                                    <div className="border-b border-border-subtle pt-1" />
                                </div>

                                {SECTIONS.map((section) => (
                                    <div key={section.key} className="space-y-2">
                                        <div className="flex flex-col items-start gap-2 sm:flex-row sm:justify-between">
                                            <div className="min-w-0">
                                                <h3 id={`series-${section.key}-label`} className="text-sm font-bold text-foreground">{t(section.labelKey)}</h3>
                                                <p className="text-[0.625rem] text-text-secondary mt-0.5">{t(section.descriptionKey)}</p>
                                            </div>
                                            <button
                                                type="button"
                                                onClick={() => handleReset(section.key)}
                                                disabled={!config[section.key]}
                                                className="text-[0.625rem] text-text-secondary hover:text-foreground flex items-center gap-1 px-2 py-1 rounded hover:bg-hover-bg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                                            >
                                                <RotateCcw size={10} /> {t("resetToDefault")}
                                            </button>
                                        </div>

                                        <textarea
                                            aria-labelledby={`series-${section.key}-label`}
                                            value={config[section.key]}
                                            onChange={(e) => setConfig(prev => ({ ...prev, [section.key]: e.target.value }))}
                                            placeholder={t("loadingDefault")}
                                            className="h-32 min-h-28 w-full resize-y rounded-lg border border-glass-border bg-input-bg p-3 font-mono text-xs text-text-secondary placeholder-text-muted focus:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/40"
                                        />

                                        {defaults && (
                                            <div>
                                                <button
                                                    type="button"
                                                    onClick={() => setExpandedDefault(expandedDefault === section.key ? null : section.key)}
                                                    aria-expanded={expandedDefault === section.key}
                                                    aria-controls={`series-${section.key}-default`}
                                                    className="text-[0.625rem] text-text-secondary hover:text-foreground flex items-center gap-1 transition-colors"
                                                >
                                                    {expandedDefault === section.key ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                                                    {t("viewDefault")}
                                                </button>
                                                {expandedDefault === section.key && (
                                                    <pre id={`series-${section.key}-default`} className="mt-2 max-h-48 overflow-x-auto overflow-y-auto whitespace-pre-wrap rounded-lg border border-border-subtle bg-surface p-3 font-mono text-[0.625rem] text-text-secondary">{defaults[section.key]}</pre>
                                                )}
                                            </div>
                                        )}

                                        <div className="border-b border-border-subtle" />
                                    </div>
                                ))}
                            </>
                        )}
                    </div>

                    {/* Footer */}
                    <div className="flex shrink-0 flex-col-reverse gap-3 border-t border-glass-border p-4 sm:flex-row sm:justify-end sm:p-6">
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
                            className="flex min-h-11 items-center justify-center gap-2 rounded-lg bg-primary px-6 py-2 text-sm font-medium text-on-accent transition-colors hover:bg-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:opacity-50"
                        >
                            {isSaving && <Loader2 size={14} className="animate-spin" />}
                            {tc("save")}
                        </button>
                    </div>
                        </motion.div>
                    </motion.div>
                </AnimatePresence>
            )}
        </ModalPortal>
    );
}
