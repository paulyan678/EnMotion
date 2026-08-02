"use client";

import { useEffect, useRef, useState } from "react";
import { KeyRound, LoaderCircle, LogOut, ShieldCheck, UserRound, UsersRound, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { useAuth } from "@/components/auth/AuthProvider";
import UserManagementDialog from "@/components/auth/UserManagementDialog";
import ModalPortal from "@/components/common/ModalPortal";

export default function AccountMenu({ className = "" }: { className?: string }) {
  const t = useTranslations("ui.auth");
  const { serverMode, status, user, logout, changePassword } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [usersOpen, setUsersOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeOnOutsideClick);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeOnOutsideClick);
    };
  }, [menuOpen]);

  if (!serverMode || status !== "authenticated" || !user) return null;

  const submitPassword = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!currentPassword || newPassword.length < 12 || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setMessage(t("passwordUpdated"));
    } catch {
      setMessage(t("passwordUpdateFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div ref={menuRef} className={`relative min-w-0 ${className}`.trim()}>
        <button
          type="button"
          onClick={() => setMenuOpen((open) => !open)}
          aria-expanded={menuOpen}
          aria-haspopup="menu"
          aria-controls="sidebar-account-menu"
          aria-label={t("accountMenu", { username: user.username })}
          className="flex min-h-10 w-full min-w-0 items-center gap-2 rounded-lg px-2.5 text-sm text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
        >
          <UserRound size={16} className="shrink-0 text-primary" />
          <span className="min-w-0 flex-1 truncate text-left font-medium">{user.username}</span>
          {user.role === "admin" && <ShieldCheck aria-label={t("administrator")} size={14} className="shrink-0 text-accent" />}
        </button>

        {menuOpen && (
          <div id="sidebar-account-menu" role="menu" className="absolute bottom-[calc(100%+6px)] left-0 z-[90] w-64 rounded-xl border border-glass-border bg-elevated p-2 shadow-2xl">
            <div className="border-b border-glass-border px-2.5 pb-2.5 pt-1">
              <p className="truncate text-sm font-semibold text-foreground">{user.username}</p>
              <p className="mt-0.5 font-mono text-[0.625rem] uppercase tracking-wider text-text-muted">
                {user.role === "admin" ? t("administrator") : t("privateWorkspace")}
              </p>
            </div>
            <button
              type="button"
              role="menuitem"
              onClick={() => { setPasswordOpen(true); setMenuOpen(false); setMessage(null); }}
              className="mt-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-text-secondary hover:bg-hover-bg hover:text-foreground"
            >
              <KeyRound size={15} /> {t("changePassword")}
            </button>
            {user.role === "admin" && (
              <button
                type="button"
                role="menuitem"
                onClick={() => { setUsersOpen(true); setMenuOpen(false); }}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-text-secondary hover:bg-hover-bg hover:text-foreground"
              >
                <UsersRound size={15} /> {t("manageUsers")}
              </button>
            )}
            <button
              type="button"
              role="menuitem"
              onClick={() => void logout()}
              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-text-secondary hover:bg-hover-bg hover:text-foreground"
            >
              <LogOut size={15} /> {t("signOut")}
            </button>
          </div>
        )}
      </div>

      {passwordOpen && (
        <ModalPortal isOpen={passwordOpen} onClose={() => setPasswordOpen(false)}>
          {(dialogRef) => (
            <div className="fixed inset-0 z-[220] grid place-items-center overflow-y-auto bg-overlay px-4 py-6 backdrop-blur-sm" onMouseDown={() => setPasswordOpen(false)}>
              <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby="change-password-title"
                tabIndex={-1}
                onMouseDown={(event) => event.stopPropagation()}
                className="max-h-[calc(100dvh-3rem)] w-full max-w-md overflow-y-auto rounded-2xl border border-glass-border bg-elevated p-6 shadow-2xl outline-none"
              >
                <form onSubmit={submitPassword}>
                  <div className="flex items-center justify-between">
                    <h2 id="change-password-title" className="font-display text-xl font-semibold">{t("changePassword")}</h2>
                    <button type="button" aria-label={t("closePasswordDialog")} onClick={() => setPasswordOpen(false)} className="rounded-lg p-2 hover:bg-hover-bg">
                      <X size={18} />
                    </button>
                  </div>
                  <label className="mt-5 block text-sm font-medium">
                    {t("currentPassword")}
                    <input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} className="glass-input mt-2 w-full" required />
                  </label>
                  <label className="mt-4 block text-sm font-medium">
                    {t("newPassword")}
                    <input type="password" autoComplete="new-password" minLength={12} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} className="glass-input mt-2 w-full" required />
                    <span className="mt-1.5 block text-xs font-normal text-text-muted">{t("passwordMinimum")}</span>
                  </label>
                  {message && <p role="status" className="mt-3 text-sm text-text-secondary">{message}</p>}
                  <button type="submit" disabled={busy || !currentPassword || newPassword.length < 12} className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 font-semibold text-on-accent disabled:opacity-50">
                    {busy && <LoaderCircle size={16} className="animate-spin" />}
                    {t("updatePassword")}
                  </button>
                </form>
              </div>
            </div>
          )}
        </ModalPortal>
      )}
      {usersOpen && user.role === "admin" && <UserManagementDialog onClose={() => setUsersOpen(false)} />}
    </>
  );
}
