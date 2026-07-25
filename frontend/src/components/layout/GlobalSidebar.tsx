"use client";

import { Activity, LayoutGrid, Layers, Wand2, Settings } from "lucide-react";
import { useTranslations } from "next-intl";
import clsx from "clsx";

export type GlobalTab = "workspace" | "library" | "playground" | "apiCalls" | "settings";

interface GlobalSidebarProps {
  activeTab: GlobalTab;
  onTabChange: (tab: GlobalTab) => void;
}

// Shared global nav model (workspace/library/playground + settings). Reused by
// the desktop GlobalSidebar (below) and the mobile BottomTabBar (md:hidden).
export const GLOBAL_NAV_ITEMS: { id: GlobalTab; icon: typeof LayoutGrid; hash: string }[] = [
  { id: "workspace", icon: LayoutGrid, hash: "#/" },
  { id: "library", icon: Layers, hash: "#/library" },
  { id: "playground", icon: Wand2, hash: "#/playground" },
  { id: "apiCalls", icon: Activity, hash: "#/api-calls" },
  { id: "settings", icon: Settings, hash: "#/settings" },
];

function navigateToHash(hash: string) {
  window.location.hash = hash;
}

function NavButton({
  active,
  label,
  icon: Icon,
  onClick,
}: {
  active: boolean;
  label: string;
  icon: typeof LayoutGrid;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={clsx(
        "group relative flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-left transition-colors",
        active
          ? "bg-primary/10 text-foreground font-semibold"
          : "text-text-secondary hover:bg-hover-bg hover:text-foreground font-medium"
      )}
    >
      {/* Active accent bar */}
      {active && (
        <span className="absolute left-0 top-1/2 -translate-y-1/2 h-[18px] w-[3px] rounded-r bg-primary" />
      )}
      <Icon
        size={18}
        strokeWidth={1.8}
        className={clsx(
          "flex-shrink-0 transition-colors",
          active ? "text-primary" : "text-text-muted group-hover:text-foreground"
        )}
      />
      <span className="text-base">{label}</span>
    </button>
  );
}

/**
 * 全局导航侧栏。
 *
 * 主导航从侧栏顶部开始，设置固定在底部。结构对所有主题统一，
 * 视觉身份由语义 token 切换（zero-leak）。
 */
export default function GlobalSidebar({ activeTab, onTabChange }: GlobalSidebarProps) {
  const t = useTranslations("nav");

  const handleNav = (id: GlobalTab, hash: string) => {
    onTabChange(id);
    navigateToHash(hash);
  };

  return (
    <aside className="w-52 flex-shrink-0 h-full hidden md:flex flex-col border-r border-glass-border bg-surface/60 backdrop-blur-xl">
      {/* Primary navigation */}
      <nav className="flex-1 flex flex-col gap-0.5 p-2.5" aria-label={t("mainNavAria")}>
        {GLOBAL_NAV_ITEMS.filter((item) => item.id !== "settings").map((item) => (
          <NavButton
            key={item.id}
            active={activeTab === item.id}
            label={t(item.id)}
            icon={item.icon}
            onClick={() => handleNav(item.id, item.hash)}
          />
        ))}
      </nav>

      {/* Settings pinned bottom */}
      <div className="p-2.5 border-t border-glass-border">
        <NavButton
          active={activeTab === "settings"}
          label={t("settings")}
          icon={Settings}
          onClick={() => handleNav("settings", "#/settings")}
        />
      </div>
    </aside>
  );
}
