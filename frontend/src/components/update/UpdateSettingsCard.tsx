"use client";

import { CircleAlert, Download, LoaderCircle, RefreshCw, RotateCw } from "lucide-react";
import { useTranslations } from "next-intl";
import { useUpdater } from "@/components/update/UpdaterProvider";
import { updateProgressPercent } from "@/lib/updater";

export default function UpdateSettingsCard() {
  const t = useTranslations("ui.update");
  const { supported, state, checkForUpdates, startUpdate, installAndRestart } = useUpdater();
  const percent = updateProgressPercent(state.progress);

  if (!supported) return null;

  const busy = state.status === "checking" || state.status === "downloading" || state.status === "installing";
  const action = state.status === "available"
    ? startUpdate
    : state.status === "ready"
      ? installAndRestart
      : checkForUpdates;
  const actionLabel = state.status === "available"
    ? t("downloadUpdate")
    : state.status === "ready"
      ? t("restartToUpdate")
      : state.status === "error"
        ? t("retry")
        : t("checkNow");
  const ActionIcon = state.status === "available"
    ? Download
    : state.status === "ready"
      ? RotateCw
      : RefreshCw;

  return (
    <section className="mt-6 rounded-xl border border-glass-border bg-surface/55 p-4" aria-labelledby="desktop-update-title">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h3 id="desktop-update-title" className="text-sm font-semibold text-foreground">{t("title")}</h3>
          <p className="mt-1 text-xs leading-relaxed text-text-muted">
            {state.currentVersion ? t("currentVersion", { version: state.currentVersion }) : t("desktopOnly")}
          </p>
          {state.availableVersion && state.status !== "idle" && (
            <p className="mt-1 text-xs font-medium text-primary">{t("availableVersion", { version: state.availableVersion })}</p>
          )}
        </div>
        <button
          type="button"
          onClick={() => void action()}
          disabled={busy}
          className="inline-flex min-h-10 shrink-0 items-center justify-center gap-2 rounded-lg border border-glass-border bg-elevated px-3.5 text-sm font-semibold text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:cursor-wait disabled:opacity-60"
        >
          {busy ? <LoaderCircle size={15} className="animate-spin text-primary" /> : <ActionIcon size={15} />}
          {state.status === "checking"
            ? t("checking")
            : state.status === "downloading"
              ? percent === null
                ? t("downloading")
                : t("downloadingPercent", { percent })
              : state.status === "installing"
                ? t("installing")
                : actionLabel}
        </button>
      </div>

      {state.status === "downloading" && (
        <div className="mt-4">
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-inset" role={percent === null ? undefined : "progressbar"} aria-label={t("downloadProgress")} aria-valuemin={percent === null ? undefined : 0} aria-valuemax={percent === null ? undefined : 100} aria-valuenow={percent ?? undefined}>
            <div className="h-full rounded-full bg-primary transition-[width] duration-fast" style={{ width: `${percent ?? 35}%` }} />
          </div>
          <p className="mt-2 text-xs text-text-muted">{t("backgroundHint")}</p>
        </div>
      )}

      {state.status === "ready" && <p className="mt-3 text-xs text-text-muted">{t("restartHint")}</p>}
      {state.releaseNotes && (state.status === "available" || state.status === "ready") && (
        <details className="mt-3 text-xs text-text-secondary">
          <summary className="cursor-pointer font-semibold text-foreground">{t("releaseNotes")}</summary>
          <p className="mt-2 max-h-32 overflow-y-auto whitespace-pre-wrap leading-relaxed">
            {/[A-Za-z]{2,}/.test(state.releaseNotes.replaceAll("EnMotion", ""))
              ? t("releaseNotesChineseOnly")
              : state.releaseNotes}
          </p>
        </details>
      )}
      {state.status === "error" && (
        <p role="alert" className="mt-3 flex items-start gap-2 rounded-lg border border-status-failed-border bg-status-failed-bg px-3 py-2 text-xs text-status-failed-fg">
          <CircleAlert size={14} className="mt-0.5 shrink-0" />
          {state.error && !/[A-Za-z]{2,}/.test(state.error.replaceAll("EnMotion", ""))
            ? state.error
            : t("failed")}
        </p>
      )}
    </section>
  );
}
