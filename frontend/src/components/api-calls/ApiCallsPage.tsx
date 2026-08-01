"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  Activity,
  AlertCircle,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Download,
  FileText,
  Image as ImageIcon,
  Loader2,
  PauseCircle,
  RotateCcw,
  Trash2,
  Video,
  X,
} from "lucide-react";
import clsx from "clsx";
import {
  apiCallsApi,
  type ApiCallActivity,
  type ApiCallMedia,
  type ApiCallProgressStep,
  type ApiCallStatus,
} from "@/lib/api";
import PreviewImage from "@/components/shared/preview/PreviewImage";
import PreviewVideo from "@/components/shared/preview/PreviewVideo";
import ModalPortal from "@/components/common/ModalPortal";
import GlobalPageHeader from "@/components/layout/GlobalPageHeader";
import { appDateTimeFormatter, parseApiTimestamp } from "@/lib/dateTime";
import { useNow } from "@/lib/useNow";

type StatusFilter = "all" | ApiCallStatus;

const LIVE_POLL_INTERVAL_MS = 2_000;
const IDLE_POLL_INTERVAL_MS = 15_000;
const KNOWN_PROGRESS_STAGES = new Set([
  "queued",
  "validating_request",
  "preparing_inputs",
  "submitted_to_provider",
  "accepted_by_provider",
  "provider_processing",
  "downloading_output",
  "persisting_media",
  "finalizing",
  "completed",
]);
const OPERATION_KEYS = {
  "chat.completions": "chatGeneration",
  "images.generations": "imageGeneration",
  "images.edits": "imageEditing",
  "video.generations": "videoGeneration",
  project_asset: "projectAsset",
  series_asset: "seriesAsset",
  global_asset: "globalAsset",
  motion_reference: "motionReference",
  video: "video",
  playground: "playground",
  project_assets_batch: "assetBatch",
  refine_batch: "refineBatch",
  generate_storyboard: "generateStoryboard",
  generate_video: "generateVideo",
  storyboard_render: "storyboardRender",
  merge: "merge",
  export: "export",
  dub_preview: "dubPreview",
} as const;
const PARAMETER_KEYS: Record<string, string> = {
  size: "size",
  quality: "quality",
  resolution: "resolution",
  duration: "duration",
  ratio: "ratio",
  aspect_ratio: "aspect_ratio",
  batch_size: "batch_size",
  seed: "seed",
  generate_audio: "generate_audio",
  audio: "audio",
  watermark: "watermark",
  prompt_extend: "prompt_extend",
  generation_mode: "generation_mode",
};

const STATUS_TONES: Record<ApiCallStatus, string> = {
  queued: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  running: "border-primary/35 bg-primary/10 text-primary",
  completed: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  failed: "border-red-400/30 bg-red-400/10 text-red-300",
  canceled: "border-glass-border bg-glass text-text-muted",
};

function parseTimestamp(value?: string | null): number | null {
  return parseApiTimestamp(value)?.getTime() ?? null;
}

export function elapsedMilliseconds(job: ApiCallActivity, now: number): number {
  const start = parseTimestamp(job.started_at) ?? parseTimestamp(job.created_at) ?? now;
  const end = job.status === "running"
    ? now
    : parseTimestamp(job.finished_at) ?? parseTimestamp(job.updated_at) ?? now;
  return Math.max(0, end - start);
}

export function formatElapsed(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1_000));
  const hours = Math.floor(totalSeconds / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
}

function LiveElapsedText({ job }: { job: ApiCallActivity }) {
  const now = useNow();
  return <>{formatElapsed(elapsedMilliseconds(job, now))}</>;
}

function ElapsedText({ job }: { job: ApiCallActivity }) {
  if (job.status === "running") return <LiveElapsedText job={job} />;
  const terminalTime = parseTimestamp(job.finished_at)
    ?? parseTimestamp(job.updated_at)
    ?? parseTimestamp(job.created_at)
    ?? 0;
  return <>{formatElapsed(elapsedMilliseconds(job, terminalTime))}</>;
}

function CategoryIcon({ category, className = "h-5 w-5" }: {
  category: ApiCallActivity["category"];
  className?: string;
}) {
  if (category === "text") return <FileText className={className} aria-hidden="true" />;
  if (category === "image") return <ImageIcon className={className} aria-hidden="true" />;
  if (category === "video") return <Video className={className} aria-hidden="true" />;
  return <Activity className={className} aria-hidden="true" />;
}

