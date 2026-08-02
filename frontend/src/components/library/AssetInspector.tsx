"use client";

import { useState, useEffect, useRef } from "react";
import { useLocale, useTranslations } from "next-intl";
import { X, Download, Loader2, Globe, Pencil, Trash2 } from "lucide-react";
import type { Character, Scene, Prop, ImageAsset } from "@/store/projectStore";
import { primaryAssetImage, primaryAssetImageUrl, type AssetImageKind } from "@/lib/assetImage";
import { notifyAssetLibraryChanged } from "@/lib/assetLibrarySync";
import { api } from "@/lib/api";
import { apiFetch } from "@/lib/httpClient";
import { getAssetUrl } from "@/lib/utils";
import { toast } from "@/store/toastStore";
import { coverGradient, GRAIN_URL } from "@/lib/atelierCover";
import { useNow } from "@/lib/useNow";
import { useModelDisplayName } from "@/lib/useModelDisplayName";
import PreviewImage from "@/components/shared/preview/PreviewImage";
import FavoriteButton from "@/components/assets/FavoriteButton";

type AssetTab = "characters" | "scenes" | "props";

// 资产类型 → 后端单数 type（生成端点用）。
const SINGULAR_TYPE: Record<AssetTab, AssetImageKind> = {
  characters: "character",
  scenes: "scene",
  props: "prop",
};

interface AssetInspectorProps {
  asset: Character | Scene | Prop;
  type: AssetTab;
  sourceName: string;
  /** Library list key (`series-…` / `project-…` / `global`). */
  sourceId: string;
  /** Canonical owner used by editing and promotion actions. */
  sourceKind: "series" | "project" | "global";
  /** Backend-derived count for this exact canonical owner/type/id identity. */
  usageCount: number | null;
  starred: boolean;
  favoritePending?: boolean;
  onClose: () => void;
  onToggleStar: () => void;
  /** Open the shared asset editor for this canonical library asset. */
  onEdit: () => void;
  /** Delete only after the canonical owner confirms the server mutation. */
  onDelete: () => void;
  deleting: boolean;
}

/** Normalize every asset type through the same canonical image resolver. */
function primaryImageAsset(asset: Character | Scene | Prop, type: AssetTab): ImageAsset | undefined {
  return primaryAssetImage(asset, SINGULAR_TYPE[type]);
}

const MIME_EXT: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
  "image/gif": "gif",
  "image/avif": "avif",
  "image/svg+xml": "svg",
};

/** 下载文件名扩展名：优先取 URL 路径后缀（剥掉 query/签名），否则回退到 blob content-type，默认 png。 */
function downloadExt(url: string, contentType?: string): string {
  try {
    const path = new URL(url, window.location.origin).pathname;
    const m = path.match(/\.([a-z0-9]+)$/i);
    if (m) return m[1].toLowerCase();
  } catch {
    // URL 解析失败时退回 content-type / 默认
  }
  const fromType = contentType?.split(";")[0].trim().toLowerCase();
  if (fromType && MIME_EXT[fromType]) return MIME_EXT[fromType];
  return "png";
}

/**
 * 资产库右侧详情抽屉（Line B "Luminous Atelier"）。
 * 库专用，不复用共享 AssetCard。展示选中资产的 hero + 变体条 + 元数据 + 动作。
 * 元数据数据驱动（metaRows）：SEED/MODEL/SIZE 当前数据模型未存（变体仅
 * id/url/created_at/prompt_used），故读为 undefined → 不渲染；后端补字段后 UI 零改自动出现。
 * 动作区保留查看上下文动作；所有编辑与生成统一进入共享资产编辑器。
 */
export default function AssetInspector(props: AssetInspectorProps) {
  const imageAsset = primaryImageAsset(props.asset, props.type);
  const defaultId = imageAsset?.selected_id ?? imageAsset?.variants?.[0]?.id ?? "none";
  const inspectorKey = `${props.sourceKind}:${props.sourceId}:${props.type}:${props.asset.id}:${defaultId}`;

  return <AssetInspectorForAsset key={inspectorKey} {...props} />;
}

