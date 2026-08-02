"use client";

import { useAuth } from "@/components/auth/AuthProvider";
import AccountControl from "@/components/auth/AccountControl";
import AccountMenu from "@/components/auth/AccountMenu";

export default function AccountNavigationControls({ compact = false }: { compact?: boolean }) {
  const { serverMode, status, user } = useAuth();
  if (!serverMode || status !== "authenticated" || !user) return null;

  return (
    <div
      data-testid="navigation-account-controls"
      className={compact
        ? "flex flex-wrap items-center gap-2 border-b border-glass-border bg-surface/45 px-4 py-3 md:hidden"
        : "space-y-2 border-t border-glass-border p-2.5"}
    >
      <AccountControl enabled={Boolean(user.id)} />
      <AccountMenu className={compact ? "min-w-44 flex-1" : "w-full"} />
    </div>
  );
}