function StatusIcon({ status, className = "h-4 w-4" }: {
  status: ApiCallStatus;
  className?: string;
}) {
  if (status === "running") return <Loader2 className={clsx(className, "animate-spin")} aria-hidden="true" />;
  if (status === "queued") return <Clock3 className={className} aria-hidden="true" />;
  if (status === "completed") return <CheckCircle2 className={className} aria-hidden="true" />;
  if (status === "failed") return <AlertCircle className={className} aria-hidden="true" />;
  return <PauseCircle className={className} aria-hidden="true" />;
}

function MediaPreview({ output, label, className }: {
  output: ApiCallMedia;
  label: string;
  className: string;
}) {
  return (
    <div className="h-full w-full" onClick={stopNested}>
      {output.media_type === "video" ? (
        <PreviewVideo
          src={output.media_path}
          poster={output.thumbnail_path ?? undefined}
          alt={label}
          className={className}
          clickToLightbox
          alwaysShowMagnify
        />
      ) : (
        <PreviewImage
          src={output.media_path}
          alt={label}
          className={className}
          imgClassName="object-cover"
          clickToLightbox
          alwaysShowMagnify
          diagnosticContext="api-calls-output"
        />
      )}
    </div>
  );
}

function stepDuration(step: ApiCallProgressStep, now: number): string | null {
  const start = parseTimestamp(step.started_at);
  if (start === null) return null;
  const end = parseTimestamp(step.finished_at) ?? (step.state === "active" ? now : null);
  return end === null ? null : formatElapsed(Math.max(0, end - start));
}

function stopNested(event: MouseEvent<HTMLElement>) {
  event.stopPropagation();
}