function AssetInspectorForAsset({
  asset,
  type,
  sourceName,
  sourceId,
  sourceKind,
  usageCount,
  starred,
  favoritePending = false,
  onClose,
  onToggleStar,
  onEdit,
  onDelete,
  deleting,
}: AssetInspectorProps) {
  const t = useTranslations("library");
  const modelDisplayName = useModelDisplayName();
  const locale = useLocale();
  const now = useNow();
  const TYPE_LABEL: Record<AssetTab, string> = {
    characters: t("characterLabel"),
    scenes: t("sceneLabel"),
    props: t("propLabel"),
  };
  // created_at 来自 time.time()（秒）；容错已是毫秒的情况。相对时间标签走 i18n。
  const timeAgo = (ts?: number): string => {
    if (!ts) return "—";
    const tsMs = ts > 1e12 ? ts : ts * 1000;
    const days = Math.floor((now - tsMs) / 86_400_000);
    if (days <= 0) return t("timeToday");
    if (days === 1) return t("timeYesterday");
    if (days < 30) return t("timeDaysAgo", { days });
    return t("timeMonthsAgo", { months: Math.floor(days / 30) });
  };
  const imageAsset = primaryImageAsset(asset, type);
  const variants = imageAsset?.variants ?? [];
  const defaultId = imageAsset?.selected_id ?? variants[0]?.id ?? null;
  const [activeVariantId, setActiveVariantId] = useState<string | null>(defaultId);
  const [promoting, setPromoting] = useState(false);

  // a11y：抽屉打开时把焦点移入面板、Escape 关闭、关闭后还原焦点（非模态，不做 focus trap）。
  const asideRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    asideRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus?.();
    };
  }, []);

  const activeVariant = variants.find((v) => v.id === activeVariantId) ?? variants[0];
  const heroUrl = getAssetUrl(
    activeVariant?.url ?? primaryAssetImageUrl(asset, SINGULAR_TYPE[type])
  );
  // 元数据行（数据驱动）：先放现有四项，再在字段存在时追加 SEED/MODEL/SIZE。
  // 后端 TODO：当前 ImageVariant 仅 id/url/created_at/prompt_used，资产无 seed/model/size，
  // 故 assetMeta.* 读为 undefined → 不 push → 不渲染。后端补字段后此处零改自动出现。
  const assetMeta = asset as Partial<{ seed: number | string; model: string; size: string }>;
  const metaRows: { label: string; value: string }[] = [
    { label: t("metaType"), value: TYPE_LABEL[type] },
    { label: t("metaSource"), value: sourceName },
    {
      label: t("usage"),
      value: usageCount === null
        ? t("usageDataFailed")
        : usageCount === 0
        ? t("neverUsed")
        : usageCount === 1
          ? t("usedOnce")
          : t("usedTimes", { count: usageCount }),
    },
    { label: t("metaVariant"), value: `${variants.length}` },
    { label: t("metaCreated"), value: timeAgo(activeVariant?.created_at) },
  ];
  if (assetMeta.seed != null) metaRows.push({ label: t("metaSeed"), value: String(assetMeta.seed) });
  if (assetMeta.model) metaRows.push({ label: t("metaModel"), value: modelDisplayName(assetMeta.model) });
  if (assetMeta.size) metaRows.push({ label: t("metaSize"), value: assetMeta.size });

  const handleDownload = async () => {
    if (!heroUrl) return;
    const fileBase = asset.name || "asset";
    try {
      const res = await apiFetch(heroUrl);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const ext = downloadExt(heroUrl, blob.type);
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = `${fileBase}.${ext}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch {
      // 跨域(CORS)/网络失败：download 属性对跨域 URL 无效，退回到新标签打开。
      window.open(heroUrl, "_blank", "noopener,noreferrer");
    }
  };

  // 提升到全局：把 project/series 来源资产 deep-copy 进全局共享池（global 来源不显示该按钮）。
  // 成功后 toast 并回调父层刷新（新入池资产即出现在「全局 / 共享」分组）。
  const handlePromote = async () => {
    if (sourceKind === "global" || promoting) return;
    // 父层传入的是列表 key（`project-<id>` / `series-<id>`）；promote API 需要裸 id。
    const rawSourceId = sourceId.replace(/^(project|series)-/, "");
    setPromoting(true);
    try {
      const promoted = await api.promoteAssetToLibrary(sourceKind, rawSourceId, SINGULAR_TYPE[type], asset.id);
      notifyAssetLibraryChanged({
        source: "global",
        assetType: SINGULAR_TYPE[type],
        assetId: typeof promoted?.id === "string" ? promoted.id : undefined,
      });
      toast.success(t("promoteSuccess"), { body: t("promoteSuccessBody", { name: asset.name }) });
    } catch (e) {
      const msg = e instanceof Error ? e.message : t("promoteFailed");
      toast.error(t("promoteFailed"), locale === "zh" ? undefined : { body: msg });
    } finally {
      setPromoting(false);
    }
  };

  return (
    <aside
      ref={asideRef}
      tabIndex={-1}
      className="fixed inset-0 z-50 flex h-full w-full flex-shrink-0 flex-col overflow-y-auto border-l border-glass-border bg-surface shadow-2xl atelier-reveal focus:outline-none md:relative md:inset-auto md:z-50 md:w-[340px]"
      aria-label={t("inspectorAria")}
    >
      {/* Hero — 磨砂铺底 + object-contain：三视图/横竖混杂的资产完整展示不裁切（避免裁头）。 */}
      <div className="relative aspect-[3/4] bg-surface-inset overflow-hidden flex-shrink-0">
        {heroUrl ? (
          <PreviewImage
            src={heroUrl}
            alt={asset.name}
            className="relative h-full w-full bg-[radial-gradient(circle_at_center,var(--color-bg-elevated),var(--color-bg-inset))]"
            imgClassName="object-contain"
            diagnosticContext="asset-library-inspector"
            noLightbox
          />
        ) : (
          // 无图像：确定性渐变封面 + 颗粒，替代发灰占位图标。
          <>
            <div className="absolute inset-0" style={{ background: coverGradient(asset.id) }} aria-hidden="true" />
            <div
              className="absolute inset-0 mix-blend-overlay opacity-60"
              style={{ backgroundImage: GRAIN_URL }}
              aria-hidden="true"
            />
            <div className="relative w-full h-full grid place-items-center p-6 text-center">
              <span className="font-display atelier-display text-2xl font-semibold text-foreground tracking-tight">
                {asset.name}
              </span>
            </div>
          </>
        )}
        {/* amber halation overlay — only on starred (atelier signature; amber = starred). */}
        {starred && (
          <div
            className="pointer-events-none absolute inset-0 shadow-[inset_0_0_60px_-10px_var(--color-status-starred-bg)]"
            aria-hidden="true"
          />
        )}
        <FavoriteButton
          pressed={starred}
          pending={favoritePending}
          onChange={() => onToggleStar()}
          variant="labeled"
          className="absolute left-3 top-3"
        />
        <button
          type="button"
          onClick={onClose}
          aria-label={t("closeInspector")}
          className="absolute top-3 right-3 w-8 h-8 rounded-full grid place-items-center bg-black/50 backdrop-blur-md text-foreground hover:bg-black/70 transition-colors"
        >
          <X size={15} />
        </button>
      </div>

      <div className="p-5 flex flex-col gap-5">
        <div>
          <div className="font-display atelier-display text-xl font-semibold text-foreground tracking-tight">
            {asset.name}
          </div>
          <div className="font-mono text-[0.59375rem] text-text-muted tracking-[0.06em] uppercase mt-1.5">
            {TYPE_LABEL[type]} · {sourceName} · {t("variantCount", { count: variants.length })}
          </div>
        </div>

        {/* Variant strip */}
        {variants.length > 1 && (
          <div>
            <div className="font-mono text-[0.5625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary mb-2.5">
              {t("variantsSection")}
            </div>
            <div className="grid grid-cols-4 gap-2">
              {variants.map((v) => {
                const on = v.id === activeVariant?.id;
                return (
                  <button
                    key={v.id}
                    type="button"
                    onClick={() => setActiveVariantId(v.id)}
                    aria-current={on ? "true" : undefined}
                    className={`relative aspect-square rounded-md overflow-hidden transition-transform hover:-translate-y-0.5 ${
                      on ? "ring-2 ring-primary" : "ring-1 ring-glass-border"
                    }`}
                  >
                    <img
                      src={getAssetUrl(v.url)}
                      alt={t("variantAlt")}
                      loading="lazy"
                      decoding="async"
                      className="w-full h-full object-cover"
                    />
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Metadata */}
        <div>
          <div className="font-mono text-[0.5625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary mb-2.5">
            {t("metadataSection")}
          </div>
          <div className="flex flex-col">
            {metaRows.map((row) => (
              <div
                key={row.label}
                className="flex justify-between items-center py-2 border-b border-glass-border last:border-b-0 text-[0.8125rem]"
              >
                <span className="font-mono text-[0.625rem] text-text-muted tracking-[0.04em]">{row.label}</span>
                <span className="text-foreground font-medium">{row.value}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={onEdit}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-on-accent text-sm font-semibold hover:bg-primary-hover transition-colors"
          >
            <Pencil size={15} />
            {t("editAsset")}
          </button>
          {/* 提升到全局：project/series 来源可用；global 来源隐藏（无需自我提升）。 */}
          {sourceKind !== "global" && (
            <button
              type="button"
              onClick={handlePromote}
              disabled={promoting}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-surface-inset border border-glass-border text-foreground text-sm font-medium hover:bg-hover-bg transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {promoting ? <Loader2 size={15} className="animate-spin" /> : <Globe size={15} />}
              {promoting ? t("promoting") : t("promoteToGlobal")}
            </button>
          )}
          {/* 下载：v1 实做 */}
          <button
            type="button"
            onClick={handleDownload}
            disabled={!heroUrl}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-surface-inset border border-glass-border text-foreground text-sm font-medium hover:bg-hover-bg transition-colors disabled:opacity-40"
          >
            <Download size={15} />
            {t("download")}
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={deleting}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg border border-red-400/25 text-red-300 text-sm font-medium hover:bg-red-400/10 transition-colors disabled:opacity-50 disabled:cursor-wait"
          >
            {deleting ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
            {deleting ? t("deletingAsset") : t("deleteAsset")}
          </button>
        </div>
      </div>
    </aside>
  );
}
