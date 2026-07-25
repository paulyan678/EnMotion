"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, CheckCircle2 } from "lucide-react";
import { useTranslations } from "next-intl";

export type BannerState = "idle" | "phase1" | "phase2" | "summary";

export interface GenerationBannerProps {
    state: BannerState;
    phase1Captions: string[];
    refineProgress?: { current: number; total: number } | null;
    summary?: { frameCount: number } | null;
}

const CAPTION_INTERVAL = 3000;

export function GenerationBanner({
    state,
    phase1Captions,
    refineProgress,
    summary,
}: GenerationBannerProps) {
    const t = useTranslations("storyboardR2V");

    if (state === "idle") return null;
    if (state === "summary" && !summary) return null;

    return (
        <AnimatePresence mode="wait">
            {state === "phase1" && (
                <Phase1Banner key="phase1" captions={phase1Captions} />
            )}

            {state === "phase2" && (
                <BannerShell key="phase2">
                    <Loader2 size={14} className="animate-spin text-status-processing-fg shrink-0" strokeWidth={1.8} />
                    <span className="text-[0.8125rem] text-text-secondary">
                        {t("bannerRefineProgress", {
                            current: refineProgress?.current ?? 0,
                            total: refineProgress?.total ?? 0,
                        })}
                    </span>
                </BannerShell>
            )}

            {state === "summary" && summary && (
                <SummaryBar
                    key="summary"
                    summary={summary}
                />
            )}
        </AnimatePresence>
    );
}

function Phase1Banner({ captions }: { captions: string[] }) {
    const [captionIndex, setCaptionIndex] = useState(0);
    const captionCount = Math.max(captions.length, 1);

    useEffect(() => {
        const timer = setInterval(() => {
            setCaptionIndex((index) => (index + 1) % captionCount);
        }, CAPTION_INTERVAL);
        return () => clearInterval(timer);
    }, [captionCount]);

    return (
        <BannerShell>
            <Loader2 size={14} className="animate-spin text-status-completed-fg shrink-0" strokeWidth={1.8} />
            <AnimatePresence mode="wait">
                <motion.span
                    key={captionIndex}
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.25 }}
                    className="text-[0.8125rem] text-text-secondary"
                >
                    {captions[captionIndex] ?? ""}
                </motion.span>
            </AnimatePresence>
        </BannerShell>
    );
}

function BannerShell({ children }: { children: React.ReactNode }) {
    return (
        <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
            className="shrink-0 px-4 pt-3 sm:px-6"
        >
            <div className="flex items-center gap-3 rounded-[14px] border border-glass-border bg-surface px-4 py-3 shadow-[var(--shadow-rest)]">
                {children}
            </div>
        </motion.div>
    );
}

function SummaryBar({
    summary,
}: {
    summary: { frameCount: number };
}) {
    const t = useTranslations("storyboardR2V");

    return (
        <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
            className="shrink-0 px-4 pt-3 sm:px-6"
        >
            <div className="flex items-center gap-3 rounded-[14px] border border-glass-border bg-surface px-4 py-3 shadow-[var(--shadow-rest)]">
                <CheckCircle2 size={16} className="text-status-completed-fg shrink-0" strokeWidth={1.8} />
                <span className="text-[0.8125rem] text-text-secondary">
                    <span className="text-status-completed-fg font-semibold">
                        {t("bannerFrameCount", { count: summary.frameCount })}
                    </span>
                </span>
            </div>
        </motion.div>
    );
}
