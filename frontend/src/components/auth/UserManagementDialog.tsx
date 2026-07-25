"use client";

import { FormEvent, useEffect, useState } from "react";
import { LoaderCircle, ShieldCheck, UserPlus, UsersRound, X } from "lucide-react";
import { useTranslations } from "next-intl";
import {
  authApi,
  type ManagedUser,
  type UserRole,
} from "@/lib/authApi";
import ModalPortal from "@/components/common/ModalPortal";
import AdminUserControls from "@/components/auth/AdminUserControls";

export default function UserManagementDialog({ onClose }: { onClose: () => void }) {
  const t = useTranslations("ui.auth");
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("user");
  const [initialCredits, setInitialCredits] = useState("0");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void authApi.listAdminUsers()
      .then((result) => {
        if (active) setUsers(result);
      })
      .catch(() => {
        if (active) setLoadError(t("loadAccountsFailed"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [t]);

  const initialCreditValue = Number(initialCredits);
  const initialCreditsValid = Number.isInteger(initialCreditValue)
    && initialCreditValue >= 0
    && initialCreditValue <= 2_000_000_000;
  const selectedUser = users.find((candidate) => candidate.id === selectedUserId) ?? null;

  const updateManagedUser = (updated: ManagedUser) => {
    setUsers((current) => current.map((candidate) => candidate.id === updated.id ? { ...candidate, ...updated } : candidate));
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (creating || username.trim().length < 3 || password.length < 12 || !initialCreditsValid) return;
    setCreating(true);
    setCreateError(null);
    setMessage(null);
    try {
      const created = await authApi.createUser({
        username: username.trim(),
        password,
        role,
        initial_credits: initialCreditValue,
      });
      setUsers((current) => [...current, created].sort((left, right) => left.username.localeCompare(right.username)));
      setMessage(t("accountCreated", { username: created.username }));
      setUsername("");
      setRole("user");
      setInitialCredits("0");
    } catch {
      setCreateError(t("accountCreateFailed"));
    } finally {
      // Never retain a submitted credential in component state.
      setPassword("");
      setCreating(false);
    }
  };

  return (
    <ModalPortal isOpen onClose={onClose}>
      {(dialogRef) => (
        <div className="fixed inset-0 z-[220] grid place-items-center overflow-y-auto bg-overlay px-4 py-6 backdrop-blur-sm" onMouseDown={onClose}>
          <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="manage-users-title"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
        className="glass-panel flex max-h-[calc(100dvh-3rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-glass-border shadow-2xl"
      >
        <header className="flex items-center justify-between border-b border-glass-border px-6 py-4">
          <div>
            <div className="flex items-center gap-2 text-primary">
              <UsersRound size={18} />
              <h2 id="manage-users-title" className="font-display text-xl font-semibold text-foreground">{t("manageAccounts")}</h2>
            </div>
            <p className="mt-1 text-sm text-text-muted">{t("accountsIsolationHint")}</p>
          </div>
          <button type="button" aria-label={t("closeUserManagement")} onClick={onClose} className="rounded-lg p-2 hover:bg-hover-bg">
            <X size={18} />
          </button>
        </header>

        <div className="grid min-h-0 gap-0 overflow-y-auto md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          <div className="border-b border-glass-border p-6 md:border-b-0 md:border-r">
            <h3 className="font-display text-base font-semibold">{t("currentUsers")}</h3>
            {loading && <p className="mt-4 flex items-center gap-2 text-sm text-text-muted"><LoaderCircle className="animate-spin" size={15} /> {t("loadingAccounts")}</p>}
            {loadError && <p role="alert" className="mt-4 text-sm text-danger">{loadError}</p>}
            {!loading && !loadError && (
              <ul aria-label={t("userAccounts")} className="mt-4 space-y-2">
                {users.map((listedUser) => {
                  const active = listedUser.active ?? listedUser.is_active ?? true;
                  return (
                    <li key={listedUser.id}>
                      <button
                        type="button"
                        aria-pressed={selectedUserId === listedUser.id}
                        onClick={() => setSelectedUserId((current) => current === listedUser.id ? null : listedUser.id)}
                        className={`w-full rounded-xl border px-3.5 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring ${selectedUserId === listedUser.id ? "border-primary/50 bg-primary/10" : "border-glass-border bg-surface/60 hover:bg-hover-bg"}`}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="min-w-0 truncate text-sm font-semibold">{listedUser.username}</span>
                          <span className="flex items-center gap-1 font-mono text-[0.625rem] uppercase tracking-wider text-text-muted">
                            {listedUser.role === "admin" && <ShieldCheck size={12} className="text-accent" />}
                            {listedUser.role === "admin" ? t("administrator") : t("user")}
                          </span>
                        </div>
                        <div className="mt-1 flex items-center justify-between gap-2 font-mono text-[0.625rem] text-text-muted">
                          <span className="truncate" title={listedUser.workspace_id}>{t("workspace")} {listedUser.workspace_id}</span>
                          <span className={active ? "text-status-completed-fg" : "text-status-failed-fg"}>{active ? t("active") : t("suspended")}</span>
                        </div>
                      </button>
                    </li>
                  );
                })}
                {users.length === 0 && <li className="text-sm text-text-muted">{t("noActiveAccounts")}</li>}
              </ul>
            )}
            {selectedUser && <AdminUserControls user={selectedUser} onUpdated={updateManagedUser} />}
          </div>

          <form onSubmit={submit} className="p-6">
            <div className="flex items-center gap-2">
              <UserPlus size={17} className="text-primary" />
              <h3 className="font-display text-base font-semibold">{t("createAccount")}</h3>
            </div>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <label className="block text-sm font-medium">
                {t("username")}
                <input aria-label={t("username")} value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} maxLength={64} autoComplete="off" className="glass-input mt-2 w-full" required />
              </label>
              <label className="block text-sm font-medium">
                {t("role")}
                <select aria-label={t("role")} value={role} onChange={(event) => setRole(event.target.value as UserRole)} className="glass-input mt-2 w-full">
                  <option value="user">{t("user")}</option>
                  <option value="admin">{t("administrator")}</option>
                </select>
              </label>
              <label className="block text-sm font-medium sm:col-span-2">
                {t("temporaryPassword")}
                <input aria-label={t("temporaryPassword")} type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} maxLength={1024} autoComplete="new-password" className="glass-input mt-2 w-full" required />
                <span className="mt-1.5 block text-xs font-normal text-text-muted">{t("temporaryPasswordHint")}</span>
              </label>
              <label className="block text-sm font-medium sm:col-span-2">
                {t("initialCredits")}
                <input aria-label={t("initialCredits")} type="number" value={initialCredits} onChange={(event) => setInitialCredits(event.target.value)} min="0" max="2000000000" step="1" className="glass-input mt-2 w-full" required />
                <span className="mt-1.5 block text-xs font-normal text-text-muted">{t("initialCreditsHint")}</span>
              </label>
            </div>
            {createError && <p role="alert" className="mt-4 rounded-lg border border-danger/30 bg-danger/10 px-3.5 py-3 text-sm text-danger">{createError}</p>}
            {message && <p role="status" className="mt-4 rounded-lg border border-primary/30 bg-primary/10 px-3.5 py-3 text-sm text-text-secondary">{message}</p>}
            <button type="submit" disabled={creating || username.trim().length < 3 || password.length < 12 || !initialCreditsValid} className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 font-semibold text-on-accent disabled:opacity-50">
              {creating && <LoaderCircle size={16} className="animate-spin" />}
              {t("createAccount")}
            </button>
          </form>
        </div>
          </section>
        </div>
      )}
    </ModalPortal>
  );
}
