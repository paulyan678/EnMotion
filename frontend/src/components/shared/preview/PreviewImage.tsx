"use client";
/**
 * PreviewImage — drop-in replacement for raw `<img>` across the workbench
 * (Issue 14). Three layers of value over bare `<img>`:
 *
 *   1. URL resolution — auto-routes raw paths through getAssetUrl() so
 *      callers never accidentally render a relative URL the browser will
 *      resolve to the dev server origin (the original ShotCard:267 bug).
 *
 *   2. Error fallback — onError swaps to a sized-adaptive panel:
 *        - micro (≤ 40px any dim): just ⚠ icon, click to retry
 *        - mid  (≤ 120px): ⚠ + small Retry link
 *        - large (> 120px): ⚠ + label + Retry + Copy URL buttons
 *      One automatic retry first (cache-bust ?retry=1) — most "broken"
 *      images are transient cache / OSS-signature blips that retry fixes.
 *      Second failure stays on the panel with a manual retry and safe,
 *      path-free diagnostics.
 *
 *   3. Click-to-lightbox — hover reveals a 🔍 button (top-right) that
 *      opens the singleton LightboxProvider. If wrapped in a
 *      LightboxGroupRegistrar, lightbox shows ← → prev/next.
 *
 * FUTURE SCANNING POINTS (other modules still using raw <img>, replace
 * opportunistically next time they're touched):
 *   - frontend/src/components/modules/PropertiesPanel.tsx
 *   - frontend/src/components/modules/VideoCreator.tsx
 *   - frontend/src/components/modules/CharacterWorkbench.tsx
 *   - frontend/src/components/modules/ConsistencyVault.tsx
 *   - frontend/src/components/canvas/* (storyboard frame thumbnails)
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, RefreshCw, Maximize2, Copy, Check } from "lucide-react";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import { durableMediaReference, getAssetUrl } from "@/lib/utils";
import { useLightbox, type LightboxItem } from "./LightboxProvider";

export interface PreviewImageProps {
    /** Raw URL — relative paths auto-resolve via getAssetUrl. Empty/undefined
     *  renders the placeholder (no fallback panel, just empty state). */
    src?: string;
    alt?: string;
    className?: string;
    /** Classes applied to the underlying image rather than its wrapper. */
    imgClassName?: string;
    /** Disable click-to-lightbox + 🔍 button (e.g. cast avatars in chip bar
     *  where lightbox is overkill for tiny 16px chips). */
    noLightbox?: boolean;
    /** Optional: when the parent has wrapped multiple PreviewImages in a
     *  LightboxGroupRegistrar, supply this so click opens the group at the
     *  right index instead of as a singleton. */
    groupId?: string;
    groupIndex?: number;
    /** Force the magnify button visible even without hover (e.g. on touch
     *  devices). Default: hover-only. */
    alwaysShowMagnify?: boolean;
    /** Whole-thumb click opens lightbox (in addition to the 🔍 button).
     *  Default false — most callers own the click for "select" / "play".
     *  Turn this on for read-only thumbs (e.g. TaskQueuePanel queue rows)
     *  where there's no other click semantic. */
    clickToLightbox?: boolean;
    /** Render the placeholder slot (no src) without the error styling —
     *  e.g. "no T2I yet" empty state should be neutral, not red. */
    placeholder?: React.ReactNode;
    /** Safe view identifier included in diagnostics. Never include an asset
     *  path, signed URL, token, user content, or credential here. */
    diagnosticContext?: string;
    /** Immutable responsive sources. `src` remains the original fallback. */
    responsiveSources?: readonly {
        src: string;
        width: number;
        type?: string;
    }[];
    /** Stable media identity plus content revision, never a signed query. */
    mediaKey?: string;
    intrinsicWidth?: number;
    intrinsicHeight?: number;
    sizes?: string;
    loading?: "eager" | "lazy";
    fetchPriority?: "high" | "low" | "auto";
    /** Known layout bucket avoids a ResizeObserver for every grid card. */
    sizeBucket?: "micro" | "mid" | "large";
    /** Optional load callback for callers that derive dimensions or other
     *  presentation metadata from the decoded image. */
    onLoad?: React.ReactEventHandler<HTMLImageElement>;
}

