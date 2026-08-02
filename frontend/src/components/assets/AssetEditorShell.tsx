"use client";

import { Check, Loader2, Save, X } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

import { useModalFocusTrap } from "@/components/common/useModalFocusTrap";

interface AssetEditorShellProps {
  title: string;
  typeLabel: string;
  isDirty: boolean;
  isSaving: boolean;
  saveDisabled?: boolean;
  onSave: () => void;
  onRequestClose: () => void;
  rail: ReactNode;
  preview: ReactNode;
  inspector: ReactNode;
}

/**
 * The one visual shell for every canonical asset editor.
 *
 * It intentionally owns only the editor chrome and responsive structure.
 * Character, scene, and prop workbenches retain their type-specific fields
 * and generation capabilities inside the three slots.
 */
export default function AssetEditorShell({
  title,
  typeLabel,
  isDirty,
  isSaving,
  saveDisabled = false,
  onSave,
  onRequestClose,
  rail,
  preview,
  inspector,
}: AssetEditorShellProps) {
  const t = useTranslations("library");
  const dialogRef = useModalFocusTrap<HTMLDivElement>(onRequestClose);

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label={t("editorTitle")}
      tabIndex={-1}
      className="fixed inset-0 z-[80] flex bg-overlay p-2 backdrop-blur-md focus:outline-none sm:p-4 xl:p-6"
    >
      <div className="m-auto flex h-full max-h-[980px] min-h-0 w-full max-w-[1680px] flex-col overflow-hidden rounded-2xl border border-glass-border bg-surface shadow-2xl">
        <header className="flex min-h-[72px] shrink-0 items-center justify-between gap-4 border-b border-glass-border bg-glass px-4 py-3 backdrop-blur-xl sm:px-6">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <div className="min-w-0">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <h1 className="truncate font-display text-lg font-bold text-foreground sm:text-xl">
                  {title}
                </h1>
                <span className="rounded-full border border-glass-border bg-input-bg px-2.5 py-1 text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-text-secondary">
                  {typeLabel}
                </span>
              </div>
              <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2">
                <span
                  className={`inline-flex items-center gap-1 text-xs ${
                    isDirty ? "text-status-pending-fg" : "text-text-muted"
                  }`}
                  aria-live="polite"
                >
                  <Check size={12} aria-hidden="true" />
                  {isDirty ? t("unsavedState") : t("savedState")}
                </span>
              </div>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={onSave}
              disabled={isSaving || saveDisabled || !isDirty}
              aria-label={isSaving ? t("savingChanges") : t("saveChanges")}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-bold text-primary-foreground shadow-lg shadow-primary/15 transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:cursor-not-allowed disabled:opacity-45"
            >
              {isSaving ? (
                <Loader2 size={16} className="animate-spin" aria-hidden="true" />
              ) : (
                <Save size={16} aria-hidden="true" />
              )}
              <span className="hidden sm:inline">
                {isSaving ? t("savingChanges") : t("saveChanges")}
              </span>
            </button>
            <button
              type="button"
              onClick={onRequestClose}
              disabled={isSaving}
              aria-label={t("closeEditor")}
              className="grid min-h-11 min-w-11 place-items-center rounded-xl border border-glass-border text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:opacity-45"
            >
              <X size={20} aria-hidden="true" />
            </button>
          </div>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[220px_minmax(0,1fr)] lg:grid-rows-[minmax(560px,1fr)_auto] lg:overflow-y-auto xl:grid-cols-[260px_minmax(0,1fr)_390px] xl:grid-rows-1 xl:overflow-hidden 2xl:grid-cols-[350px_minmax(0,1fr)_460px]">
          <aside className="border-b border-glass-border bg-black/10 p-3 lg:border-b-0 lg:border-r lg:p-4">
            {rail}
          </aside>
          <main className="min-h-[520px] min-w-0 bg-surface-inset p-3 sm:min-h-[580px] sm:p-5 lg:min-h-0">
            {preview}
          </main>
          <aside className="border-t border-glass-border bg-glass lg:col-span-2 xl:col-span-1 xl:border-l xl:border-t-0">
            {inspector}
          </aside>
        </div>
      </div>
    </div>,
    document.body,
  );
}
