"use client";

import { useEffect, useRef } from "react";
import { useAuth } from "@/components/auth/AuthProvider";
import WorkspaceNavigationBar from "@/components/layout/WorkspaceNavigationBar";
import { TopBarNavigationProvider } from "@/components/layout/TopBarNavigationContext";
import { useUpdater } from "@/components/update/UpdaterProvider";

function AuthenticatedUpdateCheck() {
  const { status } = useAuth();
  const { supported, checkForUpdates } = useUpdater();
  const checked = useRef(false);

  useEffect(() => {
    if (status !== "authenticated") {
      checked.current = false;
      return;
    }
    if (!supported || checked.current) return;
    checked.current = true;
    void checkForUpdates();
  }, [checkForUpdates, status, supported]);

  return null;
}

/**
 * Full-screen editors register their path in a collapsible workspace bar.
 * Primary application pages register nothing, so they reserve no top strip.
 */
export default function AuthenticatedViewport({ children }: { children: React.ReactNode }) {
  return (
    <TopBarNavigationProvider>
      <div className="flex h-[100dvh] w-full flex-col overflow-hidden bg-background">
        <AuthenticatedUpdateCheck />
        <WorkspaceNavigationBar />
        <div className="min-h-0 flex-1 overflow-hidden [&>div]:!h-full [&>main]:!h-full [&>main]:!w-full">
          {children}
        </div>
      </div>
    </TopBarNavigationProvider>
  );
}
