"use client";

import { Check, ChevronDown, Clipboard, Eye, Loader2, RefreshCw } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import type { CompiledGenerationRequest } from "@/lib/api";

interface GenerationRequestReviewProps {
  fingerprint: string;
  loadPreview: () => Promise<CompiledGenerationRequest>;
  disabled?: boolean;
  className?: string;
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
}: GenerationRequestReviewProps) {
  const t = useTranslations("generationRequest");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [compiled, setCompiled] = useState<CompiledGenerationRequest | null>(null);
  const [copied, setCopied] = useState(false);
  const [reviewedFingerprint, setReviewedFingerprint] = useState<string | null>(null);
  const currentCompiled = reviewedFingerprint === fingerprint ? compiled : null;
  const currentError = reviewedFingerprint === fingerprint ? error : null;

  const serialized = useMemo(
    () => currentCompiled ? JSON.stringify(currentCompiled, null, 2) : "",
    [currentCompiled],
  );

  const refresh = async () => {
    if (disabled || loading) return;
    setReviewedFingerprint(fingerprint);
    setCopied(false);
    setLoading(true);
    setError(null);
    try {
      setCompiled(await loadPreview());
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

  const copy = async () => {
    if (!serialized) return;
    await navigator.clipboard.writeText(serialized);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

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
            <>
              <div className="flex flex-wrap items-center gap-2 text-[0.6875rem] text-text-muted">
                <span className="rounded-full border border-glass-border bg-glass px-2 py-1">
                  {currentCompiled.mode.toUpperCase()}
                </span>
                <span>{t("compiler", { version: currentCompiled.compiler_version })}</span>
                <span className="font-mono" title={currentCompiled.checksum}>
                  {currentCompiled.checksum.slice(0, 12)}
                </span>
                <button
                  type="button"
                  onClick={() => void copy()}
                  className="ml-auto inline-flex min-h-8 items-center gap-1.5 rounded-lg border border-glass-border px-2 text-text-secondary hover:bg-hover-bg hover:text-foreground"
                >
                  {copied ? <Check size={12} /> : <Clipboard size={12} />}
                  {copied ? t("copied") : t("copy")}
                </button>
              </div>

              {currentCompiled.provider_requests.map((request, index) => (
                <article
                  key={`${request.phase}-${index}`}
                  className="space-y-3 rounded-lg border border-glass-border bg-input-bg p-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-xs font-bold text-foreground">
                      {currentCompiled.provider_requests.length > 1
                        ? t("requestNumber", { number: index + 1 })
                        : t("exactRequest")}
                    </h3>
                    <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[0.625rem] font-semibold text-primary">
                      {request.phase}
                    </span>
                    <span className="ml-auto text-[0.6875rem] text-text-muted">
                      {request.model}
                    </span>
                  </div>
                  <label className="block space-y-1.5">
                    <span className="text-[0.6875rem] font-semibold text-text-muted">
                      {t("fullPrompt")}
                    </span>
                    <textarea
                      readOnly
                      value={request.prompt}
                      className="min-h-28 w-full resize-y rounded-lg border border-glass-border bg-background/55 p-3 text-xs leading-relaxed text-foreground outline-none"
                    />
                  </label>
                  {request.input_media.length ? (
                    <div>
                      <p className="text-[0.6875rem] font-semibold text-text-muted">
                        {t("references")}
                      </p>
                      <ul className="mt-1 space-y-1 font-mono text-[0.625rem] text-text-secondary">
                        {request.input_media.map((media) => (
                          <li key={media} className="break-all">{media}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  <div>
                    <p className="text-[0.6875rem] font-semibold text-text-muted">
                      {t("parameters")}
                    </p>
                    <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-black/20 p-2 font-mono text-[0.625rem] leading-relaxed text-text-secondary">
                      {JSON.stringify(request.parameters, null, 2)}
                    </pre>
                  </div>
                </article>
              ))}
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