export default function ApiCallsPage() {
  const t = useTranslations("apiCalls");
  const locale = useLocale();
  const [jobs, setJobs] = useState<ApiCallActivity[]>([]);
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionJobId, setActionJobId] = useState<string | null>(null);
  const [downloadKey, setDownloadKey] = useState<string | null>(null);
  const requestInFlight = useRef(false);
  const hasLiveJobsRef = useRef(false);
  const mutationVersion = useRef(0);
  const closeDetails = useCallback(() => setSelectedJobId(null), []);

  const loadJobs = useCallback(async () => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    const versionAtStart = mutationVersion.current;
    try {
      const next = await apiCallsApi.list();
      if (versionAtStart !== mutationVersion.current) return;
      hasLiveJobsRef.current = next.some(
        (job) => job.status === "running" || job.status === "queued",
      );
      setJobs(next);
      setError(null);
    } catch {
      if (versionAtStart !== mutationVersion.current) return;
      setError(t("loadError"));
    } finally {
      requestInFlight.current = false;
      setLoading(false);
    }
  }, [t]);

  const hasLiveJobs = jobs.some((job) => job.status === "running" || job.status === "queued");
  useEffect(() => {
    hasLiveJobsRef.current = hasLiveJobs;
  }, [hasLiveJobs]);

  useEffect(() => {
    let timer: number | null = null;
    let stopped = false;
    let cycle: Promise<void> | null = null;

    const clearTimer = () => {
      if (timer === null) return;
      window.clearTimeout(timer);
      timer = null;
    };
    const scheduleNext = () => {
      clearTimer();
      if (stopped || document.visibilityState !== "visible") return;
      const delay = hasLiveJobsRef.current
        ? LIVE_POLL_INTERVAL_MS
        : IDLE_POLL_INTERVAL_MS;
      timer = window.setTimeout(() => {
        timer = null;
        runCycle();
      }, delay);
    };
    const runCycle = (allowHidden = false) => {
      if (
        stopped
        || cycle
        || (!allowHidden && document.visibilityState !== "visible")
      ) {
        return;
      }
      clearTimer();
      const activeCycle = loadJobs();
      cycle = activeCycle;
      void activeCycle.finally(() => {
        if (cycle === activeCycle) cycle = null;
        scheduleNext();
      });
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState !== "visible") {
        clearTimer();
        return;
      }
      runCycle();
    };

    // WKWebView can briefly report a newly mounted page as hidden while the
    // native window is still activating. Always perform the first load; only
    // pause subsequent polling while the document is actually backgrounded.
    runCycle(true);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      stopped = true;
      clearTimer();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [loadJobs]);

  const counts = useMemo(() => ({
    all: jobs.length,
    running: jobs.filter((job) => job.status === "running").length,
    queued: jobs.filter((job) => job.status === "queued").length,
    completed: jobs.filter((job) => job.status === "completed").length,
    failed: jobs.filter((job) => job.status === "failed").length,
    canceled: jobs.filter((job) => job.status === "canceled").length,
  }), [jobs]);

  const filteredJobs = useMemo(
    () => jobs.filter((job) => filter === "all" || job.status === filter),
    [filter, jobs],
  );
  const selectedJob = jobs.find((job) => job.id === selectedJobId) ?? null;

  const runAction = useCallback(async (
    job: ApiCallActivity,
    action: "cancel" | "retry" | "dismiss",
  ) => {
    setActionJobId(job.id);
    setError(null);
    try {
      if (action === "cancel") {
        const updated = await apiCallsApi.cancel(job.id);
        mutationVersion.current += 1;
        setJobs((current) => current.map((item) => item.id === job.id ? updated : item));
      } else if (action === "retry") {
        const retried = await apiCallsApi.retry(job.id);
        mutationVersion.current += 1;
        setJobs((current) => current.map((item) => item.id === job.id ? retried : item));
      } else {
        await apiCallsApi.dismiss(job.id);
        mutationVersion.current += 1;
        setJobs((current) => current.filter((item) => item.id !== job.id));
        setSelectedJobId((current) => current === job.id ? null : current);
      }
    } catch {
      setError(t("actionError"));
    } finally {
      setActionJobId(null);
    }
  }, [t]);

  const runDownload = useCallback(async (job: ApiCallActivity, output: ApiCallMedia) => {
    const key = `${job.id}:${output.id}`;
    setDownloadKey(key);
    setError(null);
    try {
      const { blob, filename } = await apiCallsApi.download(
        job.id,
        output.id,
        output.media_path,
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch {
      setError(t("downloadError"));
    } finally {
      setDownloadKey(null);
    }
  }, [t]);

  const operationLabel = useCallback((type: string) => {
    const key = OPERATION_KEYS[type as keyof typeof OPERATION_KEYS];
    return key ? t(`operations.${key}`) : t("operations.unknown");
  }, [t]);

  const dateFormatter = useMemo(() => appDateTimeFormatter(locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }), [locale]);

  const filters: StatusFilter[] = ["all", "running", "queued", "completed", "failed", "canceled"];

  return (
    <section className="flex min-h-full flex-col">
      <GlobalPageHeader title={t("title")} />
      <div className="flex w-full flex-col gap-5 px-4 pb-6 md:px-7">

        {error ? (
          <div role="alert" className="flex items-start gap-3 rounded-xl border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-200">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="break-words font-semibold">{error}</p>
            </div>
            <button type="button" onClick={() => setError(null)} aria-label={t("dismissError")} className="text-red-200/60 hover:text-red-100">
              <X className="h-4 w-4" />
            </button>
          </div>
        ) : null}

        <div
          className="atelier-pill-tabs inline-flex max-w-full overflow-x-auto rounded-full bg-surface-inset p-[3px]"
          role="tablist"
          aria-label={t("filterAria")}
          onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault();
            const current = filters.indexOf(filter);
            const next = event.key === "Home"
              ? 0
              : event.key === "End"
                ? filters.length - 1
                : event.key === "ArrowRight"
                  ? (current + 1) % filters.length
                  : (current - 1 + filters.length) % filters.length;
            setFilter(filters[next]);
            event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
          }}
        >
          {filters.map((status) => (
            <button
              key={status}
              type="button"
              role="tab"
              aria-selected={filter === status}
              tabIndex={filter === status ? 0 : -1}
              onClick={() => setFilter(status)}
              className={clsx(
                "inline-flex min-w-max items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[0.6875rem] font-semibold transition-colors",
                filter === status
                  ? "atelier-pill-tab-active bg-surface text-foreground shadow-sm"
                  : "text-text-muted hover:text-foreground",
              )}
            >
              {t(`filters.${status}`)}
              <span className={clsx(
                "font-mono text-[0.59375rem]",
                filter === status ? "text-text-secondary" : "text-text-muted",
              )}>{counts[status]}</span>
            </button>
          ))}
        </div>

        <div aria-live="polite" className="min-h-[320px]">
          {loading ? (
            <div className="grid min-h-[320px] place-items-center rounded-2xl border border-glass-border bg-glass">
              <div className="flex items-center gap-3 text-sm text-text-secondary">
                <Loader2 className="h-5 w-5 animate-spin text-primary" />
                {t("loading")}
              </div>
            </div>
          ) : filteredJobs.length === 0 ? (
            <div className="grid min-h-[320px] place-items-center rounded-2xl border border-dashed border-glass-border bg-glass/50 px-6 text-center">
              <div>
                <PauseCircle className="mx-auto h-10 w-10 text-text-muted" aria-hidden="true" />
                <h2 className="mt-4 font-display text-2xl text-foreground">{t("emptyTitle")}</h2>
                <p className="mt-2 text-sm text-text-muted">{t("emptyBody")}</p>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {filteredJobs.map((job) => {
                const output = job.outputs?.[0];
                const pendingAction = actionJobId === job.id;
                const progress = Math.max(0, Math.min(100, job.status === "completed" ? 100 : job.progress || 0));
                const hasDeterminateProgress = job.status === "completed"
                  || (job.status === "running" && Boolean(job.progress_stage) && progress > 0);
                const failureMessage = job.error_code === "input_image_privacy"
                  ? t("errors.inputImagePrivacy")
                  : job.error_code === "output_video_policy"
                    ? t("errors.outputVideoPolicy")
                  : job.error_code === "provider_connection_failed"
                    ? t("errors.providerConnection")
                  : job.error_code === "provider_outcome_ambiguous"
                    ? t("errors.providerOutcomeAmbiguous")
                  : job.error_code === "provider_rate_limited"
                    ? t("errors.providerRateLimited")
                  : job.error_code === "provider_authentication_failed"
                    ? t("errors.providerAuthentication")
                  : job.error_code === "provider_access_denied"
                    ? t("errors.providerAccess")
                  : job.error_code === "provider_quota_exhausted"
                    ? t("errors.providerQuota")
                  : job.error_code === "provider_request_rejected"
                    ? t("errors.providerRequest")
                  : job.error_code === "provider_payload_too_large"
                    ? t("errors.providerPayloadTooLarge")
                  : job.error && !/[A-Za-z]{2,}/.test(job.error)
                    ? job.error
                    : t("errors.requestFailed");
                return (
                  <article
                    key={job.id}
                    role="button"
                    tabIndex={0}
                    aria-label={t("openDetailsAria", { name: operationLabel(job.type) })}
                    onClick={() => setSelectedJobId(job.id)}
                    onKeyDown={(event: KeyboardEvent<HTMLElement>) => {
                      if (event.target !== event.currentTarget) return;
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelectedJobId(job.id);
                      }
                    }}
                    className="group rounded-2xl border border-glass-border bg-surface/80 p-4 shadow-sm outline-none backdrop-blur-xl transition-colors hover:border-primary/30 focus-visible:ring-2 focus-visible:ring-primary/60 sm:p-5"
                  >
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
                      <div className="h-24 w-full shrink-0 overflow-hidden rounded-xl border border-glass-border bg-elevated sm:w-36 lg:h-20 lg:w-32">
                        {output ? (
                          <MediaPreview output={output} label={operationLabel(job.type)} className="h-full w-full" />
                        ) : (
                          <div className="grid h-full w-full place-items-center text-text-muted">
                            <CategoryIcon category={job.category} className="h-7 w-7" />
                          </div>
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h2 className="font-semibold text-foreground">{operationLabel(job.type)}</h2>
                          <span className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[0.625rem] font-semibold uppercase tracking-[0.08em]", STATUS_TONES[job.status])}>
                            <StatusIcon status={job.status} className="h-3 w-3" />
                            {t(`status.${job.status}`)}
                          </span>
                          {job.outputs && job.outputs.length > 1 ? (
                            <span className="rounded-full border border-glass-border px-2 py-0.5 text-[0.625rem] text-text-muted">
                              {t("outputCount", { count: job.outputs.length })}
                            </span>
                          ) : null}
                        </div>
                        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
                          <span>{t("source", { source: t(`sources.${job.source}`) })}</span>
                          {job.model_name ? <span>{job.model_name}</span> : null}
                          {job.attempts > 1 ? <span>{t("attempt", { count: job.attempts })}</span> : null}
                        </div>
                        {job.detail
                          ? <p className="mt-2 line-clamp-1 text-sm text-text-secondary">{job.detail}</p>
                          : null}
                        {job.status === "failed" && failureMessage ? (
                          <p className="mt-2 line-clamp-1 text-xs text-red-300">{failureMessage}</p>
                        ) : null}
                        {job.status === "running" ? (
                          <div className="mt-3 max-w-2xl">
                            {hasDeterminateProgress ? (
                              <>
                                <div
                                  role="progressbar"
                                  aria-valuemin={0}
                                  aria-valuemax={100}
                                  aria-valuenow={progress}
                                  aria-label={t("progressAria")}
                                  className="h-1.5 overflow-hidden rounded-full bg-elevated"
                                >
                                  <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${progress}%` }} />
                                </div>
                                <p className="mt-1 text-[0.6875rem] text-text-muted">
                                  {t(job.progress_is_estimated ? "estimatedProgress" : "progress", { progress })}
                                  {job.progress_stage
                                    ? ` · ${KNOWN_PROGRESS_STAGES.has(job.progress_stage)
                                      ? t(`stages.${job.progress_stage}`)
                                      : t("stages.unknown")}`
                                    : ""}
                                  {job.provider_progress !== null && job.provider_progress !== undefined
                                    ? ` · ${t("providerProgress", { progress: job.provider_progress })}`
                                    : ""}
                                </p>
                              </>
                            ) : (
                              <div role="status" className="flex items-center gap-2 text-[0.6875rem] text-text-muted">
                                <span className="h-1.5 w-20 overflow-hidden rounded-full bg-elevated">
                                  <span className="block h-full w-1/2 animate-pulse rounded-full bg-primary" />
                                </span>
                                {t("indeterminateProgress")}
                              </div>
                            )}
                          </div>
                        ) : null}
                      </div>

                      <div className="flex shrink-0 items-center justify-between gap-4 border-t border-glass-border pt-3 lg:min-w-[235px] lg:justify-end lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
                        <div className="text-right">
                          <p className="font-mono text-lg tabular-nums text-foreground">
                            <ElapsedText job={job} />
                          </p>
                          <p className="text-[0.625rem] uppercase tracking-[0.1em] text-text-muted">
                            {job.status === "running" ? t("elapsedLive") : t("elapsedFinal")}
                          </p>
                          {job.status === "queued" && job.queue_position ? (
                            <p className="mt-1 text-xs text-amber-300">{t("queuePosition", { position: job.queue_position })}</p>
                          ) : null}
                        </div>
                        <div className="flex items-center gap-2" onClick={stopNested}>
                          {job.status === "completed" && output ? (
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                void runDownload(job, output);
                              }}
                              disabled={downloadKey === `${job.id}:${output.id}`}
                              aria-label={t("downloadOutput", { name: output.filename || output.id })}
                              className="grid h-9 w-9 place-items-center rounded-lg border border-glass-border text-text-secondary hover:border-primary/30 hover:text-primary disabled:opacity-50"
                            >
                              {downloadKey === `${job.id}:${output.id}`
                                ? <Loader2 className="h-4 w-4 animate-spin" />
                                : <Download className="h-4 w-4" />}
                            </button>
                          ) : null}
                          {job.status === "queued" && !job.managed_read_only ? (
                            <button
                              type="button"
                              onClick={(event) => { event.stopPropagation(); void runAction(job, "cancel"); }}
                              disabled={pendingAction}
                              className="inline-flex items-center gap-1.5 rounded-lg border border-red-400/25 px-3 py-2 text-xs font-semibold text-red-300 hover:bg-red-400/10 disabled:opacity-50"
                            >
                              {pendingAction ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <X className="h-3.5 w-3.5" />}
                              {t("cancel")}
                            </button>
                          ) : null}
                          {job.status === "failed" && !job.managed_read_only ? (
                            <button
                              type="button"
                              onClick={(event) => { event.stopPropagation(); void runAction(job, "retry"); }}
                              disabled={pendingAction}
                              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-on-accent hover:bg-primary-hover disabled:opacity-50"
                            >
                              {pendingAction ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                              {t("retry")}
                            </button>
                          ) : null}
                          <ChevronRight className="h-4 w-4 text-text-muted transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
                        </div>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {selectedJob ? (
        <JobDetailDrawer
          job={selectedJob}
          operationLabel={operationLabel}
          dateFormatter={dateFormatter}
          actionPending={actionJobId === selectedJob.id}
          downloadKey={downloadKey}
          onClose={closeDetails}
          onAction={runAction}
          onDownload={runDownload}
        />
      ) : null}
    </section>
  );
}

function JobDetailDrawer({
  job,
  operationLabel,
  dateFormatter,
  actionPending,
  downloadKey,
  onClose,
  onAction,
  onDownload,
}: {
  job: ApiCallActivity;
  operationLabel: (type: string) => string;
  dateFormatter: Intl.DateTimeFormat;
  actionPending: boolean;
  downloadKey: string | null;
  onClose: () => void;
  onAction: (job: ApiCallActivity, action: "cancel" | "retry" | "dismiss") => Promise<void>;
  onDownload: (job: ApiCallActivity, output: ApiCallMedia) => Promise<void>;
}) {
  const t = useTranslations("apiCalls");
  const now = useNow();
  const failureMessage = job.error_code === "input_image_privacy"
    ? t("errors.inputImagePrivacy")
    : job.error_code === "output_video_policy"
      ? t("errors.outputVideoPolicy")
    : job.error_code === "provider_connection_failed"
      ? t("errors.providerConnection")
    : job.error_code === "provider_outcome_ambiguous"
      ? t("errors.providerOutcomeAmbiguous")
    : job.error_code === "provider_rate_limited"
      ? t("errors.providerRateLimited")
    : job.error_code === "provider_authentication_failed"
      ? t("errors.providerAuthentication")
    : job.error_code === "provider_access_denied"
      ? t("errors.providerAccess")
    : job.error_code === "provider_quota_exhausted"
      ? t("errors.providerQuota")
    : job.error_code === "provider_request_rejected"
      ? t("errors.providerRequest")
    : job.error_code === "provider_payload_too_large"
      ? t("errors.providerPayloadTooLarge")
    : job.error && !/[A-Za-z]{2,}/.test(job.error)
      ? job.error
      : t("errors.requestFailed");

  const navigateToSource = () => {
    const route = job.source_context?.route;
    if (!route) return;
    window.location.hash = route;
  };

  return (
    <ModalPortal isOpen onClose={onClose}>
      {(dialogRef) => (
        <div
          data-testid="api-call-detail-overlay"
          className="fixed inset-0 z-[220] flex justify-end bg-black/65 backdrop-blur-sm"
          onMouseDown={onClose}
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="api-call-detail-title"
            tabIndex={-1}
            onMouseDown={(event) => event.stopPropagation()}
            className="flex h-full w-full max-w-2xl flex-col border-l border-glass-border bg-surface shadow-2xl"
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-glass-border px-5 pb-5 pt-[max(1.25rem,env(safe-area-inset-top))] sm:px-6 sm:pb-6 sm:pt-[max(1.5rem,env(safe-area-inset-top))]">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id="api-call-detail-title" className="font-display text-2xl text-foreground">
                {operationLabel(job.type)}
              </h2>
              <span className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[0.625rem] font-semibold uppercase tracking-[0.08em]", STATUS_TONES[job.status])}>
                <StatusIcon status={job.status} className="h-3 w-3" />
                {t(`status.${job.status}`)}
              </span>
            </div>
            <p className="mt-1 font-mono text-[0.6875rem] text-text-muted">{job.id}</p>
          </div>
          <button type="button" onClick={onClose} aria-label={t("closeDetails")} className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-glass-border text-text-muted hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 space-y-6 overflow-y-auto p-5 sm:p-6">
          <section aria-labelledby="request-summary-title">
            <h3 id="request-summary-title" className="text-xs font-semibold uppercase tracking-[0.14em] text-text-muted">{t("requestDetails")}</h3>
            <dl className="mt-3 grid grid-cols-1 gap-3 rounded-xl border border-glass-border bg-glass p-4 text-sm sm:grid-cols-2">
              {job.detail ? <Detail label={t("requestNameLabel")} value={job.detail} /> : null}
              <Detail label={t("statusLabel")} value={t(`status.${job.status}`)} />
              <Detail label={t("typeLabel")} value={t(`category.${job.category}`)} />
              <Detail label={t("sourceLabel")} value={t(`sources.${job.source}`)} />
              <Detail label={t("modelLabel")} value={job.model_name || t("notAvailable")} />
              <Detail label={t("createdLabel")} value={dateFormatter.format(parseTimestamp(job.created_at) ?? now)} />
              <Detail label={t("startedLabel")} value={job.started_at ? dateFormatter.format(parseTimestamp(job.started_at) ?? now) : t("notAvailable")} />
              <Detail label={t("completedLabel")} value={job.finished_at ? dateFormatter.format(parseTimestamp(job.finished_at) ?? now) : t("notAvailable")} />
              <Detail label={t("durationLabel")} value={formatElapsed(elapsedMilliseconds(job, now))} />
            </dl>
            {job.prompt ? (
              <div className="mt-3 rounded-xl border border-glass-border bg-glass p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.12em] text-text-muted">{t("promptLabel")}</p>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-text-secondary">{job.prompt}</p>
              </div>
            ) : null}
            {job.parameters && Object.keys(job.parameters).length ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.entries(job.parameters).map(([key, value]) => (
                  <span key={key} className="rounded-full border border-glass-border bg-glass px-2.5 py-1 text-xs text-text-secondary">
                    {t(`parameterKeys.${PARAMETER_KEYS[key] ?? "unknown"}`)}:{" "}
                    <span className="font-mono text-foreground">
                      {typeof value === "boolean"
                        ? t(value ? "parameterValues.enabled" : "parameterValues.disabled")
                        : String(value)}
                    </span>
                  </span>
                ))}
              </div>
            ) : null}
          </section>

          {job.input_media?.length ? (
            <MediaSection title={t("inputMedia")} media={job.input_media} job={job} downloadKey={downloadKey} onDownload={null} />
          ) : null}
          {job.outputs?.length ? (
            <MediaSection title={t("outputs")} media={job.outputs} job={job} downloadKey={downloadKey} onDownload={onDownload} />
          ) : job.status === "completed" ? (
            <div className="rounded-xl border border-dashed border-glass-border p-5 text-center text-sm text-text-muted">
              {t("noMediaOutput")}
            </div>
          ) : null}

          <section aria-labelledby="progress-timeline-title">
            <h3 id="progress-timeline-title" className="text-xs font-semibold uppercase tracking-[0.14em] text-text-muted">{t("progressTimeline")}</h3>
            <ol className="mt-4 space-y-0">
              {(job.progress_steps ?? []).map((step, index) => {
                const duration = stepDuration(step, now);
                const knownStage = KNOWN_PROGRESS_STAGES.has(step.id);
                return (
                  <li key={`${step.id}-${index}`} className="relative flex gap-3 pb-5 last:pb-0">
                    {index < (job.progress_steps?.length ?? 0) - 1 ? (
                      <span className="absolute left-[0.4375rem] top-4 h-full w-px bg-glass-border" aria-hidden="true" />
                    ) : null}
                    <span className={clsx(
                      "relative z-10 mt-1 h-3.5 w-3.5 shrink-0 rounded-full border-2 bg-surface",
                      step.state === "completed" && "border-emerald-400",
                      step.state === "active" && "border-primary animate-pulse",
                      step.state === "failed" && "border-red-400",
                      step.state === "pending" && "border-text-muted",
                    )} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <p className="text-sm font-medium text-foreground">
                          {knownStage ? t(`stages.${step.id}`) : t("stages.unknown")}
                        </p>
                        {duration ? <span className="font-mono text-[0.6875rem] text-text-muted">{duration}</span> : null}
                      </div>
                      <p className="mt-0.5 text-xs leading-relaxed text-text-muted">
                        {step.state === "failed" && step.message
                          ? /[A-Za-z]{2,}/.test(step.message)
                            ? t("errors.requestFailed")
                            : step.message
                          : knownStage
                            ? t(`stageMessages.${step.id}`)
                            : t("stageMessages.unknown")}
                      </p>
                      {step.started_at ? (
                        <p className="mt-1 font-mono text-[0.625rem] text-text-muted">
                          {t("startedLabel")}: {dateFormatter.format(parseTimestamp(step.started_at) ?? now)}
                          {step.finished_at
                            ? ` · ${t("completedLabel")}: ${dateFormatter.format(parseTimestamp(step.finished_at) ?? now)}`
                            : ""}
                        </p>
                      ) : null}
                    </div>
                  </li>
                );
              })}
              {!job.progress_steps?.length ? (
                <li className="text-sm text-text-muted">{t("noTimeline")}</li>
              ) : null}
            </ol>
          </section>

          {job.status === "failed" && failureMessage ? (
            <section className="rounded-xl border border-red-400/30 bg-red-400/[0.07] p-4 text-sm text-red-200">
              <h3 className="font-semibold">{t("failureReason")}</h3>
              <p className="mt-1 leading-relaxed">{failureMessage}</p>
              {job.error_diagnostic ? (
                <details className="mt-3 border-t border-red-300/15 pt-3">
                  <summary className="cursor-pointer font-semibold">{t("technicalDetails")}</summary>
                  <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-[0.6875rem] leading-relaxed text-red-100/70">
                    {/[A-Za-z]{2,}/.test(job.error_diagnostic)
                      ? t("diagnosticHidden")
                      : job.error_diagnostic}
                  </pre>
                </details>
              ) : null}
            </section>
          ) : null}
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-glass-border p-4 sm:px-6">
          {job.source_context?.route ? (
            <button
              type="button"
              onClick={navigateToSource}
              className="inline-flex items-center gap-2 rounded-lg border border-glass-border px-3 py-2 text-sm font-semibold text-text-secondary hover:border-primary/30 hover:text-primary"
            >
              <ArrowUpRight className="h-4 w-4" />
              {t(`openSource.${job.source}`)}
            </button>
          ) : <span />}
          <div className="flex items-center gap-2">
            {job.status === "queued" && !job.managed_read_only ? (
              <button type="button" onClick={() => void onAction(job, "cancel")} disabled={actionPending} className="inline-flex items-center gap-2 rounded-lg border border-red-400/30 px-3 py-2 text-sm font-semibold text-red-300 hover:bg-red-400/10 disabled:opacity-50">
                {actionPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <X className="h-4 w-4" />}
                {t("cancel")}
              </button>
            ) : null}
            {job.status === "failed" && !job.managed_read_only ? (
              <button type="button" onClick={() => void onAction(job, "retry")} disabled={actionPending} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-on-accent hover:bg-primary-hover disabled:opacity-50">
                {actionPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
                {t("retry")}
              </button>
            ) : null}
            {!job.managed_read_only && ["completed", "failed", "canceled"].includes(job.status) ? (
              <button type="button" onClick={() => void onAction(job, "dismiss")} disabled={actionPending} className="inline-flex items-center gap-2 rounded-lg border border-glass-border px-3 py-2 text-sm font-semibold text-text-muted hover:border-red-400/30 hover:text-red-300 disabled:opacity-50">
                <Trash2 className="h-4 w-4" />
                {t("dismiss")}
              </button>
            ) : null}
          </div>
        </footer>
          </div>
        </div>
      )}
    </ModalPortal>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[0.6875rem] uppercase tracking-[0.1em] text-text-muted">{label}</dt>
      <dd className="mt-1 break-words text-text-secondary">{value}</dd>
    </div>
  );
}

function MediaSection({
  title,
  media,
  job,
  downloadKey,
  onDownload,
}: {
  title: string;
  media: ApiCallMedia[];
  job: ApiCallActivity;
  downloadKey: string | null;
  onDownload: ((job: ApiCallActivity, output: ApiCallMedia) => Promise<void>) | null;
}) {
  const t = useTranslations("apiCalls");
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-text-muted">{title}</h3>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {media.map((output, index) => (
          <div key={output.id} className="overflow-hidden rounded-xl border border-glass-border bg-glass">
            <MediaPreview output={output} label={`${title} ${index + 1}`} className="aspect-video w-full" />
            <div className="flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-foreground">{output.filename || `${title} ${index + 1}`}</p>
                {output.size_bytes ? <p className="mt-0.5 text-[0.625rem] text-text-muted">{formatBytes(output.size_bytes)}</p> : null}
              </div>
              {onDownload ? (
                <button
                  type="button"
                  onClick={() => void onDownload(job, output)}
                  disabled={downloadKey === `${job.id}:${output.id}`}
                  aria-label={t("downloadOutput", { name: output.filename || output.id })}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-glass-border px-2.5 py-1.5 text-xs font-semibold text-text-secondary hover:border-primary/30 hover:text-primary disabled:opacity-50"
                >
                  {downloadKey === `${job.id}:${output.id}`
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <Download className="h-3.5 w-3.5" />}
                  {t("download")}
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1_024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(1)} KB`;
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}
