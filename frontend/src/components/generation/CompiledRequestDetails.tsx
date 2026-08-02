"use client";

import { Check, Clipboard } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import type { CompiledGenerationRequest } from "@/lib/api";

/** Read-only rendering of the immutable request stored with a generation job. */
export default function CompiledRequestDetails({
  compiled,
}: {
  compiled: CompiledGenerationRequest;
}) {
  const t = useTranslations("generationRequest");
  const [copied, setCopied] = useState(false);
  const serialized = useMemo(() => JSON.stringify(compiled, null, 2), [compiled]);

  const copy = async () => {
    await navigator.clipboard.writeText(serialized);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="space-y-3" data-testid="compiled-request-details">
      <div className="flex flex-wrap items-center gap-2 text-[0.6875rem] text-text-muted">
        <span className="rounded-full border border-glass-border bg-glass px-2 py-1">
          {compiled.mode.toUpperCase()}
        </span>
        <span>{t("compiler", { version: compiled.compiler_version })}</span>
        <span className="font-mono" title={compiled.checksum}>
          {compiled.checksum.slice(0, 12)}
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

      {compiled.provider_requests.map((request, index) => (
        <article
          key={`${request.phase}-${index}`}
          className="space-y-3 rounded-lg border border-glass-border bg-input-bg p-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-xs font-bold text-foreground">
              {compiled.provider_requests.length > 1
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
          <div>
            <p className="text-[0.6875rem] font-semibold text-text-muted">
              {t("fullPrompt")}
            </p>
            <p className="mt-1 whitespace-pre-wrap break-words rounded-lg border border-glass-border bg-background/55 p-3 text-xs leading-relaxed text-foreground">
              {request.prompt}
            </p>
          </div>
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
    </div>
  );
}
