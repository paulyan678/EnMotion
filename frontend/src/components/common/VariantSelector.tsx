"use client";

import { Check, Layers, Loader2, Star, Trash2, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { getAssetUrl } from "@/lib/utils";
import type { ImageAsset } from "@/store/projectStore";

interface VariantSelectorProps {
  asset: ImageAsset | undefined;
  currentImageUrl?: string;
  onSelect: (variantId: string) => void;
  onDelete: (variantId: string) => void;
  onFavorite?: (variantId: string, isFavorited: boolean) => void;
  onGenerate?: (batchSize: number) => void;
  isGenerating: boolean;
  generatingBatchSize?: number;
  disabled?: boolean;
  className?: string;
  aspectRatio?: string;
  showGenerationControls?: boolean;
  layout?: "stacked" | "stage";
}

export function VariantSelector({
  asset,
  currentImageUrl,
  onSelect,
  onDelete,
  onFavorite,
  onGenerate,
  isGenerating,
  generatingBatchSize: propGeneratingBatchSize,
  disabled = false,
  className = "",
  aspectRatio = "9:16",
  showGenerationControls = true,
  layout = "stacked",
}: VariantSelectorProps) {
  const t = useTranslations("assets");
  const [batchSize, setBatchSize] = useState(1);
  const [localGeneratingBatchSize, setLocalGeneratingBatchSize] = useState(1);
  const [zoomedImage, setZoomedImage] = useState<string | null>(null);
  const previousGenerating = useRef(isGenerating);

  useEffect(() => {
    if (isGenerating && !previousGenerating.current) {
      setLocalGeneratingBatchSize(batchSize);
    }
    previousGenerating.current = isGenerating;
  }, [batchSize, isGenerating]);

  const selectedVariant =
    asset?.variants?.find((variant) => variant.id === asset.selected_id)
    || asset?.variants?.[0];
  const displayUrl = getAssetUrl(selectedVariant?.url || currentImageUrl);
  const variants = asset?.variants || [];
  const generatingBatchSize =
    propGeneratingBatchSize || localGeneratingBatchSize;

  const aspectClass =
    aspectRatio === "16:9"
      ? "aspect-video"
      : aspectRatio === "1:1"
        ? "aspect-square"
        : "aspect-[9/16]";

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
          <button
            type="button"
            onClick={() => setZoomedImage(displayUrl)}
            className="group h-full w-full cursor-zoom-in focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus-ring"
            aria-label={t("clickToZoom")}
          >
            <img
              src={displayUrl}
              alt={t("selectedVariantAlt")}
              className="h-full w-full object-contain"
            />
            <span className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-glass-border bg-surface/90 px-3 py-1.5 text-xs text-foreground opacity-0 backdrop-blur-md transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
              {t("clickToZoom")}
            </span>
          </button>
        ) : (
          <div className="flex h-full min-h-[280px] items-center justify-center px-6 text-center text-sm text-text-muted">
            {t("noImageGenerated")}
          </div>
        )}

        {isGenerating ? (
          <div
            className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-overlay backdrop-blur-sm"
            aria-live="polite"
          >
            <Loader2 size={34} className="animate-spin text-primary" aria-hidden="true" />
            <span className="text-sm font-semibold text-foreground">
              {t("generatingVariants", { count: generatingBatchSize })}
            </span>
          </div>
        ) : null}
      </div>

      {showGenerationControls && onGenerate ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center rounded-lg border border-glass-border bg-elevated p-1">
            {[1, 2, 3, 4].map((size) => (
              <button
                type="button"
                key={size}
                onClick={() => setBatchSize(size)}
                disabled={disabled}
                className={`min-h-9 rounded-md px-3 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:opacity-45 ${
                  batchSize === size
                    ? "bg-primary text-primary-foreground"
                    : "text-text-secondary hover:bg-hover-bg hover:text-foreground"
                }`}
                aria-pressed={batchSize === size}
              >
                ×{size}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => onGenerate(batchSize)}
            disabled={isGenerating || disabled}
            className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:cursor-not-allowed disabled:opacity-45"
          >
            <Layers size={15} aria-hidden="true" />
            {t("generate")}
          </button>
        </div>
      ) : null}

      {variants.length > 0 ? (
        <div
          className="flex shrink-0 gap-2 overflow-x-auto pb-1"
          aria-label={t("variants")}
        >
          {variants.map((variant) => {
            const selected = variant.id === (asset?.selected_id || selectedVariant?.id);
            const favorited = Boolean(
              (variant as typeof variant & { is_favorited?: boolean }).is_favorited,
            );
            const url = getAssetUrl(variant.url);

            return (
              <div
                key={variant.id}
                className={`group/variant relative h-20 w-20 shrink-0 overflow-hidden rounded-lg border-2 bg-elevated transition-colors ${
                  selected
                    ? "border-primary ring-2 ring-primary/25"
                    : favorited
                      ? "border-status-starred-fg/50"
                      : "border-transparent hover:border-glass-border"
                }`}
              >
                <button
                  type="button"
                  onClick={() => !disabled && onSelect(variant.id)}
                  disabled={disabled}
                  className="h-full w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus-ring disabled:cursor-not-allowed"
                  aria-label={t("variantAlt")}
                  aria-pressed={selected}
                >
                  <img
                    src={url}
                    alt=""
                    loading="lazy"
                    className="h-full w-full object-cover"
                  />
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
                      onClick={() => !disabled && onFavorite(variant.id, !favorited)}
                      disabled={disabled}
                      className={`grid min-h-8 min-w-8 place-items-center rounded-md border border-glass-border bg-surface/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring ${
                        favorited ? "text-status-starred-fg" : "text-foreground"
                      }`}
                      aria-label={
                        favorited ? t("clickToUnfavorite") : t("clickToFavorite")
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
                        if (
                          !disabled
                          && window.confirm(t("confirmDeleteVariant"))
                        ) {
                          onDelete(variant.id);
                        }
                      }}
                      disabled={disabled}
                      className="grid min-h-8 min-w-8 place-items-center rounded-md border border-glass-border bg-surface/90 text-foreground hover:text-status-error-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
                      aria-label={t("deleteVariant")}
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

      {zoomedImage ? (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-overlay p-4 sm:p-8"
          role="dialog"
          aria-modal="true"
          aria-label={t("zoomedViewAlt")}
          onClick={() => setZoomedImage(null)}
        >
          <button
            type="button"
            className="absolute right-4 top-4 grid min-h-11 min-w-11 place-items-center rounded-full border border-glass-border bg-surface/90 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
            onClick={() => setZoomedImage(null)}
            aria-label={t("close")}
          >
            <X size={22} aria-hidden="true" />
          </button>
          <img
            src={zoomedImage}
            alt={t("zoomedViewAlt")}
            className="max-h-full max-w-full rounded-lg object-contain shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          />
        </div>
      ) : null}
    </div>
  );
}
