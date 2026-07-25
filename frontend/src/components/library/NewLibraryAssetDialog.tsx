"use client";

import { useState, useRef, type FormEvent, type ChangeEvent } from "react";
import { useLocale, useTranslations } from "next-intl";
import { X, Loader2, Plus, Upload, Image as ImageIcon } from "lucide-react";
import { api } from "@/lib/api";
import { notifyAssetLibraryChanged } from "@/lib/assetLibrarySync";
import { getAssetUrl } from "@/lib/utils";
import { toast } from "@/store/toastStore";
import ModalPortal from "@/components/common/ModalPortal";

type AssetTab = "characters" | "scenes" | "props";

// 资产类型 → 后端单数 type（/library/assets 端点用）。
const SINGULAR: Record<AssetTab, string> = { characters: "character", scenes: "scene", props: "prop" };

interface NewLibraryAssetDialogProps {
  onClose: () => void;
}

/**
 * 资产库「新建全局资产」轻量弹窗（T6-entries）。
 * 选类型(角色/场景/道具) + 名称 + 描述 +（可选）图片（上传本地文件或填 URL）→ POST /library/assets。
 * 本地上传走 POST /library/assets/upload（multipart 字段 "file" → { image_url }），结果写入 imageUrl。
 */
export default function NewLibraryAssetDialog({ onClose }: NewLibraryAssetDialogProps) {
  const t = useTranslations("library");
  const tc = useTranslations("common");
  const locale = useLocale();
  const [assetType, setAssetType] = useState<AssetTab>("characters");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);

  const nameRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const typeOptions: { id: AssetTab; label: string }[] = [
    { id: "characters", label: t("characterLabel") },
    { id: "scenes", label: t("sceneLabel") },
    { id: "props", label: t("propLabel") },
  ];

  // 上传本地图片 → 后端返回 image_url，写入 imageUrl 作为创建用图。
  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // reset so re-selecting the same file fires onChange again
    if (!file) return;
    setUploading(true);
    try {
      const { image_url } = await api.uploadLibraryImage(file);
      setImageUrl(image_url);
      toast.success(t("uploadSuccess"));
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      toast.error(t("uploadFailed"), locale !== "zh" && msg ? { body: msg } : undefined);
    } finally {
      setUploading(false);
    }
  };

  const handleSubmit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (submitting || uploading) return;
    const trimmed = name.trim();
    if (!trimmed) {
      toast.error(t("nameRequired"));
      nameRef.current?.focus();
      return;
    }
    setSubmitting(true);
    try {
      const created = await api.createLibraryAsset(SINGULAR[assetType], {
        name: trimmed,
        description: description.trim() || undefined,
        image_url: imageUrl.trim() || undefined,
      });
      notifyAssetLibraryChanged({
        source: "global",
        assetType: SINGULAR[assetType] as "character" | "scene" | "prop",
        assetId: typeof created?.id === "string" ? created.id : undefined,
      });
      toast.success(t("createSuccess"), { body: trimmed });
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("createFailed");
      toast.error(t("createFailed"), locale === "zh" ? undefined : { body: msg });
      setSubmitting(false);
    }
  };

  return (
    <ModalPortal isOpen onClose={onClose}>
      {(dialogRef) => (
    <div className="fixed inset-0 z-[60] flex items-center justify-center overflow-y-auto p-3 sm:p-4">
      {/* 点外关闭遮罩 */}
      <button
        type="button"
        aria-hidden="true"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-sm cursor-default"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-library-asset-title"
        tabIndex={-1}
        className="atelier-reveal glass-panel relative z-[1] flex max-h-[calc(100dvh-1.5rem)] w-full max-w-[440px] flex-col overflow-hidden rounded-2xl border border-glass-border shadow-2xl sm:max-h-[calc(100dvh-2rem)]"
      >
        {/* header */}
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-glass-border px-4 pb-3 pt-4 sm:px-5 sm:pt-5">
          <div>
            <h2 id="new-library-asset-title" className="atelier-display font-display text-lg font-semibold tracking-tight text-foreground">
              {t("newAssetTitle")}
            </h2>
            <div className="text-[0.75rem] text-text-muted mt-0.5">{t("newAssetSubtitle")}</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={tc("close")}
            className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-text-muted transition-colors hover:bg-surface-inset hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
          >
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4 overflow-y-auto px-4 py-4 sm:px-5">
          {/* 类型选择 */}
          <div>
            <span className="font-mono text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-text-secondary">
              {t("assetTypeAria")}
            </span>
            <div
              className="mt-2 inline-flex p-[3px] rounded-full bg-surface-inset atelier-pill-tabs"
              role="group"
              aria-label={t("assetTypeAria")}
            >
              {typeOptions.map((opt) => {
                const on = assetType === opt.id;
                return (
                  <button
                    key={opt.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() => setAssetType(opt.id)}
                    className={`min-h-10 rounded-full px-3.5 py-1.5 text-[0.6875rem] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 ${
                      on
                        ? "text-foreground atelier-pill-tab-active bg-surface shadow-sm"
                        : "text-text-muted hover:text-foreground"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* 名称 */}
          <div>
            <label
              htmlFor="lib-asset-name"
              className="font-mono text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-text-secondary"
            >
              {t("nameLabel")}
            </label>
            <input
              id="lib-asset-name"
              ref={nameRef}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("namePlaceholder")}
              className="mt-2 w-full bg-surface-inset border border-glass-border rounded-lg px-3.5 py-2.5 text-[0.8125rem] text-foreground placeholder-text-muted focus:outline-none focus:border-primary/60"
            />
          </div>

          {/* 描述 */}
          <div>
            <label
              htmlFor="lib-asset-desc"
              className="font-mono text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-text-secondary"
            >
              {t("descLabel")}
            </label>
            <textarea
              id="lib-asset-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("descPlaceholder")}
              rows={3}
              className="mt-2 w-full bg-surface-inset border border-glass-border rounded-lg px-3.5 py-2.5 text-[0.8125rem] text-foreground placeholder-text-muted focus:outline-none focus:border-primary/60 resize-none"
            />
          </div>

          {/* 图片（可选）— 上传本地图片，或填图片 URL（备选） */}
          <div>
            <span className="font-mono text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-text-secondary">
              {t("imageLabel")}
            </span>

            {/* 上传本地图片 + 预览缩略图 */}
            <div className="mt-2 flex flex-wrap items-center gap-3">
              {imageUrl ? (
                <img
                  src={getAssetUrl(imageUrl)}
                  alt=""
                  className="w-12 h-12 rounded-lg object-cover border border-glass-border bg-surface-inset shrink-0"
                />
              ) : (
                <div className="w-12 h-12 rounded-lg grid place-items-center border border-glass-border bg-surface-inset text-text-muted shrink-0">
                  <ImageIcon size={16} aria-hidden="true" />
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                onChange={handleFileChange}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-glass-border bg-surface-inset px-3.5 py-2 text-[0.8125rem] font-medium text-text-secondary transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                {uploading ? t("uploading") : t("uploadImageButton")}
              </button>
            </div>

            {/* 备选：直接填图片 URL */}
            <label
              htmlFor="lib-asset-image"
              className="block mt-3 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-text-secondary"
            >
              {t("imageUrlLabel")}
            </label>
            <input
              id="lib-asset-image"
              type="url"
              value={imageUrl}
              onChange={(e) => setImageUrl(e.target.value)}
              placeholder={t("imageUrlPlaceholder")}
              className="mt-2 w-full bg-surface-inset border border-glass-border rounded-lg px-3.5 py-2.5 text-[0.8125rem] text-foreground placeholder-text-muted focus:outline-none focus:border-primary/60"
            />
            <div className="text-[0.6875rem] text-text-muted mt-1.5">{t("imageUrlHint")}</div>
          </div>

          {/* actions */}
          <div className="flex flex-col-reverse gap-2.5 pt-1 sm:flex-row sm:items-center sm:justify-end">
            <button
              type="button"
              onClick={onClose}
              className="min-h-11 rounded-lg border border-glass-border bg-surface-inset px-4 py-2 text-sm font-medium text-text-secondary transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
            >
              {tc("cancel")}
            </button>
            <button
              type="submit"
              disabled={submitting || uploading}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-accent transition-colors hover:bg-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
              {submitting ? t("creating") : tc("create")}
            </button>
          </div>
        </form>
      </div>
    </div>
      )}
    </ModalPortal>
  );
}
