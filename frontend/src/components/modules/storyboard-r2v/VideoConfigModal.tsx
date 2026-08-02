"use client";

import { useMemo, useState } from "react";
import { Check, Settings2, X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useTranslations } from "next-intl";

import GroupedModelGrid from "@/components/common/GroupedModelGrid";
import {
    DEFAULT_I2V_MODEL_ID,
    type DurationConfig,
    VIDEO_I2V_MODELS,
} from "@/lib/modelCatalog";
import { getModelTranslationKey } from "@/lib/newApiModels";

export interface VideoConfig {
    model: string;
    duration: number;
    resolution: string;
    generateAudio: boolean;
    ratio: string;
    watermark: boolean;
}

interface VideoConfigModalProps {
    isOpen: boolean;
    onClose: () => void;
    config: VideoConfig;
    onConfigChange: (config: VideoConfig) => void;
}

export const DEFAULT_VIDEO_CONFIG: VideoConfig = {
    model: DEFAULT_I2V_MODEL_ID,
    duration: 5,
    resolution: "720p",
    generateAudio: true,
    ratio: "16:9",
    watermark: false,
};

const RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4"];

export default function VideoConfigModal({
    isOpen,
    onClose,
    config,
    onConfigChange,
}: VideoConfigModalProps) {
    const t = useTranslations("storyboardR2V");
    const tm = useTranslations("models");
    const [draft, setDraft] = useState<VideoConfig>(config);
    const [syncedConfig, setSyncedConfig] = useState(config);
    const [wasOpen, setWasOpen] = useState(isOpen);

    const reopened = isOpen && !wasOpen;
    if (isOpen !== wasOpen) setWasOpen(isOpen);
    if (isOpen && (config !== syncedConfig || reopened)) {
        setSyncedConfig(config);
        setDraft(config);
    }

    const activeModel = useMemo(
        () => VIDEO_I2V_MODELS.find((model) => model.id === draft.model)
            ?? VIDEO_I2V_MODELS.find((model) => model.id === DEFAULT_I2V_MODEL_ID)
            ?? VIDEO_I2V_MODELS[0],
        [draft.model],
    );

    const selectModel = (modelId: string) => {
        const model = VIDEO_I2V_MODELS.find((item) => item.id === modelId);
        if (!model) return;
        let duration = draft.duration;
        if (model.duration.type === "fixed") duration = model.duration.value;
        if (model.duration.type === "buttons" && !model.duration.options.includes(duration)) {
            duration = model.duration.default;
        }
        if (
            model.duration.type === "slider" &&
            (duration < model.duration.min || duration > model.duration.max)
        ) {
            duration = model.duration.default;
        }
        setDraft((current) => ({ ...current, model: modelId, duration }));
    };

    return (
        <AnimatePresence>
            {isOpen ? (
                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-lg"
                    onClick={onClose}
                >
                    <motion.div
                        initial={{ opacity: 0, y: 20, scale: 0.97 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: 20, scale: 0.97 }}
                        className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-glass-border bg-surface shadow-2xl"
                        onClick={(event) => event.stopPropagation()}
                    >
                        <header className="flex items-center justify-between border-b border-glass-border px-7 py-5">
                            <div className="flex items-center gap-3">
                                <span className="grid h-9 w-9 place-items-center rounded-xl border border-glass-border bg-glass">
                                    <Settings2 size={16} />
                                </span>
                                <div>
                                    <h2 className="text-sm font-semibold text-foreground">{t("videoSettings")}</h2>
                                    <p className="mt-0.5 text-xs text-text-muted">{t("videoGenerationLabel")} · {activeModel ? tm(`${getModelTranslationKey(activeModel.id)}.name`) : ""}</p>
                                </div>
                            </div>
                            <button type="button" onClick={onClose} className="rounded-lg p-2 text-text-muted hover:bg-hover-bg hover:text-foreground">
                                <X size={18} />
                            </button>
                        </header>

                        <div className="flex-1 space-y-8 overflow-y-auto px-7 py-6">
                            <section className="space-y-3">
                                <h3 className="text-[10px] font-bold uppercase tracking-[0.2em] text-text-muted">{t("modelSelection")}</h3>
                                <GroupedModelGrid
                                    models={VIDEO_I2V_MODELS}
                                    selectedId={draft.model}
                                    onSelect={selectModel}
                                />
                            </section>

                            <section className="grid gap-5 sm:grid-cols-2">
                                <Field label={t("durationLabel")}>
                                    <DurationControl
                                        config={activeModel?.duration ?? { type: "slider", min: 4, max: 15, step: 1, default: 5 }}
                                        value={draft.duration}
                                        onChange={(duration) => setDraft((current) => ({ ...current, duration }))}
                                    />
                                </Field>
                                <Field label={t("resolution")}>
                                    <select value={draft.resolution} onChange={(event) => setDraft((current) => ({ ...current, resolution: event.target.value }))} className="glass-input w-full text-xs">
                                        <option value="720p">720p</option>
                                        <option value="1080p">1080p</option>
                                    </select>
                                </Field>
                                <Field label={t("aspectRatio")}>
                                    <select value={draft.ratio} onChange={(event) => setDraft((current) => ({ ...current, ratio: event.target.value }))} className="glass-input w-full text-xs">
                                        {RATIOS.map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}
                                    </select>
                                </Field>
                                <div className="space-y-2">
                                    <Toggle label={t("generateSynchronizedAudio")} checked={draft.generateAudio} onChange={(generateAudio) => setDraft((current) => ({ ...current, generateAudio }))} />
                                    <Toggle label={t("watermark")} checked={draft.watermark} onChange={(watermark) => setDraft((current) => ({ ...current, watermark }))} />
                                </div>
                            </section>
                        </div>

                        <footer className="flex justify-end gap-3 border-t border-glass-border px-7 py-4">
                            <button type="button" onClick={onClose} className="glass-button px-4 py-2 text-sm">{t("cancel")}</button>
                            <button
                                type="button"
                                onClick={() => {
                                    onConfigChange(draft);
                                    onClose();
                                }}
                                className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white"
                            >
                                <Check size={15} /> {t("applySettings")}
                            </button>
                        </footer>
                    </motion.div>
                </motion.div>
            ) : null}
        </AnimatePresence>
    );
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div>
            <label className="mb-2 block text-xs text-text-secondary">{label}</label>
            {children}
        </div>
    );
}

