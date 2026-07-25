"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CircleAlert,
  Coins,
  LoaderCircle,
  RefreshCw,
  X,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import {
  authApi,
  type AccountBalance,
  type AccountUsageItem,
} from "@/lib/authApi";
import ModalPortal from "@/components/common/ModalPortal";

const EMPTY_BALANCE: AccountBalance = {
  available_credits: 0,
  reserved_credits: 0,
  total_credits: 0,
};

function formatCredits(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value);
}

function usageOperationKey(operation: string): string {
  const keys: Record<string, string> = {
    "chat.completions": "chatCompletions",
    "images.generations": "imageGeneration",
    "images.edits": "imageEditing",
    "video.generations": "videoGeneration",
  };
  return keys[operation.trim().toLowerCase()] ?? "other";
}

function usageStatusKey(status: string): string {
  const normalized = status.trim().toLowerCase();
  const keys: Record<string, string> = {
    settled: "settled",
    completed: "completed",
    captured: "captured",
    succeeded: "succeeded",
    reserved: "reserved",
    processing: "processing",
    pending_reconciliation: "pendingReconciliation",
    released: "released",
    refunded: "refunded",
    failed: "failed",
    canceled: "canceled",
    cancelled: "canceled",
  };
  return keys[normalized] ?? "other";
}

function usageStatusTone(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (["failed", "canceled", "cancelled"].includes(normalized)) return "text-status-failed-fg";
  if (["settled", "completed", "captured", "succeeded", "released", "refunded"].includes(normalized)) {
    return "text-status-completed-fg";
  }
  return "text-status-processing-fg";
}

