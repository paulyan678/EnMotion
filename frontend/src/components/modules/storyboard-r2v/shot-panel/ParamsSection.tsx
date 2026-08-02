"use client";

import { useCallback, useMemo } from "react";
import { Dices, X } from "lucide-react";

import type { DurationConfig, I2VModelConfig } from "@/lib/modelCatalog";
import SectionShell from "./SectionShell";
import { usePanelSectionState } from "./usePanelSectionState";
import { useLocale, useTranslations } from "next-intl";
import { getModelTranslationKey } from "@/lib/newApiModels";

export interface ParamsState {
    model: string;
    duration: number;
    count: number;
    resolution?: string;
    ratio?: string;
    seed?: number;
    generateAudio?: boolean;
    watermark?: boolean;
}

interface ParamsSectionProps {
    shotId: string;
    modelList: I2VModelConfig[];
    title: string;
    params: ParamsState;
    onChange: (next: ParamsState) => void;
    inFlightCount?: number;
    errorMessage?: string | null;
    requestReview?: React.ReactNode;
}

const RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4"];

export default function ParamsSection({
    shotId,
    modelList,
    title,
    params,
    onChange,
    inFlightCount = 0,
    errorMessage,
    requestReview,
}: ParamsSectionProps) {
    const t = useTranslations("storyboardR2V");
    const tm = useTranslations("models");
    const locale = useLocale();
    const [open, setOpen] = usePanelSectionState(shotId, "params", true);
    const activeModel = useMemo(
        () => modelList.find((model) => model.id === params.model) ?? modelList[0],
        [modelList, params.model],
    );
    const durationConfig = activeModel?.duration ?? { type: "slider", min: 4, max: 15, step: 1, default: 5 };

    const set = useCallback(<K extends keyof ParamsState>(key: K, value: ParamsState[K]) => {
        onChange({ ...params, [key]: value });
    }, [onChange, params]);

    const selectModel = (modelId: string) => {
        const model = modelList.find((item) => item.id === modelId);
        if (!model) return;
        let duration = params.duration;
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
        onChange({ ...params, model: modelId, duration });
    };

    return (
        <SectionShell
            title={title}
            open={open}
            onToggle={() => setOpen(!open)}
            subtitle={activeModel ? tm(`${getModelTranslationKey(activeModel.id)}.name`) : undefined}
            trailing={inFlightCount > 0 ? (
                <span className="rounded-full border border-status-processing-border bg-status-processing-bg px-1.5 py-0.5 text-[9px] font-semibold text-status-processing-fg">
                    {t("runningCount", { count: inFlightCount })}
                </span>
            ) : undefined}
        >
            <div className="space-y-3">
                <ParamRow label={t("model")}>
                    <select value={params.model} onChange={(event) => selectModel(event.target.value)} className="glass-input w-full text-xs">
                        {modelList.map((model) => (
                            <option key={model.id} value={model.id}>{tm(`${getModelTranslationKey(model.id)}.name`)}</option>
                        ))}
                    </select>
                </ParamRow>
                <ParamRow label={t("duration")}>
                    <DurationControl config={durationConfig} value={params.duration} onChange={(duration) => set("duration", duration)} />
                </ParamRow>
                <ParamRow label={t("resolution")}>
                    <Pills options={["720p", "1080p"]} value={params.resolution ?? "720p"} onChange={(resolution) => set("resolution", resolution)} />
                </ParamRow>
                <ParamRow label={t("ratio")}>
                    <Pills options={RATIOS} value={params.ratio ?? "16:9"} onChange={(ratio) => set("ratio", ratio)} />
                </ParamRow>
                <ParamRow label={t("seed")}>
                    <div className="flex items-center gap-2">
                        <input
                            type="number"
                            value={params.seed ?? ""}
                            onChange={(event) => set("seed", event.target.value === "" ? undefined : Number(event.target.value))}
                            placeholder={t("random")}
                            className="glass-input w-36 text-xs"
                        />
                        <button type="button" onClick={() => set("seed", Math.floor(Math.random() * 1_000_000_000))} className="rounded-lg p-2 text-text-muted hover:bg-hover-bg" aria-label={t("randomSeed")}>
                            <Dices size={15} />
                        </button>
                        {params.seed !== undefined ? (
                            <button type="button" onClick={() => set("seed", undefined)} className="rounded-lg p-2 text-text-muted hover:bg-hover-bg" aria-label={t("clearSeed")}>
                                <X size={14} />
                            </button>
                        ) : null}
                    </div>
                </ParamRow>
                <ParamRow label={t("audio")}>
                    <Toggle checked={params.generateAudio ?? true} onChange={(value) => set("generateAudio", value)} label={t("generateSynchronizedAudio")} />
                </ParamRow>
                <ParamRow label={t("watermark")}>
                    <Toggle checked={params.watermark ?? false} onChange={(value) => set("watermark", value)} label={t("embedWatermark")} />
                </ParamRow>
                {errorMessage ? (
                    <div role="alert" className="rounded-lg border border-status-failed-border bg-status-failed-bg px-3 py-2 text-sm text-status-failed-fg">
                        {locale === "zh" ? t("unknownErrorFallback") : errorMessage}
                    </div>
                ) : null}
                {requestReview}
            </div>
        </SectionShell>
    );
}
function ParamRow({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div className="flex flex-col gap-1.5 sm:flex-row sm:gap-4">
            <span className="w-20 shrink-0 pt-1.5 font-mono text-[11px] font-medium uppercase tracking-[0.1em] text-text-muted">{label}</span>
            <div className="min-w-0 flex-1">{children}</div>
        </div>
    );
}

function Pills({
    options,
    value,
    onChange,
}: {
    options: string[];
    value: string;
    onChange: (value: string) => void;
}) {
    return (
        <div className="flex flex-wrap gap-2">
            {options.map((option) => (
                <button type="button" key={option} onClick={() => onChange(option)} className={`rounded-full border px-2.5 py-1 font-mono text-[10px] ${value === option ? "border-primary/45 bg-primary/15 text-primary" : "border-glass-border bg-surface-inset text-text-secondary"}`}>
                    {option}
                </button>
            ))}
        </div>
    );
}

function Toggle({
    checked,
    onChange,
    label,
}: {
    checked: boolean;
    onChange: (checked: boolean) => void;
    label: string;
}) {
    return (
        <label className="flex cursor-pointer items-center gap-2 text-xs text-text-secondary">
            <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="accent-primary" />
            {label}
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
    onChange: (value: number) => void;
}) {
    if (config.type === "fixed") return <span className="text-xs text-text-secondary">{config.value}秒</span>;
    if (config.type === "buttons") {
        return <Pills options={config.options.map(String)} value={String(value)} onChange={(next) => onChange(Number(next))} />;
    }
    return (
        <div className="flex items-center gap-2">
            <input type="range" min={config.min} max={config.max} step={config.step} value={value} onChange={(event) => onChange(Number(event.target.value))} className="flex-1 accent-primary" />
            <span className="w-10 text-right font-mono text-xs text-primary">{value}秒</span>
        </div>
    );
}
