"use client";

import { AlertTriangle, Loader2, LockKeyhole, Sparkles, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import ModalPortal from "@/components/common/ModalPortal";
import ChatModelSelect from "@/components/generation/ChatModelSelect";
import GenerationRequestReview from "@/components/generation/GenerationRequestReview";
import WorkflowActionButton from "@/components/shared/WorkflowActionButton";
import {
  api,
  type TextGenerationConfig,
  type TextGenerationOperation,
  type TextGenerationRequestDraft,
} from "@/lib/api";
import { extractErrorDetail } from "@/lib/utils";

interface TextGenerationRequestDialogProps<T = unknown> {
  open: boolean;
  scriptId: string;
  operation: TextGenerationOperation;
  initialSourceText?: string;
  warning?: string;
  onClose: () => void;
  onCompleted: (result: T) => void | Promise<void>;
}

export default function TextGenerationRequestDialog<T = unknown>({
  open,
  scriptId,
  operation,
  initialSourceText,
  warning,
  onClose,
  onCompleted,
}: TextGenerationRequestDialogProps<T>) {
  const t = useTranslations("generationRequest");
  const [config, setConfig] = useState<TextGenerationConfig | null>(null);
  const [model, setModel] = useState("");
  const [instructions, setInstructions] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState<{ fingerprint: string; checksum: string } | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      setConfig(null);
      setReviewed(null);
      void api.getTextGenerationConfig(scriptId, operation)
        .then((next) => {
          if (cancelled) return;
          setConfig(next);
          setModel(next.model);
          setInstructions(next.instructions);
          setSourceText(initialSourceText ?? next.source_text);
        })
        .catch((cause) => {
          if (!cancelled) setError(extractErrorDetail(cause, t("textConfigFailed")));
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [initialSourceText, open, operation, scriptId, t]);

  const requestDraft = useMemo<TextGenerationRequestDraft | null>(() => {
    if (!config || !model || !instructions.trim() || !sourceText.trim()) return null;
    return {
      operation,
      model,
      instructions: instructions.trim(),
      source_text: sourceText,
    };
  }, [config, instructions, model, operation, sourceText]);

  const fingerprint = useMemo(
    () => requestDraft ? JSON.stringify(requestDraft) : "",
    [requestDraft],
  );

  const submit = async () => {
    if (!requestDraft || submitting || reviewed?.fingerprint !== fingerprint) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.executeTextGeneration<T>(scriptId, {
        ...requestDraft,
        compiled_request_checksum: reviewed.checksum,
      });
      await onCompleted(result);
      onClose();
    } catch (cause) {
      setError(extractErrorDetail(cause, t("textExecutionFailed")));
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <ModalPortal isOpen={open} onClose={submitting ? () => undefined : onClose}>
      {(dialogRef) => (
        <div
          className="fixed inset-0 z-[230] grid place-items-center overflow-y-auto bg-overlay p-3 backdrop-blur-sm sm:p-6"
          onMouseDown={(event) => {
            if (!submitting && event.target === event.currentTarget) onClose();
          }}
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="text-generation-dialog-title"
            tabIndex={-1}
            className="flex max-h-[min(94dvh,980px)] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-glass-border bg-elevated shadow-[0_28px_90px_-20px_rgba(0,0,0,0.78)] outline-none"
          >
            <header className="flex shrink-0 items-center gap-3 border-b border-glass-border px-5 py-4 sm:px-6">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-primary/30 bg-primary/10 text-primary">
                <Sparkles size={17} />
              </span>
              <div className="min-w-0 flex-1">
                <h2 id="text-generation-dialog-title" className="truncate font-display text-xl font-semibold text-foreground">
                  {t(`textOperations.${operation}`)}
                </h2>
                <p className="mt-0.5 text-xs text-text-muted">{t("textComposerDescription")}</p>
              </div>
              <button
                type="button"
                onClick={onClose}
                disabled={submitting}
                aria-label={t("cancel")}
                className="grid h-10 w-10 shrink-0 place-items-center rounded-lg text-text-muted hover:bg-hover-bg hover:text-foreground disabled:opacity-40"
              >
                <X size={18} />
              </button>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto p-5 custom-scrollbar sm:p-6">
              {loading ? (
                <div className="flex min-h-72 items-center justify-center gap-2 text-sm text-text-muted">
                  <Loader2 size={17} className="animate-spin text-primary" />
                  {t("loading")}
                </div>
              ) : config ? (
                <div className="space-y-5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="text-sm font-semibold text-foreground">{t("textRunSettings")}</p>
                    <ChatModelSelect
                      id={`text-generation-model-${operation}`}
                      label={t("model")}
                      value={model}
                      onChange={setModel}
                      disabled={submitting}
                    />
                  </div>

                  {warning ? (
                    <div className="flex items-start gap-2 rounded-xl border border-accent/35 bg-accent/10 px-3.5 py-3 text-sm text-accent">
                      <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                      <span>{warning}</span>
                    </div>
                  ) : null}

                  <label className="block space-y-2">
                    <span className="text-sm font-semibold text-foreground">{t("textInstructions")}</span>
                    <textarea
                      value={instructions}
                      onChange={(event) => setInstructions(event.target.value)}
                      disabled={submitting}
                      rows={7}
                      className="glass-input w-full resize-y text-sm leading-relaxed"
                    />
                  </label>

                  <section className="space-y-2">
                    <div className="flex items-center gap-2">
                      <LockKeyhole size={14} className="text-text-muted" />
                      <h3 className="text-sm font-semibold text-foreground">{t("textOutputContract")}</h3>
                      <span className="rounded-full border border-glass-border px-2 py-0.5 text-[0.625rem] font-semibold text-text-muted">
                        {t("locked")}
                      </span>
                    </div>
                    <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-xl border border-glass-border bg-black/15 p-3 text-xs leading-relaxed text-text-secondary custom-scrollbar">
                      {config.output_contract}
                    </pre>
                  </section>

                  <label className="block space-y-2">
                    <span className="text-sm font-semibold text-foreground">{t("textSourceContent")}</span>
                    <textarea
                      value={sourceText}
                      onChange={(event) => setSourceText(event.target.value)}
                      disabled={submitting}
                      rows={9}
                      className="glass-input w-full resize-y font-mono text-xs leading-relaxed"
                    />
                  </label>

                  {requestDraft ? (
                    <GenerationRequestReview
                      fingerprint={fingerprint}
                      loadPreview={() => api.previewTextGeneration(scriptId, requestDraft)}
                      disabled={submitting}
                      defaultOpen
                      onReviewed={(compiled, reviewedFingerprint) => {
                        setReviewed({
                          fingerprint: reviewedFingerprint,
                          checksum: compiled.checksum,
                        });
                      }}
                    />
                  ) : null}
                </div>
              ) : null}

              {error ? (
                <p role="alert" className="mt-4 rounded-xl border border-status-failed-border bg-status-failed-bg px-3.5 py-3 text-sm text-status-failed-fg">
                  {error}
                </p>
              ) : null}
            </div>

            <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-glass-border px-5 py-3.5 sm:px-6">
              <WorkflowActionButton variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
                {t("cancel")}
              </WorkflowActionButton>
              <WorkflowActionButton
                variant="primary"
                size="sm"
                loading={submitting}
                disabled={!requestDraft || reviewed?.fingerprint !== fingerprint || loading || submitting}
                onClick={() => void submit()}
                leftIcon={<Sparkles />}
              >
                {submitting ? t("textRunning") : t("textRun")}
              </WorkflowActionButton>
            </footer>
          </div>
        </div>
      )}
    </ModalPortal>
  );
}