function mediaDiagnostic(url: string, context?: string) {
    let transport: "authenticated-file" | "remote" | "blob" | "embedded" | "unknown" = "unknown";
    let extension = "unknown";
    try {
        if (url.startsWith("data:")) transport = "embedded";
        else if (url.startsWith("blob:")) transport = "blob";
        else {
            const parsed = new URL(url, typeof window !== "undefined" ? window.location.origin : "http://enmotion.invalid");
            transport = parsed.pathname.includes("/files/") ? "authenticated-file" : "remote";
            const match = parsed.pathname.match(/\.([a-z0-9]{1,8})$/i);
            extension = match?.[1]?.toLowerCase() ?? "unknown";
        }
    } catch {
        // Keep diagnostics intentionally coarse when a URL cannot be parsed.
    }
    return { context: context || "unspecified", transport, extension };
}

export default function PreviewImage(props: PreviewImageProps) {
    return <PreviewImageForSource {...props} />;
}

function PreviewImageForSource({
    src, alt, className, imgClassName, noLightbox = false,
    groupId, groupIndex, alwaysShowMagnify = false,
    clickToLightbox = false, placeholder, diagnosticContext, onLoad,
    responsiveSources = [], mediaKey, intrinsicWidth, intrinsicHeight,
    sizes, loading = "lazy", fetchPriority = "auto",
    sizeBucket: explicitSizeBucket,
}: PreviewImageProps) {
    const t = useTranslations("preview");
    const { open, openInGroup } = useLightbox();
    const [errored, setErrored] = useState(false);
    const [loaded, setLoaded] = useState(false);
    const [retryNonce, setRetryNonce] = useState(0);
    const [hasRetriedOnce, setHasRetriedOnce] = useState(false);
    const [copied, setCopied] = useState(false);
    const hasLoggedFailure = useRef(false);
    const wrapperRef = useRef<HTMLDivElement | null>(null);
    const sourceIdentity = mediaKey || durableMediaReference(src);
    const previousSourceIdentity = useRef(sourceIdentity);
    /** Sized-adaptive bucket; recomputed on resize via ResizeObserver. */
    const [measuredSizeBucket, setMeasuredSizeBucket] = useState<"micro" | "mid" | "large">(
        explicitSizeBucket ?? "large",
    );
    const sizeBucket = explicitSizeBucket ?? measuredSizeBucket;

    useEffect(() => {
        if (previousSourceIdentity.current === sourceIdentity) return;
        previousSourceIdentity.current = sourceIdentity;
        setErrored(false);
        setLoaded(false);
        setRetryNonce(0);
        setHasRetriedOnce(false);
        setCopied(false);
        hasLoggedFailure.current = false;
    }, [sourceIdentity]);

    useEffect(() => {
        if (explicitSizeBucket) return;
        const el = wrapperRef.current;
        if (!el) return;
        const measure = () => {
            const { width, height } = el.getBoundingClientRect();
            const min = Math.min(width, height);
            if (min <= 40) setMeasuredSizeBucket("micro");
            else if (min <= 120) setMeasuredSizeBucket("mid");
            else setMeasuredSizeBucket("large");
        };
        measure();
        if (typeof ResizeObserver === "undefined") return;
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [explicitSizeBucket]);

    const resolved = getAssetUrl(src);
    const responsiveFallback = useMemo(() => {
        if (responsiveSources.length === 0) return "";
        const ordered = [...responsiveSources]
            .filter((source) => source.width > 0 && !!source.src)
            .sort((left, right) => left.width - right.width);
        const cardSized = ordered.find((source) => source.width >= 384)
            ?? ordered.at(-1);
        return cardSized ? getAssetUrl(cardSized.src) : "";
    }, [responsiveSources]);
    const canCacheBust = (() => {
        if (!resolved || typeof window === "undefined") return false;
        try {
            return new URL(resolved, window.location.origin).pathname.includes("/files/");
        } catch {
            return false;
        }
    })();
    /** Append retry-bust to bypass browser/OSS cache on retry. */
    const displaySrc = resolved
        ? (retryNonce > 0 && canCacheBust
            ? `${resolved}${resolved.includes("?") ? "&" : "?"}__r=${retryNonce}`
            : responsiveFallback || resolved)
        : "";
    const responsiveSrcSet = useMemo(() => {
        if (retryNonce > 0) return "";
        return responsiveSources
            .filter((source) => source.width > 0 && !!source.src)
            .sort((left, right) => left.width - right.width)
            .map((source) => `${getAssetUrl(source.src)} ${source.width}w`)
            .join(", ");
    }, [responsiveSources, retryNonce]);
    const responsiveType = responsiveSources.find((source) => source.type)?.type
        ?? "image/webp";

    const handleError = () => {
        // First failure → silent automatic retry once (cache hiccup / signature blip).
        // Second failure → surface fallback panel.
        if (!hasRetriedOnce && canCacheBust) {
            setLoaded(false);
            setHasRetriedOnce(true);
            setRetryNonce(n => n + 1);
        } else {
            setLoaded(false);
            setErrored(true);
            if (!hasLoggedFailure.current) {
                hasLoggedFailure.current = true;
                console.warn("[MediaPreview] Image failed to load", mediaDiagnostic(resolved, diagnosticContext));
            }
        }
    };

    const handleManualRetry = () => {
        setLoaded(false);
        setErrored(false);
        hasLoggedFailure.current = false;
        setRetryNonce(n => n + 1);
    };

    const handleCopyUrl = async () => {
        if (!resolved) return;
        try {
            await navigator.clipboard.writeText(resolved);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked */
        }
    };

    const handleOpenLightbox = (e?: React.MouseEvent) => {
        if (e) e.stopPropagation();
        if (noLightbox || !src) return;
        const item: LightboxItem = { src, alt, kind: "image" };
        if (groupId && typeof groupIndex === "number") openInGroup(groupId, groupIndex);
        else open(item);
    };

    if (!src) {
        return (
            <div ref={wrapperRef} className={clsx("relative overflow-hidden", className)}>
                {placeholder ?? null}
            </div>
        );
    }

    const clickable = clickToLightbox && !noLightbox && !errored && !!src;
    const hasExplicitObjectFit = imgClassName
        ?.split(/\s+/)
        .some((name) => /^(?:[^:]+:)*object-(?:contain|cover|fill|none|scale-down)$/.test(name));

    return (
        <div
            ref={wrapperRef}
            className={clsx(
                "group/preview relative overflow-hidden",
                clickable && "cursor-zoom-in",
                className,
            )}
            onClick={clickable ? handleOpenLightbox : undefined}
            role={clickable ? "button" : undefined}
            tabIndex={clickable ? 0 : undefined}
            onKeyDown={clickable ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleOpenLightbox();
                }
            } : undefined}
            aria-label={clickable ? t("zoom") : undefined}
        >
            {!errored ? (
                <>
                    {!loaded ? (
                        <div
                            role="status"
                            aria-label={t("loading")}
                            className="absolute inset-0 z-[1] grid place-items-center bg-elevated"
                        >
                            <RefreshCw
                                size={sizeBucket === "micro" ? 11 : 18}
                                aria-hidden="true"
                                className="animate-spin text-text-muted"
                            />
                        </div>
                    ) : null}
                    <picture className="contents">
                        {responsiveSrcSet ? (
                            <source
                                type={responsiveType}
                                srcSet={responsiveSrcSet}
                                sizes={sizes}
                            />
                        ) : null}
                        <img
                            src={displaySrc}
                            alt={alt ?? ""}
                            width={intrinsicWidth}
                            height={intrinsicHeight}
                            loading={loading}
                            fetchPriority={fetchPriority}
                            decoding="async"
                            onLoad={(event) => {
                                setLoaded(true);
                                onLoad?.(event);
                            }}
                            onError={handleError}
                            className={clsx(
                                "h-full w-full transition-opacity duration-fast",
                                loaded ? "opacity-100" : "opacity-0",
                                !hasExplicitObjectFit && "object-cover",
                                imgClassName,
                            )}
                        />
                    </picture>
                    {!noLightbox && sizeBucket !== "micro" ? (
                        <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); handleOpenLightbox(); }}
                            aria-label={t("zoom")}
                            title={t("zoom")}
                            className={clsx(
                                "absolute right-1 top-1 grid h-6 w-6 place-items-center rounded bg-black/55 text-foreground backdrop-blur transition-opacity duration-fast ease-out-quart hover:bg-black/75 focus-visible:outline-none focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-primary/55",
                                alwaysShowMagnify
                                    ? "opacity-100"
                                    : "opacity-0 group-hover/preview:opacity-100",
                            )}
                        >
                            <Maximize2 size={11} aria-hidden="true" />
                        </button>
                    ) : null}
                </>
            ) : (
                <FallbackPanel
                    sizeBucket={sizeBucket}
                    url={resolved}
                    onRetry={handleManualRetry}
                    onCopyUrl={handleCopyUrl}
                    copied={copied}
                />
            )}
        </div>
    );
}