function AccountUsageDialog({
  initialBalance,
  onBalance,
  onClose,
}: {
  initialBalance: AccountBalance;
  onBalance: (balance: AccountBalance) => void;
  onClose: () => void;
}) {
  const t = useTranslations("ui.auth");
  const locale = useLocale();
  const [balance, setBalance] = useState(initialBalance);
  const [items, setItems] = useState<AccountUsageItem[]>([]);
  const [cursor, setCursor] = useState<string | null>();
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(false);

  const load = useCallback(async (nextCursor?: string) => {
    const loadingNextPage = Boolean(nextCursor);
    if (loadingNextPage) setLoadingMore(true);
    else setLoading(true);
    setError(false);
    try {
      const [nextBalance, page] = await Promise.all([
        authApi.accountBalance(),
        authApi.accountUsage(30, nextCursor),
      ]);
      setBalance(nextBalance);
      onBalance(nextBalance);
      setItems((current) => loadingNextPage ? [...current, ...page.items] : page.items);
      setCursor(page.next_cursor ?? null);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [onBalance]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  return (
    <ModalPortal isOpen onClose={onClose}>
      {(dialogRef) => (
        <div
          className="fixed inset-0 z-[220] grid place-items-center overflow-y-auto bg-overlay p-4 backdrop-blur-sm"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) onClose();
          }}
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="account-usage-title"
            tabIndex={-1}
            className="flex max-h-[min(88dvh,760px)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-glass-border bg-elevated shadow-2xl outline-none"
      >
        <header className="flex items-start justify-between gap-4 border-b border-glass-border px-5 py-4 sm:px-6">
          <div>
            <h2 id="account-usage-title" className="font-display text-xl font-semibold text-foreground">{t("usageTitle")}</h2>
            <p className="mt-1 text-sm text-text-muted">{t("usageHint")}</p>
          </div>
          <button type="button" onClick={onClose} aria-label={t("closeUsage")} className="grid min-h-10 min-w-10 place-items-center rounded-lg text-text-muted hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring">
            <X size={18} />
          </button>
        </header>

        <div className="grid grid-cols-1 gap-3 border-b border-glass-border p-5 sm:grid-cols-3 sm:px-6">
          {([
            ["availableCredits", balance.available_credits],
            ["reservedCredits", balance.reserved_credits],
            ["totalCredits", balance.total_credits],
          ] as const).map(([label, value]) => (
            <div key={label} className="rounded-xl border border-glass-border bg-surface/65 px-4 py-3">
              <p className="font-mono text-[0.625rem] uppercase tracking-wider text-text-muted">{t(label)}</p>
              <p className="mt-1 text-xl font-semibold text-foreground">{formatCredits(value, locale)}</p>
            </div>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-5 sm:px-6">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-foreground">{t("recentUsage")}</h3>
            <button type="button" onClick={() => void load()} disabled={loading} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs font-semibold text-text-secondary hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:opacity-50">
              <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
              {t("refreshUsage")}
            </button>
          </div>

          {loading && items.length === 0 && (
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-text-muted">
              <LoaderCircle size={16} className="animate-spin text-primary" /> {t("loadingUsage")}
            </div>
          )}
          {error && items.length === 0 && (
            <p role="alert" className="mt-4 flex items-center gap-2 rounded-lg border border-status-failed-border bg-status-failed-bg px-3 py-3 text-sm text-status-failed-fg">
              <CircleAlert size={15} /> {t("loadUsageFailed")}
            </p>
          )}
          {!loading && !error && items.length === 0 && <p className="py-10 text-center text-sm text-text-muted">{t("noUsage")}</p>}

          {items.length > 0 && (
            <ul className="mt-3 space-y-2" aria-label={t("recentUsage")}>
              {items.map((item) => (
                <li key={item.id} className="grid gap-2 rounded-xl border border-glass-border bg-surface/45 px-3.5 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-foreground">
                      {t(`usageOperations.${usageOperationKey(item.operation)}`)}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-text-muted">{item.model}</p>
                    <p className="mt-1 text-[0.6875rem] text-text-muted">
                      {new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(item.created_at))}
                    </p>
                  </div>
                  <div className="text-left sm:text-right">
                    <p className="font-mono text-sm font-semibold text-foreground">
                      −{formatCredits(item.settled_units || item.reserved_units, locale)}
                    </p>
                    <p className={`mt-0.5 text-xs font-semibold ${usageStatusTone(item.status)}`}>
                      {t(`usageStatuses.${usageStatusKey(item.status)}`)}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {cursor && (
            <button type="button" onClick={() => void load(cursor)} disabled={loadingMore} className="mt-4 flex min-h-10 w-full items-center justify-center gap-2 rounded-lg border border-glass-border text-sm font-semibold text-text-secondary hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:opacity-50">
              {loadingMore && <LoaderCircle size={15} className="animate-spin" />}
              {t("loadMoreUsage")}
            </button>
          )}
        </div>
          </div>
        </div>
      )}
    </ModalPortal>
  );
}

export default function AccountControl({ enabled = true }: { enabled?: boolean }) {
  const t = useTranslations("ui.auth");
  const locale = useLocale();
  const [balance, setBalance] = useState<AccountBalance>(EMPTY_BALANCE);
  const [loaded, setLoaded] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setBalance(await authApi.accountBalance());
      setLoaded(true);
    } catch {
      // Account service errors must not block local creative work.
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const initialRefresh = window.setTimeout(() => void refresh(), 0);
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 30_000);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearTimeout(initialRefresh);
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [enabled, refresh]);

  const label = useMemo(
    () => loaded ? formatCredits(balance.available_credits, locale) : "—",
    [balance.available_credits, loaded, locale],
  );

  if (!enabled) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setDialogOpen(true)}
        aria-label={t("openUsage", { credits: label })}
        className="inline-flex min-h-8 shrink-0 items-center gap-1.5 rounded-full border border-glass-border bg-surface/65 px-2.5 text-xs font-semibold text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
      >
        <Coins size={14} className="text-accent" />
        <span className="hidden sm:inline">{t("credits")}</span>
        <span className="font-mono text-foreground">{label}</span>
      </button>
      {dialogOpen && (
        <AccountUsageDialog
          initialBalance={balance}
          onBalance={setBalance}
          onClose={() => setDialogOpen(false)}
        />
      )}
    </>
  );
}
