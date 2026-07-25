"use client";
/**
 * ToastContainer — bottom-right stack of project-aware notifications.
 * Mounted once at the app root (Providers.tsx) so toasts survive
 * page/project navigation.
 */
import { motion, AnimatePresence } from "framer-motion";
import { CheckCircle2, AlertCircle, AlertTriangle, Info, Loader2, X } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { useToastStore, type Toast, type ToastKind } from "@/store/toastStore";

const KIND_STYLES: Record<ToastKind, { ring: string; bg: string; icon: ReactNode; iconClass: string }> = {
    info: {
        ring: "border-primary/40",
        bg: "bg-primary/10",
        icon: <Info size={14} />,
        iconClass: "text-primary",
    },
    progress: {
        ring: "border-primary/40",
        bg: "bg-primary/10",
        icon: <Loader2 size={14} className="animate-spin" />,
        iconClass: "text-primary",
    },
    success: {
        ring: "border-status-completed-border",
        bg: "bg-status-completed-bg",
        icon: <CheckCircle2 size={14} />,
        iconClass: "text-status-completed-fg",
    },
    error: {
        ring: "border-status-failed-border",
        bg: "bg-status-failed-bg",
        icon: <AlertCircle size={14} />,
        iconClass: "text-status-failed-fg",
    },
    warning: {
        ring: "border-status-processing-border",
        bg: "bg-status-processing-bg",
        icon: <AlertTriangle size={14} />,
        iconClass: "text-status-processing-fg",
    },
};

function ToastCard({ toast }: { toast: Toast }) {
    const tc = useTranslations("common");
    const dismiss = useToastStore((s) => s.dismiss);
    const style = KIND_STYLES[toast.kind];
    return (
        <motion.div
            role={toast.kind === "error" ? "alert" : "status"}
            aria-live={toast.kind === "error" ? "assertive" : "polite"}
            layout
            initial={{ opacity: 0, y: 12, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 40, transition: { duration: 0.18 } }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className={`pointer-events-auto w-[min(340px,calc(100vw-2rem))] rounded-lg border ${style.ring} ${style.bg} backdrop-blur-sm shadow-[0_8px_24px_-12px_rgba(0,0,0,0.55)] px-3 py-2.5 flex items-start gap-2.5`}
        >
            <span className={`mt-0.5 shrink-0 ${style.iconClass}`}>{style.icon}</span>
            <div className="min-w-0 flex-1">
                {toast.projectTitle && (
                    <p className="font-mono text-[0.59375rem] uppercase tracking-[0.16em] text-text-muted mb-0.5 truncate">
                        {toast.projectTitle}
                    </p>
                )}
                <p className="text-[0.8125rem] font-medium text-foreground leading-snug">{toast.title}</p>
                {toast.body && (
                    <div className="mt-0.5">
                        <p className={`text-[0.71875rem] text-text-secondary leading-snug ${toast.body.length > 120 ? "line-clamp-3" : ""}`}>
                            {toast.body}
                        </p>
                        {(toast.kind === "error" && toast.body.length > 40) && (
                            <button
                                onClick={() => { navigator.clipboard.writeText(toast.body!); }}
                                className="mt-1 min-h-9 rounded-md px-2 text-[0.6875rem] text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
                            >
                                {tc("copyErrorDetails")}
                            </button>
                        )}
                    </div>
                )}
                {toast.action && (
                    <button
                        onClick={() => {
                            toast.action!.onClick();
                            dismiss(toast.id);
                        }}
                        className="mt-1.5 inline-flex min-h-9 items-center rounded-md border border-glass-border bg-elevated px-2.5 text-[0.6875rem] font-medium text-foreground transition-colors hover:bg-hover-bg"
                    >
                        {toast.action.label}
                    </button>
                )}
            </div>
            <button
                onClick={() => dismiss(toast.id)}
                aria-label={tc("dismiss")}
                className="-mr-1 grid min-h-9 min-w-9 shrink-0 place-items-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
            >
                <X size={12} />
            </button>
        </motion.div>
    );
}

export default function ToastContainer() {
    const toasts = useToastStore((s) => s.toasts);
    return (
        <div
            aria-label="通知"
            className="pointer-events-none fixed inset-x-4 bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-[200] flex max-h-[calc(100dvh-6rem)] flex-col-reverse items-end gap-2 overflow-hidden sm:inset-x-auto sm:bottom-4 sm:right-4"
        >
            <AnimatePresence initial={false}>
                {toasts.map((t) => (
                    <ToastCard key={t.id} toast={t} />
                ))}
            </AnimatePresence>
        </div>
    );
}
