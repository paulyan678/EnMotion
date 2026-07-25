"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useProjectStore } from "@/store/projectStore";
import ModalPortal from "@/components/common/ModalPortal";

interface CreateProjectDialogProps {
    isOpen: boolean;
    onClose: () => void;
    seriesId?: string;
    seriesTitle?: string;
}

export default function CreateProjectDialog({ isOpen, onClose, seriesId, seriesTitle }: CreateProjectDialogProps) {
    const [title, setTitle] = useState("");
    const [text, setText] = useState("");
    const [isCreating, setIsCreating] = useState(false);
    const createProject = useProjectStore((state) => state.createProject);
    const t = useTranslations("project");
    const tc = useTranslations("common");
    const locale = useLocale();


    const handleCreate = async () => {
        if (!title) {
            alert(t("titleRequired"));
            return;
        }

        setIsCreating(true);
        try {
            await createProject(title, text, true, "i2v_legacy", seriesId);
            // Get the newly created project
            const currentProject = useProjectStore.getState().currentProject;
            if (currentProject) {
                // Use hash-based routing to match the app's routing structure
                window.location.hash = `#/project/${currentProject.id}`;
            }
            onClose();
        } catch (error: any) {
            const errorMessage = locale === "zh"
                ? t("checkBackend")
                : error?.response?.data?.detail || error?.message || t("checkBackend");
            alert(t("createFailed", { error: errorMessage }));
        } finally {
            setIsCreating(false);
        }
    };

    return (
        <ModalPortal isOpen={isOpen} onClose={onClose}>
            {(dialogRef) => (
                <AnimatePresence>
                    {isOpen && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-overlay p-3 backdrop-blur-sm sm:p-6"
                            onClick={onClose}
                        >
                            <motion.div
                                ref={dialogRef}
                                role="dialog"
                                aria-modal="true"
                                aria-labelledby="create-project-dialog-title"
                                tabIndex={-1}
                                initial={{ scale: 0.96, opacity: 0 }}
                                animate={{ scale: 1, opacity: 1 }}
                                exit={{ scale: 0.96, opacity: 0 }}
                                className="flex max-h-[calc(100dvh-1.5rem)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-border bg-elevated shadow-2xl sm:max-h-[calc(100dvh-3rem)]"
                                onClick={(e) => e.stopPropagation()}
                            >
                                <div className="mb-5 flex shrink-0 items-start justify-between gap-4 px-4 pt-4 sm:mb-6 sm:px-8 sm:pt-8">
                            <div>
                                <h2 id="create-project-dialog-title" className="font-display text-xl font-bold text-foreground sm:text-2xl">{t("createTitle")}</h2>
                                {seriesId && (
                                    <div className="mt-1 font-mono text-[0.6875rem] uppercase tracking-wider text-primary">
                                        {t("series")} · {seriesTitle}
                                    </div>
                                )}
                            </div>
                            <button
                                type="button"
                                onClick={onClose}
                                aria-label={tc("close")}
                                className="grid h-10 w-10 shrink-0 place-items-center rounded-lg text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                            >
                                <X size={20} />
                            </button>
                        </div>

                                <div className="space-y-4 overflow-y-auto px-4 pb-4 sm:px-8 sm:pb-8">
                            <div>
                                <label htmlFor="create-project-title" className="mb-2 block text-sm font-medium text-foreground">
                                    {t("projectTitle")}
                                </label>
                                <input
                                    id="create-project-title"
                                    type="text"
                                    value={title}
                                    onChange={(e) => setTitle(e.target.value)}
                                    placeholder={t("projectTitlePlaceholder")}
                                    className="glass-input w-full"
                                />
                            </div>

                            <div>
                                <label htmlFor="create-project-script" className="mb-2 block text-sm font-medium text-foreground">
                                    {t("scriptContent")}
                                </label>
                                <textarea
                                    id="create-project-script"
                                    value={text}
                                    onChange={(e) => setText(e.target.value)}
                                    placeholder={t("scriptPlaceholder")}
                                    rows={8}
                                    className="glass-input max-h-[40dvh] w-full resize-y font-mono text-sm"
                                />
                            </div>

                            <div className="flex flex-col-reverse gap-3 pt-4 sm:flex-row">
                                <button
                                    type="button"
                                    onClick={onClose}
                                    className="glass-button min-h-11 flex-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                                >
                                    {tc("cancel")}
                                </button>
                                <button
                                    type="button"
                                    onClick={handleCreate}
                                    disabled={isCreating || !title}
                                    className="min-h-11 flex-1 rounded-lg bg-primary px-6 py-3 font-medium text-on-accent transition-colors hover:bg-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                    {isCreating ? t("creating") : t("createProject")}
                                </button>
                            </div>
                        </div>
                            </motion.div>
                        </motion.div>
                    )}
                </AnimatePresence>
            )}
        </ModalPortal>
    );
}
