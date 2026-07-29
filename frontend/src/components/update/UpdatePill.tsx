"use client";

import {
  ArrowDownToLine,
  LoaderCircle,
  RotateCw,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useUpdater } from "@/components/update/UpdaterProvider";
import { updateProgressPercent } from "@/lib/updater";

export default function UpdatePill() {
  const t = useTranslations("ui.update");
  const { supported, state, startUpdate, installAndRestart } = useUpdater();
  const percent = updateProgressPercent(state.progress);

  if (
    !supported
    || !["available", "downloading", "ready", "installing"].includes(state.status)
    || (state.status === "available" && !state.availableVersion)
  ) return null;

  if (state.status === "downloading") {
    return (
      <div
        className="flex min-w-8 shrink-0 items-center gap-2 rounded-full border border-primary/25 bg-primary/10 px-2 py-1 text-xs text-text-secondary sm:min-w-28 sm:px-2.5"
        role="status"
        aria-live="polite"
      >
        <LoaderCircle size={13} className="shrink-0 animate-spin text-primary" />
        <span className="hidden truncate sm:inline">{percent === null ? t("downloading") : t("downloadingPercent", { percent })}</span>
        <span
          className="hidden h-1 w-10 overflow-hidden rounded-full bg-surface-inset sm:block"
          role="progressbar"
          aria-label={t("downloadProgress")}
          aria-valuemin={percent === null ? undefined : 0}
          aria-valuemax={percent === null ? undefined : 100}
          aria-valuenow={percent ?? undefined}
        >
          <span
            className="block h-full rounded-full bg-primary transition-[width] duration-fast"
            style={{ width: `${percent ?? 35}%` }}
          />
        </span>
      </div>
    );
  }

  if (state.status === "installing") {
    return (
      <div className="flex shrink-0 items-center gap-2 rounded-full border border-primary/25 bg-primary/10 px-2 py-1 text-xs text-text-secondary sm:px-2.5" role="status" aria-label={t("installing")}>
        <LoaderCircle size={13} className="animate-spin text-primary" />
        <span className="hidden sm:inline">{t("installing")}</span>
      </div>
    );
  }

  const action = state.status === "available" ? startUpdate : installAndRestart;
  const label = state.status === "available"
    ? t("downloadVersion", { version: state.availableVersion ?? "" })
    : t("restartToUpdate");
  const Icon = state.status === "available"
    ? ArrowDownToLine
    : RotateCw;

  return (
    <button
      type="button"
      onClick={() => void action()}
      title={state.status === "ready" ? t("restartHint") : label}
      aria-label={label}
      className="flex min-h-8 min-w-8 shrink-0 items-center justify-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-2 text-xs font-semibold text-primary transition-colors hover:border-primary/55 hover:bg-primary/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring sm:px-2.5"
    >
      <Icon size={13} />
      <span className="hidden max-w-40 truncate sm:inline">{label}</span>
    </button>
  );
}
