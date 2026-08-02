"use client";

import { useState } from "react";
import { FilePenLine, Loader2, X } from "lucide-react";
import { useTranslations } from "next-intl";
import ModalPortal from "@/components/common/ModalPortal";

export interface ContentMetadataValue {
  title: string;
  description: string;
  scriptSummary?: string;
}

interface ContentMetadataDialogProps {
  open: boolean;
  kind: "series" | "episode" | "project";
  value: ContentMetadataValue;
  onClose: () => void;
  onSave: (value: ContentMetadataValue) => Promise<void>;
}

export default function ContentMetadataDialog(props: ContentMetadataDialogProps) {
  if (!props.open) return null;
  return <ContentMetadataDialogBody {...props} />;
}

function ContentMetadataDialogBody({
  open,
  kind,
  value,
  onClose,
  onSave,
}: ContentMetadataDialogProps) {
  const t = useTranslations("contentMetadata");
  const tc = useTranslations("common");
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const title = draft.title.trim();
  const canSave = !!title && !saving;

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      await onSave({
        title,
        description: draft.description.trim(),
        ...(kind === "series"
          ? {}
          : { scriptSummary: (draft.scriptSummary || "").trim() }),
      });
      onClose();
    } catch (saveError) {
      console.error("Failed to save content metadata:", saveError);
      setError(t("saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <ModalPortal isOpen={open} onClose={saving ? () => undefined : onClose}>
      {(dialogRef) => (
        <div
          className="fixed inset-0 z-[220] flex items-center justify-center overflow-y-auto bg-overlay p-3 backdrop-blur-sm sm:p-6"
          onMouseDown={() => {
            if (!saving) onClose();
          }}
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="content-metadata-title"
            tabIndex={-1}
            className="flex max-h-[calc(100dvh-1.5rem)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-glass-border bg-elevated shadow-2xl sm:max-h-[calc(100dvh-3rem)]"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="flex shrink-0 items-start justify-between gap-4 border-b border-glass-border p-4 sm:p-6">
              <div className="flex min-w-0 items-center gap-3">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary/15 text-primary">
                  <FilePenLine size={19} aria-hidden="true" />
                </span>
                <div className="min-w-0">
                  <h2 id="content-metadata-title" className="text-lg font-semibold text-foreground">
                    {t(`${kind}Title`)}
                  </h2>
                  <p className="mt-0.5 text-xs text-text-secondary">{t(`${kind}Hint`)}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={onClose}
                disabled={saving}
                aria-label={tc("close")}
                className="grid h-10 w-10 shrink-0 place-items-center rounded-lg text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:opacity-40"
              >
                <X size={19} aria-hidden="true" />
              </button>
            </header>

            <div className="custom-scrollbar flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
              <label className="block space-y-2">
                <span className="text-sm font-medium text-foreground">{t("name")}</span>
                <input
                  autoFocus
                  type="text"
                  aria-label={t("name")}
                  value={draft.title}
                  maxLength={200}
                  onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))}
                  aria-invalid={!draft.title.trim()}
                  className="min-h-11 w-full rounded-xl border border-glass-border bg-input-bg px-3 py-2 text-sm text-foreground outline-none transition-colors placeholder:text-text-muted focus:border-primary focus:ring-2 focus:ring-primary/30"
                  placeholder={t(`${kind}NamePlaceholder`)}
                />
                <span className="block text-right font-mono text-[0.625rem] text-text-muted">
                  {draft.title.length}/200
                </span>
              </label>

              <label className="block space-y-2">
                <span className="text-sm font-medium text-foreground">{t("description")}</span>
                <textarea
                  aria-label={t("description")}
                  value={draft.description}
                  maxLength={20_000}
                  onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
                  className="min-h-28 w-full resize-y rounded-xl border border-glass-border bg-input-bg p-3 text-sm leading-relaxed text-foreground outline-none transition-colors placeholder:text-text-muted focus:border-primary focus:ring-2 focus:ring-primary/30"
                  placeholder={t(`${kind}DescriptionPlaceholder`)}
                />
                <span className="block text-right font-mono text-[0.625rem] text-text-muted">
                  {draft.description.length}/20000
                </span>
              </label>

              {kind !== "series" ? (
                <label className="block space-y-2">
                  <span className="text-sm font-medium text-foreground">{t("scriptSummary")}</span>
                  <textarea
                    aria-label={t("scriptSummary")}
                    value={draft.scriptSummary || ""}
                    maxLength={20_000}
                    onChange={(event) => setDraft((current) => ({ ...current, scriptSummary: event.target.value }))}
                    className="min-h-36 w-full resize-y rounded-xl border border-glass-border bg-input-bg p-3 text-sm leading-relaxed text-foreground outline-none transition-colors placeholder:text-text-muted focus:border-primary focus:ring-2 focus:ring-primary/30"
                    placeholder={t("scriptSummaryPlaceholder")}
                  />
                  <span className="block text-right font-mono text-[0.625rem] text-text-muted">
                    {(draft.scriptSummary || "").length}/20000
                  </span>
                </label>
              ) : null}

              {error ? (
                <p role="alert" className="rounded-xl border border-status-failed-border bg-status-failed-bg px-3 py-2 text-sm text-status-failed-fg">
                  {error}
                </p>
              ) : null}
            </div>

            <footer className="flex shrink-0 flex-col-reverse gap-3 border-t border-glass-border p-4 sm:flex-row sm:justify-end sm:p-6">
              <button
                type="button"
                onClick={onClose}
                disabled={saving}
                className="min-h-11 rounded-xl px-5 py-2 text-sm text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:opacity-40"
              >
                {tc("cancel")}
              </button>
              <button
                type="button"
                onClick={() => void handleSave()}
                disabled={!canSave}
                className="flex min-h-11 items-center justify-center gap-2 rounded-xl bg-primary px-6 py-2 text-sm font-semibold text-on-accent transition-colors hover:bg-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {saving ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : null}
                {saving ? tc("saving") : tc("save")}
              </button>
            </footer>
          </div>
        </div>
      )}
    </ModalPortal>
  );
}
