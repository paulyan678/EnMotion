"use client";
import { motion, AnimatePresence } from "framer-motion";
import { Users, MapPin, Box, Check, X, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import ModalPortal from "@/components/common/ModalPortal";

export interface ExtractionPreview {
    characters: { name: string; description?: string }[];
    scenes: { name: string; description?: string }[];
    props: { name: string; description?: string }[];
}

interface EntityConfirmModalProps {
    isOpen: boolean;
    preview: ExtractionPreview | null;
    currentCounts: { characters: number; scenes: number; props: number };
    onConfirm: () => void;
    onDiscard: () => void;
    applying?: boolean;
}

export default function EntityConfirmModal({
    isOpen,
    preview,
    currentCounts,
    onConfirm,
    onDiscard,
    applying = false,
}: EntityConfirmModalProps) {
    const t = useTranslations("script");

    if (!preview) return null;
    const handleDismiss = () => {
        if (!applying) onDiscard();
    };

    const sections = [
        { key: "characters" as const, icon: Users, items: preview.characters, prev: currentCounts.characters },
        { key: "scenes" as const, icon: MapPin, items: preview.scenes, prev: currentCounts.scenes },
        { key: "props" as const, icon: Box, items: preview.props, prev: currentCounts.props },
    ];

    return (
        <ModalPortal isOpen={isOpen} onClose={handleDismiss}>
            {(dialogRef) => (
                <AnimatePresence>
                    {isOpen && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            className="fixed inset-0 z-[100] flex items-center justify-center overflow-y-auto bg-overlay p-3 backdrop-blur-sm sm:p-4"
                            onClick={handleDismiss}
                        >
                            <motion.div
                                ref={dialogRef}
                                role="dialog"
                                aria-modal="true"
                                aria-busy={applying}
                                aria-labelledby="entity-confirm-title"
                                aria-describedby="entity-confirm-description"
                                tabIndex={-1}
                                initial={{ scale: 0.96, opacity: 0 }}
                                animate={{ scale: 1, opacity: 1 }}
                                exit={{ scale: 0.96, opacity: 0 }}
                                transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
                                className="relative flex max-h-[calc(100dvh-1.5rem)] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-glass-border bg-elevated shadow-[0_24px_64px_-12px_rgba(0,0,0,0.7)] sm:max-h-[calc(100dvh-2rem)]"
                                onClick={e => e.stopPropagation()}
                            >
                        {/* Header */}
                        <header className="shrink-0 border-b border-glass-border px-4 py-4 sm:px-6 sm:py-5">
                            <h2 id="entity-confirm-title" className="font-display text-display font-medium text-foreground">
                                {t("extractConfirmTitle")}
                            </h2>
                            <p id="entity-confirm-description" className="mt-1 text-xs text-text-secondary">
                                {t("extractConfirmSubtitle")}
                            </p>
                        </header>

                        {/* Body */}
                        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:px-6">
                            {sections.map(({ key, icon: Icon, items, prev }) => (
                                <div key={key} className="space-y-2">
                                    <div className="flex items-center gap-2 text-sm text-text-secondary">
                                        <Icon size={14} />
                                        <span className="font-medium">
                                            {t(`entityKind_${key}`)}
                                        </span>
                                        <span className="ml-auto text-xs opacity-70">
                                            {prev} → {items.length}
                                        </span>
                                    </div>
                                    {items.length > 0 ? (
                                        <div className="flex flex-wrap gap-1.5">
                                            {items.map((item, i) => (
                                                <span
                                                    key={i}
                                                    className="inline-flex items-center px-2 py-0.5 rounded-md bg-elevated border border-glass-border text-xs text-foreground"
                                                    title={item.description}
                                                >
                                                    {item.name}
                                                </span>
                                            ))}
                                        </div>
                                    ) : (
                                        <p className="text-xs italic text-text-muted">{t("noEntities")}</p>
                                    )}
                                </div>
                            ))}
                        </div>

                        {/* Footer */}
                        <footer className="flex shrink-0 flex-col-reverse gap-3 border-t border-glass-border px-4 py-4 sm:flex-row sm:items-center sm:justify-end sm:px-6">
                            <button
                                type="button"
                                onClick={onDiscard}
                                disabled={applying}
                                className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg px-4 py-2 text-sm text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                            >
                                <X size={14} />
                                {t("extractDiscard")}
                            </button>
                            <button
                                type="button"
                                onClick={onConfirm}
                                disabled={applying}
                                className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-on-accent transition-colors hover:bg-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                                {applying ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                                {t("extractApply")}
                            </button>
                        </footer>
                            </motion.div>
                        </motion.div>
                    )}
                </AnimatePresence>
            )}
        </ModalPortal>
    );
}
