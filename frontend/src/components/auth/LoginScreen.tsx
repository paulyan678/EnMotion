"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { LockKeyhole, UserRound } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { authErrorMessage } from "@/lib/authApi";
import { useAuth } from "@/components/auth/AuthProvider";
import EnMotionBranding from "@/components/layout/EnMotionBranding";
import StableAsyncButtonContent from "@/components/shared/StableAsyncButtonContent";

export default function LoginScreen() {
  const locale = useLocale() as "zh" | "en";
  const t = useTranslations("ui.auth");
  const tb = useTranslations("ui.brand");
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const usernameRef = useRef<HTMLInputElement>(null);

  useEffect(() => { usernameRef.current?.focus(); }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!username.trim() || !password || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await login(username.trim(), password);
      setPassword("");
    } catch (reason) {
      setError(authErrorMessage(reason, locale));
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="relative min-h-[100dvh] w-full overflow-x-hidden overflow-y-auto bg-background text-foreground">
      <div className="atelier-page-bloom" aria-hidden="true" />
      <div className="atelier-page-grain" aria-hidden="true" />
      <div className="relative z-10 flex min-h-[100dvh] items-center justify-center px-5 py-[max(2.5rem,env(safe-area-inset-top))]">
        <section className="glass-panel atelier-card w-full max-w-md rounded-2xl border border-glass-border p-7 shadow-2xl md:p-9">
          <EnMotionBranding size="md" showSlogan={false} />
          <div className="mt-7">
            <div className="font-mono text-[0.625rem] font-semibold uppercase tracking-[0.2em] text-primary">
              {t("privateWorkspace")}
            </div>
            <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight">{t("signInTitle")}</h1>
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">
              {t("isolationHint")}
            </p>
          </div>

          <form className="mt-7 space-y-4" onSubmit={submit}>
            <label className="block">
              <span className="mb-2 block text-sm font-medium">{t("username")}</span>
              <span className="relative block">
                <UserRound className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-text-muted" size={17} />
                <input
                  ref={usernameRef}
                  name="username"
                  autoComplete="username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  disabled={submitting}
                  className="glass-input w-full py-3 pl-10 pr-4"
                  required
                />
              </span>
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-medium">{t("password")}</span>
              <span className="relative block">
                <LockKeyhole className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-text-muted" size={17} />
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={submitting}
                  className="glass-input w-full py-3 pl-10 pr-4"
                  required
                />
              </span>
            </label>

            {error && (
              <p role="alert" className="rounded-lg border border-danger/30 bg-danger/10 px-3.5 py-3 text-sm text-danger">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting || !username.trim() || !password}
              aria-busy={submitting}
              className="flex w-full items-center justify-center rounded-[10px] bg-primary px-5 py-3 font-semibold text-on-accent shadow-[var(--glow-primary)] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
            >
              <StableAsyncButtonContent
                loading={submitting}
                idleLabel={t("signIn")}
                loadingLabel={t("signingIn")}
                iconSize={17}
              />
            </button>
          </form>

          <p className="mt-6 text-center font-mono text-[0.625rem] uppercase tracking-wider text-text-muted">
            {tb("slogan")}
          </p>
        </section>
      </div>
    </main>
  );
}
