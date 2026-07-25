"use client";

import {
  Check,
  Layers,
  Loader2,
  Play,
  RefreshCw,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { getAssetUrl } from "@/lib/utils";
import type { VideoTask } from "@/store/projectStore";

export type SelectableVideoVariant = Partial<VideoTask> & {
  id: string;
  url?: string;
  created_at?: number;
  is_favorited?: boolean;
};

interface VideoVariantSelectorProps {
  videos: SelectableVideoVariant[];
  selectedId?: string | null;
  onSelect?: (videoId: string) => void;
  onDelete: (videoId: string) => void;
  onFavorite?: (videoId: string, isFavorited: boolean) => void;
  onGenerate?: (duration: number) => void;
  isGenerating: boolean;
  className?: string;
  aspectRatio?: string;
  disabledReason?: string;
  showGenerationControls?: boolean;
  layout?: "stacked" | "stage";
  fallbackImageUrl?: string;
}

export function VideoVariantSelector({
  videos = [],
  selectedId,
  onSelect,
  onDelete,
  onFavorite,
  onGenerate,
  isGenerating,
  className = "",
  aspectRatio = "16:9",
  disabledReason,
  showGenerationControls = true,
  layout = "stacked",
  fallbackImageUrl,
}: VideoVariantSelectorProps) {
  const t = useTranslations("assets");
  const [duration, setDuration] = useState(5);
  const [localSelectedVideoId, setLocalSelectedVideoId] =
    useState<string | null>(null);
  const selectedVideoId = selectedId ?? localSelectedVideoId;
  const selectedVideo =
    videos.find((video) => video.id === selectedVideoId)
    || [...videos].sort((left, right) => {
      const completedRank =
        Number(right.status === "completed") - Number(left.status === "completed");
      return (
        completedRank
        || Number(right.created_at || 0) - Number(left.created_at || 0)
      );
    })[0];
  const effectiveSelectedId = selectedVideo?.id ?? null;
  const displayUrl = getAssetUrl(
    selectedVideo?.video_url || selectedVideo?.url,
  );
  const aspectClass =
    aspectRatio === "9:16"
      ? "aspect-[9/16]"
      : aspectRatio === "1:1"
        ? "aspect-square"
        : "aspect-video";

  return (
    <div
      className={`flex min-h-0 flex-col gap-3 ${
        layout === "stage" ? "h-full" : ""
      } ${className}`}
    >
      <div
        className={`relative min-h-0 overflow-hidden rounded-xl border border-glass-border bg-elevated ${
          layout === "stage" ? "min-h-[320px] flex-1" : aspectClass
        }`}
      >
        {displayUrl ? (
          <video
            key={selectedVideo?.id}
            src={displayUrl}
            className="h-full w-full object-contain"
            controls
            loop
            playsInline
          />
        ) : (
          <div className="flex h-full min-h-[280px] flex-col items-center justify-center gap-2 px-6 text-center text-sm text-text-muted">
            {isGenerating ? (
              <>
                <Loader2 size={30} className="animate-spin text-primary" aria-hidden="true" />
                <span>{t("generatingVideo")}</span>
              </>
            ) : (
              <>
                <Play size={30} className="opacity-40" aria-hidden="true" />
                <span>{t("noVideoGenerated")}</span>
              </>
            )}
          </div>
        )}

        {isGenerating && displayUrl ? (
          <div
            className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3 bg-overlay backdrop-blur-sm"
            aria-live="polite"
          >
            <Loader2 size={34} className="animate-spin text-primary" aria-hidden="true" />
            <span className="text-sm font-semibold text-foreground">
              {t("generatingVideo")}
            </span>
          </div>
        ) : null}
      </div>

      {showGenerationControls && onGenerate ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 rounded-lg border border-glass-border bg-elevated p-1">
              <span className="px-2 text-xs text-text-secondary">
                {t("duration")}
              </span>
              <button
                type="button"
                onClick={() => setDuration(5)}
                className="min-h-9 rounded-md bg-primary px-3 text-xs font-semibold text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
                aria-pressed="true"
              >
                5秒
              </button>
            </div>
            <button
              type="button"
              onClick={() => onGenerate(duration)}
              disabled={isGenerating || Boolean(disabledReason)}
              title={disabledReason}
              className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:cursor-not-allowed disabled:opacity-45"
            >
              <Layers size={15} aria-hidden="true" />
              {isGenerating ? t("generating") : t("generateVideo")}
            </button>
          </div>
          {disabledReason ? (
            <p className="text-xs text-status-pending-fg">{disabledReason}</p>
          ) : null}
        </>
      ) : null}

      {videos.length > 0 ? (
        <div
          className="flex shrink-0 gap-2 overflow-x-auto pb-1"
          aria-label={t("videoVariants")}
        >
          {videos.map((video) => {
            const selected = video.id === effectiveSelectedId;
            const favorited = Boolean(video.is_starred || video.is_favorited);
            const thumbUrl = getAssetUrl(video.image_url || fallbackImageUrl);

            return (
              <div
                key={video.id}
                className={`group/variant relative h-20 w-28 shrink-0 overflow-hidden rounded-lg border-2 bg-elevated transition-colors ${
                  selected
                    ? "border-primary ring-2 ring-primary/25"
                    : favorited
                      ? "border-status-starred-fg/50"
                      : "border-transparent hover:border-glass-border"
                }`}
              >
                <button
                  type="button"
                  onClick={() => {
                    setLocalSelectedVideoId(video.id);
                    onSelect?.(video.id);
                  }}
                  className="h-full w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus-ring"
                  aria-label={t("selectVideoVariant")}
                  aria-pressed={selected}
                >
                  {thumbUrl ? (
                    <img
                      src={thumbUrl}
                      alt=""
                      className="h-full w-full object-cover opacity-80"
                    />
                  ) : (
                    <span className="flex h-full w-full items-center justify-center">
                      <Play size={20} className="text-foreground/70" aria-hidden="true" />
                    </span>
                  )}
                  <span className="pointer-events-none absolute inset-0 grid place-items-center">
                    {video.status === "processing" || video.status === "pending" ? (
                      <RefreshCw size={18} className="animate-spin text-white" aria-hidden="true" />
                    ) : video.status === "failed" ? (
                      <X size={18} className="text-status-error-fg" aria-hidden="true" />
                    ) : (
                      <Play size={18} className="text-white drop-shadow-md" aria-hidden="true" />
                    )}
                  </span>
                </button>

                {selected ? (
                  <span className="pointer-events-none absolute left-1 top-1 rounded-full bg-primary p-0.5 text-primary-foreground">
                    <Check size={10} aria-hidden="true" />
                  </span>
                ) : null}

                <div className="absolute bottom-1 right-1 flex gap-1 opacity-0 transition-opacity group-hover/variant:opacity-100 group-focus-within/variant:opacity-100">
                  {onFavorite ? (
                    <button
                      type="button"
                      onClick={() => onFavorite(video.id, !favorited)}
                      className={`grid min-h-8 min-w-8 place-items-center rounded-md border border-glass-border bg-surface/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring ${
                        favorited ? "text-status-starred-fg" : "text-foreground"
                      }`}
                      aria-label={
                        favorited
                          ? t("removeVideoFavorite")
                          : t("addVideoFavorite")
                      }
                      aria-pressed={favorited}
                    >
                      <Star
                        size={13}
                        className={favorited ? "fill-current" : ""}
                        aria-hidden="true"
                      />
                    </button>
                  ) : null}
                  {!favorited ? (
                    <button
                      type="button"
                      onClick={() => {
                        if (window.confirm(t("confirmDeleteVideo"))) {
                          onDelete(video.id);
                        }
                      }}
                      className="grid min-h-8 min-w-8 place-items-center rounded-md border border-glass-border bg-surface/90 text-foreground hover:text-status-error-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
                      aria-label={t("deleteVideo")}
                    >
                      <Trash2 size={13} aria-hidden="true" />
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
