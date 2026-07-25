"use client";
/**
 * EpisodeMiniList — compact episode switcher rendered at the top of
 * PipelineSidebar when the current project belongs to a Series.
 *
 * Solves the friction L3 raised in the 火山剧创 comparison: switching
 * between episodes of the same series used to require Series detail
 * page → pick another episode → re-open Studio (3 clicks). Now it's
 * one click while staying on the same module (e.g. Storyboard).
 *
 * UX:
 *   - Vertical scrollable list of episodes, sorted by episode_number
 *   - Each item: small chip "Ep N" + truncated title, active highlighted
 *   - Click → canonical series/episode route (keeps the mounted editor state)
 *   - Hidden entirely when project has no series_id (standalone projects)
 *
 * Backend hit is one-shot per session (cached by series id). Falls back
 * to silent hide on fetch error — non-blocking.
 */
import { useEffect, useState } from "react";
import clsx from "clsx";
import { api } from "@/lib/api";
import { debugLog } from "@/lib/debugLog";

interface EpisodeListItem {
    id: string;
    title: string;
    episode_number?: number;
}

interface EpisodeMiniListProps {
    seriesId: string;
    currentProjectId: string;
}

export default function EpisodeMiniList(props: EpisodeMiniListProps) {
    return <EpisodeMiniListForSeries key={props.seriesId} {...props} />;
}

function EpisodeMiniListForSeries({
    seriesId,
    currentProjectId,
}: EpisodeMiniListProps) {
    const [episodes, setEpisodes] = useState<EpisodeListItem[] | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        api.getSeriesEpisodes(seriesId)
            .then((eps: any[]) => {
                if (cancelled) return;
                // Sort by episode_number asc; episodes without a
                // number fall to the end in stable insertion order.
                const sorted = [...eps].sort((a, b) => {
                    const an = typeof a.episode_number === "number" ? a.episode_number : 999;
                    const bn = typeof b.episode_number === "number" ? b.episode_number : 999;
                    return an - bn;
                });
                setEpisodes(sorted.map((e) => ({
                    id: e.id,
                    title: e.title || `Episode ${e.episode_number ?? "?"}`,
                    episode_number: e.episode_number,
                })));
            })
            .catch((err) => {
                debugLog.warn("Studio", "EpisodeMiniList fetch failed:", err);
                if (!cancelled) setEpisodes([]);
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => { cancelled = true; };
    }, [seriesId]);

    // Always render the current series' episode list, including a
    // single-episode series, so the active context remains visible.
    if (loading || !episodes || episodes.length === 0) return null;

    const handleSwitch = (epId: string) => {
        if (epId === currentProjectId) return;
        // The application already uses the URL hash as its router. Adding a
        // second `#step` fragment makes it part of the project id, so no
        // episode can match the active item. Staying on the canonical series
        // route updates the exact episode id while React preserves the mounted
        // editor's active pipeline step.
        window.location.assign(`#/series/${seriesId}/episode/${epId}`);
    };

    return (
        <div className="px-4 py-2">
            <div className="max-h-[180px] space-y-1 overflow-y-auto pr-1">
                {episodes.map((ep) => {
                    const isActive = ep.id === currentProjectId;
                    return (
                        <button
                            key={ep.id}
                            type="button"
                            onClick={() => handleSwitch(ep.id)}
                            title={ep.title}
                            aria-current={isActive ? "page" : undefined}
                            className={clsx(
                                "group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors duration-fast ease-out-quart focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55",
                                isActive
                                    ? "bg-primary/15 text-primary"
                                    : "text-text-secondary hover:bg-hover-bg hover:text-foreground",
                            )}
                        >
                            <span className={clsx(
                                "grid h-5 w-7 shrink-0 place-items-center rounded font-mono text-chrome-sm font-medium tabular-nums",
                                isActive
                                    ? "bg-primary/25 text-primary"
                                    : "bg-black/30 text-text-muted group-hover:text-foreground",
                            )}>
                                {ep.episode_number ?? "—"}
                            </span>
                            <span className={clsx(
                                "truncate font-sans text-body-sm",
                                isActive && "font-semibold text-primary",
                            )}>
                                {ep.title}
                            </span>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}
