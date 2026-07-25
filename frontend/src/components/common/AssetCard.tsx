"use client";

import { Image as ImageIcon, Pencil, Share2 } from "lucide-react";
import { useTranslations } from "next-intl";
import type { Character, Scene, Prop } from "@/store/projectStore";
import { primaryAssetDisplayUrl, type AssetImageKind } from "@/lib/assetImage";
import PreviewImage from "@/components/shared/preview/PreviewImage";

type AssetTab = "characters" | "scenes" | "props";

interface AssetCardProps {
  asset: Character | Scene | Prop;
  type: AssetTab;
  onEdit?: () => void;
}

const SINGULAR: Record<AssetTab, AssetImageKind> = {
  characters: "character",
  scenes: "scene",
  props: "prop",
};

export default function AssetCard({ asset, type, onEdit }: AssetCardProps) {
  const imageUrl = primaryAssetDisplayUrl(asset, SINGULAR[type]);
  const t = useTranslations("assetCard");
  // Series-shared assets get a subtle top-right badge so the user
  // knows mutations here will propagate across episodes (A1 design
  // decision). Episode-local stays unbadged — the more common case.
  const isShared = (asset as Character | Scene | Prop).source === "series";

  return (
    <div className="glass-panel rounded-xl overflow-hidden relative">
      {isShared ? (
        <span
          className="absolute right-2 top-2 z-10 inline-flex items-center gap-1 rounded-full border border-status-starred-border bg-status-starred-bg px-2 py-[2px] font-mono text-[0.625rem] font-medium uppercase tracking-[0.18em] text-status-starred-fg shadow-[inset_0_1px_0_rgba(255,255,255,0.08)] backdrop-blur-[2px]"
          title={t("seriesSharedTooltip")}
        >
          <Share2 size={10} aria-hidden="true" />
          {t("seriesSharedBadge")}
        </span>
      ) : null}
      <div className="aspect-square bg-elevated/50 flex items-center justify-center overflow-hidden">
        {imageUrl ? (
          <PreviewImage
            src={imageUrl}
            alt={asset.name}
            className="h-full w-full"
            imgClassName="object-cover"
            noLightbox
          />
        ) : (
          <ImageIcon size={32} className="text-text-muted" />
        )}
      </div>
      <div className="p-3">
        <div className="flex min-w-0 items-start gap-2">
          <h4 className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
            {asset.name}
          </h4>
          {onEdit ? (
            <button
              type="button"
              onClick={onEdit}
              className="-m-2 inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-hover-bg hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
              aria-label={t("editAsset", { name: asset.name })}
            >
              <Pencil size={15} aria-hidden="true" />
            </button>
          ) : null}
        </div>
        {asset.description && (
          <p className="text-xs text-text-secondary mt-1 line-clamp-2">{asset.description}</p>
        )}
      </div>
    </div>
  );
}
