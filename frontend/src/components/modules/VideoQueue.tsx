"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, RefreshCw, Copy, Download, Trash2, AlertCircle } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import NextImage from "next/image";

import { VideoTask } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";

interface VideoQueueProps {
    tasks: VideoTask[];
    onRemix: (task: VideoTask) => void;
    onRetry: (task: VideoTask) => Promise<void>;
    onDelete: (task: VideoTask) => Promise<void>;
    shotNumberByFrameId?: Record<string, number>;
}

type VideoQueueFilter = "all" | "queued" | "processing" | "completed" | "failed" | "canceled";

export default function VideoQueue({
    tasks,
    onRemix,
    onRetry,
    onDelete,
    shotNumberByFrameId = {},
}: VideoQueueProps) {
    const tv = useTranslations("video");
    const [filter, setFilter] = useState<VideoQueueFilter>("all");

    const filteredTasks = tasks.filter(t => {
        if (filter === "all") return true;
        if (filter === "queued") return t.status === "pending";
        if (filter === "processing") return t.status === "processing";
        return t.status === filter;
    }).reverse(); // Newest first

    const processingCount = tasks.filter(t => t.status === "processing").length;
    const filterCounts: Record<VideoQueueFilter, number> = {
        all: tasks.length,
        queued: tasks.filter((task) => task.status === "pending").length,
        processing: processingCount,
        completed: tasks.filter((task) => task.status === "completed").length,
        failed: tasks.filter((task) => task.status === "failed").length,
        canceled: tasks.filter((task) => task.status === "canceled").length,
    };

    return (
        <div className="h-full flex flex-col bg-surface border-l border-border-subtle">
            {/* Header & Tabs */}
            <div className="p-4 border-b border-border-subtle">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="font-display font-bold text-foreground">{tv("taskQueue")}</h3>
                    <div className="text-xs font-mono text-text-muted flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${processingCount > 0 ? "bg-green-500 animate-pulse" : "bg-gray-600"}`} />
                        {tv("computeStatus")} {processingCount > 0 ? tv("running") : tv("idle")}
                    </div>
                </div>

                <div className="flex gap-1 overflow-x-auto rounded-lg bg-glass p-1 custom-scrollbar">
                    {([
                        { id: "all", label: tv("all") },
                        { id: "queued", label: tv("queued") },
                        { id: "processing", label: tv("processing") },
                        { id: "completed", label: tv("completed") },
                        { id: "failed", label: tv("failed") },
                        { id: "canceled", label: tv("canceled") },
                    ] satisfies Array<{ id: VideoQueueFilter; label: string }>).map((tab) => (
                        <button
                            key={tab.id}
                            onClick={() => setFilter(tab.id)}
                            aria-label={tab.label}
                            className={`flex min-w-max flex-1 items-center justify-center gap-1 rounded-md px-2 py-1.5 text-xs transition-colors ${filter === tab.id
                                ? "bg-hover-bg text-foreground font-medium shadow-sm"
                                : "text-text-muted hover:text-text-secondary"
                                }`}
                        >
                            {tab.label}
                            <span className="font-mono text-[10px] opacity-70">{filterCounts[tab.id]}</span>
                        </button>
                    ))}
                </div>
            </div>

            {/* Task List */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
                <AnimatePresence mode="popLayout">
                    {filteredTasks.map((task) => (
                        <TaskCard
                            key={task.id}
                            task={task}
                            onRemix={onRemix}
                            onRetry={onRetry}
                            onDelete={onDelete}
                            shotNumber={task.frame_id ? shotNumberByFrameId[task.frame_id] : undefined}
                        />
                    ))}

                    {filteredTasks.length === 0 && (
                        <div className="text-center py-10 text-text-muted text-sm">
                            {tv("noTasks")}
                        </div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
}

function TaskCard({
    task,
    onRemix,
    onRetry,
    onDelete,
    shotNumber,
}: {
    task: VideoTask;
    onRemix: (t: VideoTask) => void;
    onRetry: (t: VideoTask) => Promise<void>;
    onDelete: (t: VideoTask) => Promise<void>;
    shotNumber?: number;
}) {
    const tv = useTranslations("video");
    const locale = useLocale();
    const ts = useTranslations("storyboard");
    const [deleting, setDeleting] = useState(false);
    const [retrying, setRetrying] = useState(false);
    const isCompleted = task.status === "completed";
    const isProcessing = task.status === "processing" || task.status === "pending";
    const isFailed = task.status === "failed";
    const isCanceled = task.status === "canceled";

    const failureMessage = (() => {
        switch (task.error_code) {
            case "input_image_privacy": return tv("errors.inputImagePrivacy");
            case "video_generation_timeout": return tv("errors.timeout");
            case "video_generation_interrupted": return tv("errors.interrupted");
            case "video_queue_unavailable": return tv("errors.queueUnavailable");
            case "video_generation_failed": return tv("errors.generic");
            default:
                return task.error && (locale !== "zh" || !/[A-Za-z]{2,}/.test(task.error))
                    ? task.error
                    : tv("unknownError");
        }
    })();


    const getDisplayUrl = (url: string) => {
        return getAssetUrl(url);
    };

    const handleDelete = async () => {
        if (deleting || !window.confirm(tv("confirmDeleteTask"))) return;
        setDeleting(true);
        try {
            await onDelete(task);
        } finally {
            setDeleting(false);
        }
    };

    const handleRetry = async () => {
        if (retrying) return;
        setRetrying(true);
        try {
            await onRetry(task);
        } finally {
            setRetrying(false);
        }
    };

    return (
        <motion.div
            layout
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95 }}
            className={`rounded-xl overflow-hidden border transition-all ${isProcessing ? "bg-glass border-glass-border" :
                isFailed ? "bg-red-500/5 border-red-500/20" :
                isCanceled ? "bg-glass border-glass-border" :
                    "bg-surface border-glass-border hover:border-glass-border"
                }`}
        >
            {task.frame_id ? (
                <div className="flex flex-wrap items-center gap-2 border-b border-glass-border px-3 py-2 text-[11px] text-text-muted">
                    <span className="font-medium text-text-secondary">
                        {shotNumber ? tv("shotNumber", { number: shotNumber }) : `#${task.frame_id.slice(0, 8)}`}
                    </span>
                    {task.frame_type ? (
                        <span className="rounded-full bg-glass px-2 py-0.5">
                            {ts(`frameTypes.${task.frame_type}`)}
                        </span>
                    ) : null}
                </div>
            ) : null}
            {/* Processing State (Compact) */}
            {isProcessing && (
                <div className="p-3 flex gap-3 items-center">
                    <div className="w-12 h-12 rounded bg-surface/50 relative overflow-hidden flex-shrink-0">
                        {task.image_url ? (
                            <NextImage
                                src={getDisplayUrl(task.image_url)}
                                alt={tv("input")}
                                fill
                                sizes="48px"
                                className="object-cover opacity-60"
                                unoptimized
                            />
                        ) : null}
                        <div className="absolute inset-0 flex items-center justify-center">
                            <Loader2 className="animate-spin text-primary" size={16} />
                        </div>
                    </div>
                    <div className="flex-1 min-w-0">
                        <div className="flex justify-between items-center mb-1">
                            <span className="text-xs font-mono text-text-secondary">#{task.id.slice(0, 6)}</span>
                            <span className="text-xs text-primary animate-pulse">
                                {task.status === "pending" ? tv("queued") : tv("generating")}
                            </span>
                        </div>
                        <p className="text-xs text-text-secondary truncate">{task.prompt}</p>
                    </div>
                </div>
            )}

            {/* Completed State (Detailed) */}
            {isCompleted && (
                <div>
                    {/* Header */}
                    <div className="px-3 py-2 border-b border-border-subtle flex justify-between items-center bg-glass">
                        <span className="text-xs font-mono text-text-muted">#{task.id.slice(0, 6)}</span>
                        <div className="flex gap-2">
                            <button
                                onClick={() => onRemix(task)}
                                className="text-xs flex items-center gap-1 text-text-secondary hover:text-foreground transition-colors"
                                title={tv("remixTitle")}
                            >
                                <RefreshCw size={12} /> {tv("remix")}
                            </button>
                        </div>
                    </div>

                    {/* Visual Comparison */}
                    <div className="flex h-32 relative group">
                        {/* Input Image/Videos (Left) */}
                        <div className="w-1/2 relative border-r border-glass-border">
                            {task.image_url ? (
                                <NextImage
                                    src={getDisplayUrl(task.image_url)}
                                    alt={tv("input")}
                                    fill
                                    sizes="180px"
                                    className="object-cover"
                                    unoptimized
                                />
                            ) : null}
                            <div className="absolute top-2 left-2 bg-surface px-1.5 py-0.5 rounded text-[0.625rem] text-text-secondary">{tv("input")}</div>
                        </div>

                        {/* Output Video (Right) */}
                        <div className="w-1/2 relative bg-black">
                            {task.video_url ? (
                                <video
                                    src={getAssetUrl(task.video_url)}
                                    controls
                                    className="w-full h-full object-cover"
                                />
                            ) : (
                                <div className="w-full h-full flex items-center justify-center text-red-500 text-xs">
                                    {tv("error")}
                                </div>
                            )}
                            <div className="absolute top-2 right-2 bg-primary/80 px-1.5 py-0.5 rounded text-[0.625rem] text-foreground">{tv("result")}</div>
                        </div>
                    </div>

                    {/* Prompt & Actions */}
                    <div className="p-3">
                        <p className="text-xs text-text-secondary line-clamp-2 mb-3 hover:line-clamp-none transition-all cursor-help">
                            {task.prompt}
                        </p>

                        <div className="flex justify-between items-center">
                            <div className="flex gap-2">
                                <button className="p-1.5 hover:bg-hover-bg rounded text-text-secondary hover:text-foreground">
                                    <Copy size={14} />
                                </button>
                                <button className="p-1.5 hover:bg-hover-bg rounded text-text-secondary hover:text-foreground">
                                    <Download size={14} />
                                </button>
                            </div>
                            <button
                                type="button"
                                onClick={() => { void handleDelete(); }}
                                disabled={deleting}
                                className="p-1.5 hover:bg-red-500/20 rounded text-text-muted hover:text-red-400 disabled:cursor-wait disabled:opacity-40"
                                title={tv("deleteTask")}
                                aria-label={tv("deleteTask")}
                            >
                                {deleting ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Failed State */}
            {isFailed && (
                <div className="p-3">
                    <div className="flex items-center gap-2 text-red-400 mb-2">
                        <AlertCircle size={16} />
                        <span className="text-sm font-medium">{tv("genFailed")}</span>
                    </div>
                    <p className="text-xs text-text-muted mb-2">{failureMessage}</p>
                    {task.error_diagnostic ? (
                        <details className="mb-3 text-xs text-text-muted">
                            <summary className="cursor-pointer hover:text-text-secondary">{tv("technicalDetails")}</summary>
                            <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-black/30 p-2 font-mono text-[0.625rem]">
                                {locale === "zh" ? tv("diagnosticHidden") : task.error_diagnostic}
                            </pre>
                        </details>
                    ) : null}
                    <div className="flex gap-2">
                        <button
                            onClick={() => { void handleRetry(); }}
                            disabled={retrying}
                            className="flex-1 py-1.5 bg-glass hover:bg-hover-bg rounded text-xs text-text-secondary transition-colors disabled:cursor-wait disabled:opacity-60"
                        >
                            {retrying ? tv("retrying") : tv("retryTask")}
                        </button>
                        <button
                            type="button"
                            onClick={() => { void handleDelete(); }}
                            disabled={deleting}
                            className="px-2.5 py-1.5 bg-glass hover:bg-red-500/20 rounded text-text-muted hover:text-red-400 disabled:cursor-wait disabled:opacity-40"
                            title={tv("deleteTask")}
                            aria-label={tv("deleteTask")}
                        >
                            {deleting ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                        </button>
                    </div>
                </div>
            )}

            {isCanceled && (
                <div className="p-3">
                    <p className="text-sm font-medium text-text-secondary">{tv("canceled")}</p>
                    <p className="mt-1 text-xs text-text-muted">{tv("canceledMessage")}</p>
                </div>
            )}
        </motion.div>
    );
}