interface FallbackPanelProps {
    sizeBucket: "micro" | "mid" | "large";
    url: string;
    onRetry: () => void;
    onCopyUrl: () => void;
    copied: boolean;
}

function FallbackPanel({ sizeBucket, url, onRetry, onCopyUrl, copied }: FallbackPanelProps) {
    const t = useTranslations("preview");
    // Micro (≤ 40px) — just ⚠ icon, whole panel = retry on click
    if (sizeBucket === "micro") {
        return (
            <button
                type="button"
                onClick={(event) => { event.stopPropagation(); onRetry(); }}
                title={t("imgLoadFailedRetry")}
                aria-label={t("imgLoadFailedRetry")}
                className="grid h-full w-full place-items-center bg-status-failed-bg text-status-failed-fg transition-colors duration-fast ease-out-quart hover:bg-status-failed-bg/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-border"
            >
                <AlertTriangle size={12} aria-hidden="true" />
            </button>
        );
    }

    // Mid (≤ 120px) — ⚠ icon + small Retry link
    if (sizeBucket === "mid") {
        return (
            <div
                className="grid h-full w-full place-items-center gap-1 bg-status-failed-bg p-2 text-status-failed-fg"
            >
                <AlertTriangle size={16} aria-hidden="true" />
                <button
                    type="button"
                    onClick={(event) => { event.stopPropagation(); onRetry(); }}
                    className="inline-flex items-center gap-0.5 font-mono text-chrome-sm font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-border"
                >
                    <RefreshCw size={10} aria-hidden="true" />
                    {t("retry")}
                </button>
            </div>
        );
    }

    // Large (> 120px) — full panel with retry + explicit copy action.
    // The address is never rendered or included in a tooltip.
    return (
        <div
            role="alert"
            className="grid h-full w-full place-items-center gap-2 bg-status-failed-bg p-4 text-center text-status-failed-fg"
        >
            <AlertTriangle size={22} aria-hidden="true" />
            <div className="space-y-1">
                <p className="font-sans text-body-sm font-medium">{t("imgLoadFailed")}</p>
                <p
                    className="max-w-[26rem] truncate font-mono text-chrome-sm text-status-failed-fg/75"
                >
                    {t("safeDiagnosticHint")}
                </p>
            </div>
            <div className="flex items-center gap-1.5 pt-1">
                <button
                    type="button"
                    onClick={(event) => { event.stopPropagation(); onRetry(); }}
                    className="inline-flex min-h-[28px] items-center gap-1 rounded border border-status-failed-border bg-status-failed-bg px-2.5 py-1 font-mono text-chrome font-medium text-status-failed-fg transition-colors duration-fast ease-out-quart hover:bg-status-failed-fg/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-border"
                >
                    <RefreshCw size={11} aria-hidden="true" />
                    {t("retry")}
                </button>
                <button
                    type="button"
                    onClick={(event) => { event.stopPropagation(); onCopyUrl(); }}
                    className="inline-flex min-h-[28px] items-center gap-1 rounded border border-glass-border bg-black/30 px-2.5 py-1 font-mono text-chrome font-medium text-text-secondary transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                >
                    {copied ? <Check size={11} /> : <Copy size={11} />}
                    {copied ? t("copied") : t("copyUrl")}
                </button>
            </div>
        </div>
    );
}
