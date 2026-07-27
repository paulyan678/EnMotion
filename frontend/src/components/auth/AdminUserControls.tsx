"use client";

import { FormEvent, useMemo, useState } from "react";
import {
  Ban,
  Coins,
  KeyRound,
  LoaderCircle,
  LogOut,
  UserCheck,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { authApi, type ManagedUser } from "@/lib/authApi";

const ADMIN_RESET_PASSWORD_MIN_LENGTH = 6;

function isActive(user: ManagedUser): boolean {
  return user.active ?? user.is_active ?? true;
}

export default function AdminUserControls({
  user,
  onUpdated,
}: {
  user: ManagedUser;
  onUpdated: (user: ManagedUser) => void;
}) {
  const t = useTranslations("ui.auth");
  const [delta, setDelta] = useState("");
  const [reason, setReason] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState<"credits" | "status" | "password" | "sessions" | null>(null);
  const [message, setMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  const parsedDelta = useMemo(() => {
    const value = Number(delta);
    return Number.isSafeInteger(value) && value !== 0 ? value : 0;
  }, [delta]);

  const adjustCredits = async (event: FormEvent) => {
    event.preventDefault();
    if (!parsedDelta || reason.trim().length < 3 || busy) return;
    setBusy("credits");
    setMessage(null);
    try {
      const result = await authApi.adjustCredits(user.id, parsedDelta, reason.trim());
      onUpdated({
        ...user,
        ...(result.user ?? {}),
        ...(result.balance ? {
          available_credits: result.balance.available_credits,
          reserved_credits: result.balance.reserved_credits,
        } : {}),
      });
      setDelta("");
      setReason("");
      setMessage({ kind: "success", text: t("creditsUpdated") });
    } catch {
      setMessage({ kind: "error", text: t("adminActionFailed") });
    } finally {
      setBusy(null);
    }
  };

  const toggleStatus = async () => {
    if (busy) return;
    setBusy("status");
    setMessage(null);
    try {
      onUpdated(await authApi.setUserActive(user.id, !isActive(user)));
      setMessage({ kind: "success", text: t("accountStatusUpdated") });
    } catch {
      setMessage({ kind: "error", text: t("adminActionFailed") });
    } finally {
      setBusy(null);
    }
  };

  const resetPassword = async () => {
    if (newPassword.length < ADMIN_RESET_PASSWORD_MIN_LENGTH || busy) return;
    setBusy("password");
    setMessage(null);
    try {
      await authApi.resetUserPassword(user.id, newPassword);
      setMessage({ kind: "success", text: t("temporaryPasswordUpdated") });
      setNewPassword("");
    } catch {
      setMessage({ kind: "error", text: t("adminActionFailed") });
    } finally {
      setBusy(null);
    }
  };

  const revokeSessions = async () => {
    if (busy) return;
    setBusy("sessions");
    setMessage(null);
    try {
      await authApi.revokeUserSessions(user.id);
      setMessage({ kind: "success", text: t("sessionsRevoked") });
    } catch {
      setMessage({ kind: "error", text: t("adminActionFailed") });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mt-4 rounded-xl border border-glass-border bg-surface/65 p-3.5" aria-label={t("manageSelectedUser", { username: user.username })}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{user.username}</p>
          <p className="mt-0.5 text-xs text-text-muted">
            {t("creditBalance", { credits: user.available_credits ?? 0 })}
          </p>
        </div>
        <span className={`rounded-full px-2 py-1 font-mono text-[0.625rem] uppercase tracking-wider ${isActive(user) ? "bg-status-completed-bg text-status-completed-fg" : "bg-status-failed-bg text-status-failed-fg"}`}>
          {isActive(user) ? t("active") : t("suspended")}
        </span>
      </div>

      <form onSubmit={adjustCredits} className="mt-4 space-y-2.5">
        <label className="block text-xs font-medium text-text-secondary">
          {t("creditAdjustment")}
          <input type="number" step="1" value={delta} onChange={(event) => setDelta(event.target.value)} placeholder={t("creditAdjustmentHint")} className="glass-input mt-1.5 w-full" />
        </label>
        <label className="block text-xs font-medium text-text-secondary">
          {t("adjustmentReason")}
          <input value={reason} onChange={(event) => setReason(event.target.value)} minLength={3} maxLength={240} className="glass-input mt-1.5 w-full" />
        </label>
        <button type="submit" disabled={!parsedDelta || reason.trim().length < 3 || Boolean(busy)} className="inline-flex min-h-9 w-full items-center justify-center gap-2 rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:opacity-50">
          {busy === "credits" ? <LoaderCircle size={14} className="animate-spin" /> : <Coins size={14} />}
          {t("applyCreditAdjustment")}
        </button>
      </form>

      <div className="mt-4 border-t border-glass-border pt-3">
        <label className="block text-xs font-medium text-text-secondary">
          {t("newTemporaryPassword")}
          <input type="text" autoComplete="new-password" minLength={ADMIN_RESET_PASSWORD_MIN_LENGTH} maxLength={256} autoCapitalize="none" autoCorrect="off" spellCheck={false} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} className="glass-input mt-1.5 w-full" />
          <span className="mt-1.5 block text-[0.6875rem] font-normal text-text-muted">{t("adminResetPasswordHint")}</span>
        </label>
        <button type="button" onClick={() => void resetPassword()} disabled={newPassword.length < ADMIN_RESET_PASSWORD_MIN_LENGTH || Boolean(busy)} className="mt-2 inline-flex min-h-9 w-full items-center justify-center gap-2 rounded-lg border border-glass-border px-3 text-xs font-semibold text-text-secondary hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:opacity-50">
          {busy === "password" ? <LoaderCircle size={14} className="animate-spin" /> : <KeyRound size={14} />}
          {t("resetPassword")}
        </button>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <button type="button" onClick={() => void toggleStatus()} disabled={Boolean(busy)} className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border border-glass-border px-2 text-xs font-semibold text-text-secondary hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:opacity-50">
          {busy === "status" ? <LoaderCircle size={14} className="animate-spin" /> : isActive(user) ? <Ban size={14} /> : <UserCheck size={14} />}
          {isActive(user) ? t("suspendAccount") : t("reactivateAccount")}
        </button>
        <button type="button" onClick={() => void revokeSessions()} disabled={Boolean(busy)} className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border border-glass-border px-2 text-xs font-semibold text-text-secondary hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:opacity-50">
          {busy === "sessions" ? <LoaderCircle size={14} className="animate-spin" /> : <LogOut size={14} />}
          {t("revokeSessions")}
        </button>
      </div>

      {message && (
        <p role={message.kind === "error" ? "alert" : "status"} className={`mt-3 rounded-lg border px-3 py-2 text-xs ${message.kind === "error" ? "border-status-failed-border bg-status-failed-bg text-status-failed-fg" : "border-status-completed-border bg-status-completed-bg text-status-completed-fg"}`}>
          {message.text}
        </p>
      )}
    </div>
  );
}
