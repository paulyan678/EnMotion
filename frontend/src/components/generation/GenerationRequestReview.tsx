"use client";

import { ChevronDown, Eye, Loader2, RefreshCw } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import type { CompiledGenerationRequest } from "@/lib/api";
import CompiledRequestContent from "@/components/generation/CompiledRequestContent";

interface GenerationRequestReviewProps {
  fingerprint: string;
  loadPreview: () => Promise<CompiledGenerationRequest>;
  disabled?: boolean;
  className?: string;
  defaultOpen?: boolean;
  onReviewed?: (compiled: CompiledGenerationRequest, fingerprint: string) => void;
}

/**
 * Shared, non-overlapping provider-request inspector.
 *
 * The server remains the compiler of record. Any draft change clears this
 * component's snapshot, preventing an older preview from looking current.
 */
export default function GenerationRequestReview({
  fingerprint,
  loadPreview,
  disabled = false,
  className = "",
  defaultOpen = false,
  onReviewed,
}: GenerationRequestReviewProps) {
  const t = useTranslations("generationRequest");
  const [open, setOpen] = useState(defaultOpen);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [compiled, setCompiled] = useState<CompiledGenerationRequest | null>(null);
  const [reviewedFingerprint, setReviewedFingerprint] = useState<string | null>(null);
  const currentCompiled = reviewedFingerprint === fingerprint ? compiled : null;
  const currentError = reviewedFingerprint === fingerprint ? error : null;

  const refresh = async () => {
    if (disabled || loading) return;
    setCompiled(null);
    setReviewedFingerprint(fingerprint);
    setLoading(true);
    setError(null);
    try {
      const next = await loadPreview();
      setCompiled(next);
      onReviewed?.(next, fingerprint);
    } catch {
      setCompiled(null);
      setError(t("previewFailed"));
    } finally {
      setLoading(false);
    }
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !currentCompiled && !loading) void refresh();
  };

  useEffect(() => {
    if (!open || disabled || loading || currentCompiled || currentError) return;
    const timer = window.setTimeout(() => void refresh(), 0);
    // `fingerprint` is the invalidation contract. Loading/error/current state
    // guards prevent duplicate previews while keeping default-open surfaces live.
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disabled, fingerprint, open]);

  return (
    <section
      className={`shrink-0 overflow-hidden rounded-xl border border-glass-border bg-black/10 ${className}`}
      data-testid="generation-request-review"
    >
      <button
        type="button"
        onClick={toggle}
        disabled={disabled}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center gap-2 px-3 text-left text-sm font-semibold text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Eye size={15} aria-hidden="true" />
        <span>{t("reviewButton")}</span>
        {currentCompiled ? (
          <span className="ml-auto rounded-full border border-status-success-border bg-status-success-bg px-2 py-0.5 text-[0.625rem] font-semibold text-status-success-fg">
            {t("reviewed")}
          </span>
        ) : null}
        <ChevronDown
          size={15}
          className={`${currentCompiled ? "" : "ml-auto"} shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        />
      </button>

      {open ? (
        <div className="space-y-3 border-t border-glass-border p-3">
          <p className="text-xs leading-relaxed text-text-muted">
            {t("description")}
          </p>

          {loading ? (
            <div className="flex min-h-20 items-center justify-center gap-2 text-xs text-text-muted">
              <Loader2 size={15} className="animate-spin" aria-hidden="true" />
              {t("loading")}
            </div>
          ) : null}

          {currentError ? (
            <div className="rounded-lg border border-status-error-border bg-status-error-bg p-3 text-xs text-status-error-fg">
              <p>{currentError}</p>
              <button
                type="button"
                onClick={() => void refresh()}
                className="mt-2 inline-flex items-center gap-1.5 font-semibold underline underline-offset-2"
              >
                <RefreshCw size={12} aria-hidden="true" />
                {t("retry")}
              </button>
            </div>
          ) : null}

          {!loading && !currentError && !currentCompiled ? (
            <button
              type="button"
              onClick={() => void refresh()}
              className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg border border-glass-border text-xs font-semibold text-text-secondary hover:bg-hover-bg hover:text-foreground"
            >
              <RefreshCw size={13} aria-hidden="true" />
              {t("refresh")}
            </button>
          ) : null}

          {currentCompiled ? (
            <CompiledRequestContent compiled={currentCompiled} />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
