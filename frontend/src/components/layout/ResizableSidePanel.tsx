"use client";

import clsx from "clsx";
import {
    PanelLeftClose,
    PanelLeftOpen,
    PanelRightClose,
    PanelRightOpen,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type {
    KeyboardEvent as ReactKeyboardEvent,
    PointerEvent as ReactPointerEvent,
    ReactNode,
} from "react";
import { useTranslations } from "next-intl";

type PanelSide = "left" | "right";

interface ResizableSidePanelProps {
    side: PanelSide;
    storageKey: string;
    defaultWidth: number;
    minWidth: number;
    maxWidth: number;
    /** Space that must remain available to the rest of the editor. */
    minRemainingWidth: number;
    children: ReactNode;
    className?: string;
}

interface StoredPanelState {
    width: number;
    collapsed: boolean;
}

const KEYBOARD_RESIZE_STEP = 16;
const COMPACT_BREAKPOINT = 640;
const COMPACT_EDGE_GAP = 48;

export const EPISODE_EDITOR_PANEL_STORAGE_KEYS = {
    left: "enmotion:episode-editor:left-panel",
    right: "enmotion:episode-editor:right-panel",
} as const;

function readStoredState(
    storageKey: string,
    defaultWidth: number,
    minWidth: number,
    maxWidth: number,
): StoredPanelState {
    if (typeof window === "undefined") {
        return { width: defaultWidth, collapsed: false };
    }

    try {
        const raw = window.sessionStorage.getItem(storageKey);
        if (!raw) return { width: defaultWidth, collapsed: false };
        const parsed = JSON.parse(raw) as Partial<StoredPanelState>;
        const storedWidth = typeof parsed.width === "number" && Number.isFinite(parsed.width)
            ? parsed.width
            : defaultWidth;
        return {
            width: Math.round(Math.min(maxWidth, Math.max(minWidth, storedWidth))),
            collapsed: parsed.collapsed === true,
        };
    } catch {
        return { width: defaultWidth, collapsed: false };
    }
}

/**
 * A shared editor side panel with Overleaf-style collapse and drag resizing.
 * The subtree stays mounted while collapsed so in-progress panel state is not
 * discarded, while the flex item itself shrinks to zero layout width.
 */
export default function ResizableSidePanel({
    side,
    storageKey,
    defaultWidth,
    minWidth,
    maxWidth,
    minRemainingWidth,
    children,
    className,
}: ResizableSidePanelProps) {
    const t = useTranslations("common");
    const [width, setWidth] = useState(() => (
        readStoredState(storageKey, defaultWidth, minWidth, maxWidth).width
    ));
    const [collapsed, setCollapsed] = useState(() => (
        readStoredState(storageKey, defaultWidth, minWidth, maxWidth).collapsed
    ));
    const [resizing, setResizing] = useState(false);
    const [compact, setCompact] = useState(false);
    const [mobileOpen, setMobileOpen] = useState(false);
    const [availableMaxWidth, setAvailableMaxWidth] = useState(maxWidth);
    const panelRef = useRef<HTMLDivElement>(null);
    const compactRef = useRef(false);
    const dragStart = useRef({ clientX: 0, width: defaultWidth });
    const previousBodyStyle = useRef({ cursor: "", userSelect: "" });

    const measureEffectiveMaxWidth = useCallback(() => {
        const parentWidth = panelRef.current?.parentElement?.getBoundingClientRect().width
            || (typeof window !== "undefined" ? window.innerWidth : maxWidth + minRemainingWidth);
        const reservedWidth = compact ? COMPACT_EDGE_GAP : minRemainingWidth;
        return Math.max(minWidth, Math.min(maxWidth, parentWidth - reservedWidth));
    }, [compact, maxWidth, minRemainingWidth, minWidth]);

    const clampWidth = useCallback((candidate: number) => (
        Math.round(Math.min(measureEffectiveMaxWidth(), Math.max(minWidth, candidate)))
    ), [measureEffectiveMaxWidth, minWidth]);

    useEffect(() => {
        try {
            window.sessionStorage.setItem(storageKey, JSON.stringify({ width, collapsed }));
        } catch {
            // A blocked sessionStorage must not make the editor unusable.
        }
    }, [collapsed, storageKey, width]);

    useEffect(() => {
        const updateCompactLayout = () => {
            const next = window.innerWidth < COMPACT_BREAKPOINT;
            setCompact(next);
            if (next && !compactRef.current) setMobileOpen(false);
            compactRef.current = next;
        };
        updateCompactLayout();
        window.addEventListener("resize", updateCompactLayout);
        return () => window.removeEventListener("resize", updateCompactLayout);
    }, []);

    useEffect(() => {
        const clampToViewport = () => {
            const nextMaxWidth = measureEffectiveMaxWidth();
            setAvailableMaxWidth(nextMaxWidth);
            setWidth((current) => Math.round(Math.min(
                nextMaxWidth,
                Math.max(minWidth, compact ? Math.max(defaultWidth, current) : current),
            )));
        };
        clampToViewport();
        window.addEventListener("resize", clampToViewport);

        const parent = panelRef.current?.parentElement;
        const observer = typeof ResizeObserver !== "undefined" && parent
            ? new ResizeObserver(clampToViewport)
            : null;
        observer?.observe(parent!);

        return () => {
            window.removeEventListener("resize", clampToViewport);
            observer?.disconnect();
        };
    }, [compact, defaultWidth, measureEffectiveMaxWidth, minWidth]);

    useEffect(() => () => {
        if (!resizing) return;
        document.body.style.cursor = previousBodyStyle.current.cursor;
        document.body.style.userSelect = previousBodyStyle.current.userSelect;
    }, [resizing]);

    const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
        if (event.button !== 0) return;
        dragStart.current = { clientX: event.clientX, width };
        previousBodyStyle.current = {
            cursor: document.body.style.cursor,
            userSelect: document.body.style.userSelect,
        };
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
        event.currentTarget.setPointerCapture?.(event.pointerId);
        setResizing(true);
    };

    const continueResize = (event: ReactPointerEvent<HTMLDivElement>) => {
        if (!resizing) return;
        const pointerDelta = event.clientX - dragStart.current.clientX;
        const widthDelta = side === "left" ? pointerDelta : -pointerDelta;
        setWidth(clampWidth(dragStart.current.width + widthDelta));
    };

    const finishResize = (event: ReactPointerEvent<HTMLDivElement>) => {
        if (!resizing) return;
        if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
            event.currentTarget.releasePointerCapture?.(event.pointerId);
        }
        document.body.style.cursor = previousBodyStyle.current.cursor;
        document.body.style.userSelect = previousBodyStyle.current.userSelect;
        setResizing(false);
    };

    const handleSeparatorKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
        let nextWidth: number | null = null;
        if (event.key === "Home") nextWidth = minWidth;
        if (event.key === "End") nextWidth = measureEffectiveMaxWidth();
        if (event.key === "ArrowLeft") {
            nextWidth = width + (side === "left" ? -KEYBOARD_RESIZE_STEP : KEYBOARD_RESIZE_STEP);
        }
        if (event.key === "ArrowRight") {
            nextWidth = width + (side === "left" ? KEYBOARD_RESIZE_STEP : -KEYBOARD_RESIZE_STEP);
        }
        if (nextWidth === null) return;
        event.preventDefault();
        setWidth(clampWidth(nextWidth));
    };

    const collapseLabel = side === "left" ? t("collapseLeftPanel") : t("collapseRightPanel");
    const restoreLabel = side === "left" ? t("restoreLeftPanel") : t("restoreRightPanel");
    const resizeLabel = side === "left" ? t("resizeLeftPanel") : t("resizeRightPanel");
    const CollapseIcon = side === "left" ? PanelLeftClose : PanelRightClose;
    const RestoreIcon = side === "left" ? PanelLeftOpen : PanelRightOpen;
    const effectivelyCollapsed = compact ? !mobileOpen : collapsed;
    const collapse = () => {
        if (compact) setMobileOpen(false);
        else setCollapsed(true);
    };
    const restore = () => {
        if (compact) setMobileOpen(true);
        else setCollapsed(false);
    };

    return (
        <div
            ref={panelRef}
            data-side-panel={side}
            data-collapsed={effectivelyCollapsed ? "true" : "false"}
            data-compact={compact ? "true" : "false"}
            className={clsx(
                "relative h-full min-h-0 shrink-0 overflow-visible",
                !resizing && "transition-[width] duration-200 ease-out",
                compact && "absolute inset-y-0 z-[70] max-w-[calc(100vw-3rem)] shadow-[0_0_0_100vmax_var(--color-overlay),0_24px_60px_rgba(0,0,0,0.38)]",
                compact && side === "left" && "left-0",
                compact && side === "right" && "right-0",
                className,
            )}
            style={{ width: effectivelyCollapsed ? 0 : width }}
        >
            <div
                aria-hidden={effectivelyCollapsed}
                inert={effectivelyCollapsed ? true : undefined}
                className={clsx(
                    "h-full min-h-0 overflow-hidden",
                    effectivelyCollapsed && "pointer-events-none invisible absolute inset-y-0",
                    effectivelyCollapsed && side === "right" && "right-0",
                    effectivelyCollapsed && side === "left" && "left-0",
                )}
                style={{ width, visibility: effectivelyCollapsed ? "hidden" : undefined }}
            >
                {children}
            </div>

            {!effectivelyCollapsed && (
                <>
                    {!compact ? (
                        <div
                            role="separator"
                            aria-orientation="vertical"
                            aria-label={resizeLabel}
                            aria-valuemin={minWidth}
                            aria-valuemax={availableMaxWidth}
                            aria-valuenow={width}
                            tabIndex={0}
                            data-testid={`${side}-panel-resizer`}
                            onPointerDown={startResize}
                            onPointerMove={continueResize}
                            onPointerUp={finishResize}
                            onPointerCancel={finishResize}
                            onDoubleClick={() => setWidth(clampWidth(defaultWidth))}
                            onKeyDown={handleSeparatorKeyDown}
                            title={t("resizePanelHint")}
                            className={clsx(
                                "group absolute inset-y-0 z-[60] w-3 touch-none cursor-col-resize focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55",
                                side === "left" ? "-right-1.5" : "-left-1.5",
                            )}
                        >
                            <span
                                aria-hidden="true"
                                className={clsx(
                                    "absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-glass-border transition-all duration-fast group-hover:w-0.5 group-hover:bg-primary/70 group-focus-visible:w-0.5 group-focus-visible:bg-primary",
                                    resizing && "w-0.5 bg-primary shadow-[var(--glow-primary)]",
                                )}
                            />
                        </div>
                    ) : null}

                    <button
                        type="button"
                        aria-label={collapseLabel}
                        title={collapseLabel}
                        onPointerDown={(event) => event.stopPropagation()}
                        onClick={collapse}
                        className={clsx(
                            "absolute top-1/2 z-[61] grid h-10 w-6 -translate-y-1/2 place-items-center rounded-md border border-glass-border bg-surface/95 text-text-muted shadow-lg backdrop-blur-xl transition-colors duration-fast hover:border-primary/45 hover:bg-elevated hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55",
                            compact && "top-20 translate-y-0",
                            side === "left" ? "right-0 translate-x-1/2" : "left-0 -translate-x-1/2",
                        )}
                    >
                        <CollapseIcon size={14} />
                    </button>

                    {resizing && (
                        <output
                            aria-live="polite"
                            className={clsx(
                                "pointer-events-none absolute top-[calc(50%+28px)] z-[62] rounded-full border border-primary/30 bg-surface/95 px-2 py-1 font-mono text-[0.625rem] tabular-nums text-primary shadow-lg backdrop-blur-xl",
                                side === "left" ? "right-2" : "left-2",
                            )}
                        >
                            {t("panelWidth", { width })}
                        </output>
                    )}
                </>
            )}

            {effectivelyCollapsed && (
                <button
                    type="button"
                    aria-label={restoreLabel}
                    title={restoreLabel}
                    onClick={restore}
                    className={clsx(
                        "absolute top-1/2 z-[61] grid h-11 w-7 -translate-y-1/2 place-items-center border border-glass-border bg-surface/95 text-text-muted shadow-xl backdrop-blur-xl transition-colors duration-fast hover:border-primary/45 hover:bg-elevated hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55",
                        compact && "top-20 translate-y-0",
                        side === "left"
                            ? "left-0 rounded-r-lg border-l-0"
                            : "right-0 rounded-l-lg border-r-0",
                    )}
                >
                    <RestoreIcon size={15} />
                </button>
            )}
        </div>
    );
}
