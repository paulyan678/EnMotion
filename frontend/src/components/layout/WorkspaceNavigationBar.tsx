"use client";

import { PanelTopClose, PanelTopOpen } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import BreadcrumbBar from "@/components/layout/BreadcrumbBar";
import { useTopBarNavigation } from "@/components/layout/TopBarNavigationContext";

const STORAGE_KEY = "enmotion:workspace-navigation-collapsed";

function readCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.sessionStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export default function WorkspaceNavigationBar() {
  const t = useTranslations("common");
  const { navigation } = useTopBarNavigation();
  const [collapsed, setCollapsed] = useState(readCollapsed);

  useEffect(() => {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, String(collapsed));
    } catch {
      // Session persistence is a convenience; navigation remains functional.
    }
  }, [collapsed]);

  if (!navigation) return null;

  if (collapsed) {
    return (
      <div className="relative z-[80] h-0 shrink-0" data-testid="workspace-navigation-collapsed">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          aria-label={t("restoreTopNavigation")}
          title={t("restoreTopNavigation")}
          className="absolute left-1/2 top-0 grid h-8 w-12 -translate-x-1/2 place-items-center rounded-b-xl border border-t-0 border-glass-border bg-surface/95 text-text-muted shadow-lg backdrop-blur-xl transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
        >
          <PanelTopOpen size={15} />
        </button>
      </div>
    );
  }

  return (
    <header className="relative z-40 flex h-11 shrink-0 items-center gap-2 border-b border-glass-border bg-surface/70 px-3 backdrop-blur-xl md:px-5">
      <div className="min-w-0 flex-1 self-stretch">
        <BreadcrumbBar
          segments={navigation.segments}
          currentContent={navigation.currentContent}
          description={navigation.description}
          actions={navigation.actions}
          embedded
        />
      </div>
      <button
        type="button"
        onClick={() => setCollapsed(true)}
        aria-label={t("collapseTopNavigation")}
        title={t("collapseTopNavigation")}
        className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-text-muted transition-colors hover:bg-hover-bg hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
      >
        <PanelTopClose size={16} />
      </button>
    </header>
  );
}
