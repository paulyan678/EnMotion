"use client";

import { RotateCcw } from "lucide-react";
import { useTranslations } from "next-intl";

interface ModelSettingInheritanceButtonProps {
  overridden: boolean;
  onInherit: () => void;
}

export default function ModelSettingInheritanceButton({
  overridden,
  onInherit,
}: ModelSettingInheritanceButtonProps) {
  const t = useTranslations("models");

  return (
    <button
      type="button"
      aria-pressed={!overridden}
      disabled={!overridden}
      onClick={onInherit}
      className="inline-flex min-h-9 shrink-0 items-center gap-1 rounded-lg border border-glass-border px-2.5 py-1.5 text-xs text-text-secondary transition-colors hover:border-primary/40 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-default disabled:border-primary/20 disabled:bg-primary/10 disabled:text-primary"
    >
      <RotateCcw size={12} aria-hidden="true" />
      {t(overridden ? "inheritSetting" : "inheritedSetting")}
    </button>
  );
}
