"use client";

import { Check, ChevronDown, Clipboard } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import type { CompiledGenerationRequest } from "@/lib/api";
import { getApprovedModel } from "@/lib/newApiModels";

const MODE_KEYS: Record<string, string> = {
  t2i: "modeTextToImage",
  i2i: "modeImageEdit",
  t2v: "modeTextToVideo",
  i2v: "modeImageToVideo",
  r2v: "modeReferenceToVideo",
  text: "modeText",
  image: "modeImage",
  video: "modeVideo",
  entity_extraction: "modeEntityExtraction",
  style_analysis: "modeStyleAnalysis",
  storyboard_extraction: "modeStoryboardExtraction",
};

const PARAMETER_KEYS: Record<string, string> = {
  duration: "parameterDuration",
  resolution: "parameterResolution",
  ratio: "parameterRatio",
  aspect_ratio: "parameterRatio",
  seed: "parameterSeed",
  generate_audio: "parameterAudio",
  watermark: "parameterWatermark",
  batch_size: "parameterOutputs",
  n: "parameterOutputs",
  size: "parameterSize",
  quality: "parameterQuality",
  output_format: "parameterFormat",
  background: "parameterBackground",
};

function referenceName(reference: string, fallback: string): string {
  if (!reference) return fallback;
  try {
    const parsed = new URL(reference);
    const name = decodeURIComponent(parsed.pathname.split("/").filter(Boolean).pop() || "");
    if (name) return name;
  } catch {
    const normalized = reference.replace(/\\/g, "/");
    const name = normalized.split("/").filter(Boolean).pop();
    if (name) return decodeURIComponent(name);
  }
  return fallback;
}

function parameterValue(value: string | number | boolean, yes: string, no: string): string {
  if (typeof value === "boolean") return value ? yes : no;
  return String(value);
}

export default function CompiledRequestContent({
  compiled,
}: {
  compiled: CompiledGenerationRequest;
}) {
  const t = useTranslations("generationRequest");
  const [copied, setCopied] = useState(false);
  const serialized = useMemo(() => JSON.stringify(compiled, null, 2), [compiled]);
  const modeKey = MODE_KEYS[compiled.mode.toLowerCase()];
  const modeLabel = modeKey ? t(modeKey) : t("modeGeneration");

  const copy = async () => {
    await navigator.clipboard.writeText(serialized);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="space-y-3" data-testid="compiled-request-content">
      <div className="flex flex-wrap items-center gap-2 text-[0.6875rem] text-text-muted">
        <span className="rounded-full border border-primary/25 bg-primary/10 px-2 py-1 font-semibold text-primary">
          {modeLabel}
        </span>
        <span>{t("requestCount", { count: compiled.provider_requests.length })}</span>
      </div>

      {compiled.provider_requests.map((request, index) => {
        const modelName = getApprovedModel(request.model)?.name || request.model;
        return (
          <article
            key={`${request.phase}-${index}`}
            className="space-y-3 rounded-lg border border-glass-border bg-input-bg p-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-xs font-bold text-foreground">
                {compiled.provider_requests.length > 1
                  ? t("requestNumber", { number: index + 1 })
                  : t("sentContent")}
              </h3>
              <span className="ml-auto text-xs font-semibold text-text-secondary">{modelName}</span>
            </div>

            <label className="block space-y-1.5">
              <span className="text-[0.6875rem] font-semibold text-text-muted">{t("fullPrompt")}</span>
              <textarea
                readOnly
                value={request.prompt}
                className="min-h-28 w-full resize-y rounded-lg border border-glass-border bg-background/55 p-3 text-xs leading-relaxed text-foreground outline-none"
              />
            </label>

            {request.input_media.length ? (
              <div>
                <p className="text-[0.6875rem] font-semibold text-text-muted">{t("references")}</p>
                <ul className="mt-1 flex flex-wrap gap-1.5 text-[0.6875rem] text-text-secondary">
                  {request.input_media.map((media, mediaIndex) => (
                    <li key={`${media}-${mediaIndex}`} className="max-w-full truncate rounded-md border border-glass-border bg-glass px-2 py-1" title={referenceName(media, t("referenceNumber", { number: mediaIndex + 1 }))}>
                      {referenceName(media, t("referenceNumber", { number: mediaIndex + 1 }))}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {Object.keys(request.parameters).length ? (
              <div>
                <p className="text-[0.6875rem] font-semibold text-text-muted">{t("settings")}</p>
                <dl className="mt-1 grid grid-cols-1 gap-x-4 gap-y-1.5 rounded-lg border border-glass-border bg-black/10 p-2 text-[0.6875rem] sm:grid-cols-2">
                  {Object.entries(request.parameters).map(([key, value]) => (
                    <div key={key} className="flex min-w-0 items-center justify-between gap-3">
                      <dt className="truncate text-text-muted">
                        {PARAMETER_KEYS[key] ? t(PARAMETER_KEYS[key]) : key.replaceAll("_", " ")}
                      </dt>
                      <dd className="truncate font-medium text-text-secondary">
                        {parameterValue(value, t("yes"), t("no"))}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            ) : null}
          </article>
        );
      })}

      <details className="group rounded-lg border border-glass-border bg-black/10">
        <summary className="flex min-h-10 cursor-pointer list-none items-center gap-2 px-3 text-xs font-semibold text-text-secondary transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60">
          <ChevronDown size={13} className="transition-transform group-open:rotate-180" aria-hidden="true" />
          {t("technicalDetails")}
        </summary>
        <div className="space-y-3 border-t border-glass-border p-3">
          <dl className="grid grid-cols-1 gap-2 text-[0.6875rem] sm:grid-cols-2">
            <div>
              <dt className="text-text-muted">{t("compilerLabel")}</dt>
              <dd className="font-mono text-text-secondary">{compiled.compiler_version}</dd>
            </div>
            <div>
              <dt className="text-text-muted">{t("checksumLabel")}</dt>
              <dd className="truncate font-mono text-text-secondary" title={compiled.checksum}>{compiled.checksum}</dd>
            </div>
            <div>
              <dt className="text-text-muted">{t("rawModeLabel")}</dt>
              <dd className="font-mono text-text-secondary">{compiled.mode}</dd>
            </div>
            <div>
              <dt className="text-text-muted">{t("requestIdLabel")}</dt>
              <dd className="truncate font-mono text-text-secondary" title={compiled.compiled_request_id}>{compiled.compiled_request_id}</dd>
            </div>
          </dl>

          {compiled.provider_requests.map((request, index) => (
            <div key={`${request.phase}-technical-${index}`} className="space-y-1.5">
              <p className="font-mono text-[0.625rem] text-text-muted">
                {request.phase} · {request.model}
              </p>
              {request.input_media.length ? (
                <ul className="space-y-1 font-mono text-[0.625rem] text-text-secondary">
                  {request.input_media.map((media) => <li key={media} className="break-all">{media}</li>)}
                </ul>
              ) : null}
              <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-black/20 p-2 font-mono text-[0.625rem] leading-relaxed text-text-secondary">
                {JSON.stringify(request.parameters, null, 2)}
              </pre>
            </div>
          ))}

          <button
            type="button"
            onClick={() => void copy()}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-glass-border px-3 text-xs text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground"
          >
            {copied ? <Check size={12} aria-hidden="true" /> : <Clipboard size={12} aria-hidden="true" />}
            {copied ? t("copied") : t("copyTechnical")}
          </button>
        </div>
      </details>
    </div>
  );
}