function Toggle({
    label,
    checked,
    onChange,
}: {
    label: string;
    checked: boolean;
    onChange: (checked: boolean) => void;
}) {
    return (
        <label className="flex cursor-pointer items-center justify-between rounded-lg bg-glass px-3 py-2 text-xs text-text-secondary">
            <span>{label}</span>
            <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="accent-primary" />
        </label>
    );
}

function DurationControl({
    config,
    value,
    onChange,
}: {
    config: DurationConfig;
    value: number;
    onChange: (duration: number) => void;
}) {
    if (config.type === "fixed") {
        return <div className="rounded-lg bg-glass py-2 text-center text-xs text-text-muted">{config.value}秒</div>;
    }
    if (config.type === "buttons") {
        return (
            <div className="grid grid-cols-3 gap-2">
                {config.options.map((duration) => (
                    <button type="button" key={duration} onClick={() => onChange(duration)} className={`rounded-lg border py-2 text-xs ${value === duration ? "border-primary bg-primary/15 text-primary" : "border-transparent bg-glass text-text-secondary"}`}>
                        {duration}秒
                    </button>
                ))}
            </div>
        );
    }
    return (
        <div>
            <input type="range" min={config.min} max={config.max} step={config.step} value={value} onChange={(event) => onChange(Number(event.target.value))} className="w-full accent-primary" />
            <div className="text-center text-xs font-medium text-primary">{value}秒</div>
        </div>
    );
}
