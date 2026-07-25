"use client";

import { LoaderCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { useAuth } from "@/components/auth/AuthProvider";
import LoginScreen from "@/components/auth/LoginScreen";
import AuthenticatedViewport from "@/components/auth/AuthenticatedViewport";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const t = useTranslations("ui.auth");

  if (status === "disabled") return children;
  if (status === "authenticated") return <AuthenticatedViewport>{children}</AuthenticatedViewport>;
  if (status === "unauthenticated") return <LoginScreen />;

  return (
    <main className="grid min-h-[100dvh] place-items-center bg-background text-foreground" aria-busy="true">
      <div className="flex items-center gap-3 text-sm text-text-secondary">
        <LoaderCircle className="animate-spin text-primary" size={20} />
        {t("openingWorkspace")}
      </div>
    </main>
  );
}
