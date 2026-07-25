"use client";

import { useAuth } from "@/components/auth/AuthProvider";
import ServerAccountBar from "@/components/auth/ServerAccountBar";
import { TopBarNavigationProvider } from "@/components/layout/TopBarNavigationContext";

/**
 * Server mode reserves a small, persistent account strip above every screen,
 * including full-screen project/series editors. Desktop mode returns the
 * original tree byte-for-byte so its viewport sizing stays unchanged.
 */
export default function AuthenticatedViewport({ children }: { children: React.ReactNode }) {
  const { serverMode } = useAuth();
  if (!serverMode) return children;

  return (
    <TopBarNavigationProvider>
      <div className="flex h-[100dvh] w-full flex-col overflow-hidden bg-background">
        <ServerAccountBar />
        <div className="min-h-0 flex-1 overflow-hidden [&>div]:!h-full [&>main]:!h-full [&>main]:!w-full">
          {children}
        </div>
      </div>
    </TopBarNavigationProvider>
  );
}
