"use client";

import { useSettingsStore, type ThemePreset } from "@/store/settingsStore";
import { useHydrated } from "@/lib/useHydrated";
import { useTranslations } from "next-intl";

interface EnMotionBrandingProps {
  size?: "sm" | "md";
  showSlogan?: boolean;
}

// The official outlined lockups preserve identical typography across platforms.
const LOGO_SRC: Record<ThemePreset, string> = {
  "atelier-dark": "enmotion-lockup-on-dark.svg",
  "bridge-dark": "enmotion-lockup-on-dark.svg",
  "brand-dark": "enmotion-lockup-on-dark.svg",
  "atelier-light": "enmotion-lockup.svg",
  "brand-light": "enmotion-lockup.svg",
};

export default function EnMotionBranding({ size = "md", showSlogan = true }: EnMotionBrandingProps) {
  const t = useTranslations("ui.brand");
  const logoSize = size === "sm" ? "w-[9.75rem]" : "w-[13.5rem]";

  const theme = useSettingsStore((s) => s.theme);
  // Keep SSR and the first client render on the default theme to avoid a
  // hydration mismatch, then switch to the user's selected theme.
  const mounted = useHydrated();
  const activeTheme: ThemePreset = mounted ? theme : "atelier-dark";
  const logoSrc = LOGO_SRC[activeTheme] ?? "enmotion-lockup-on-dark.svg";

  return (
    <div className="inline-flex flex-col">
      <img
        src={logoSrc}
        alt={t("documentTitle")}
        className={`${logoSize} h-auto object-contain object-left`}
      />
      {showSlogan && (
        <p className="mt-2 text-center font-mono atelier-display text-[0.5rem] uppercase tracking-[0.15em] text-text-muted">
          {t("slogan")}
        </p>
      )}
    </div>
  );
}
