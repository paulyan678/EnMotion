"use client";

import { ChevronLeft } from "lucide-react";
import clsx from "clsx";
import { useTranslations } from "next-intl";

export interface BreadcrumbSegment {
  label: string;
  hash?: string;
}

interface BreadcrumbBarProps {
  segments: BreadcrumbSegment[];
  currentContent?: React.ReactNode;
  description?: string;
  actions?: React.ReactNode;
  embedded?: boolean;
}

export default function BreadcrumbBar({
  segments,
  currentContent,
  description,
  actions,
  embedded = false,
}: BreadcrumbBarProps) {
  const tc = useTranslations("common");
  const handleBack = () => {
    if (segments.length >= 2 && segments[segments.length - 2].hash) {
      window.location.hash = segments[segments.length - 2].hash!;
    } else if (segments[0]?.hash) {
      window.location.hash = segments[0].hash;
    } else {
      window.location.hash = "";
    }
  };

  return (
    <div
      className={clsx(
        "relative z-30 flex min-w-0 items-center gap-2.5",
        embedded
          ? "h-full"
          : "border-b border-glass-border bg-surface/80 px-4 py-2.5 backdrop-blur-sm",
      )}
    >
      {/* Back arrow */}
      <button
        onClick={handleBack}
        className="flex shrink-0 items-center rounded-lg p-1.5 text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground"
        title={tc("back")}
      >
        <ChevronLeft size={18} />
      </button>

      {/* Breadcrumb segments */}
      <nav className={clsx(
        "flex min-w-0 items-center gap-1.5 overflow-hidden text-sm",
        description ? "flex-1 lg:max-w-[60%] lg:flex-none" : "flex-1",
      )}>
        {segments.map((seg, i) => {
          const isLast = i === segments.length - 1;
          return (
            <span
              key={i}
              className={clsx(
                "flex min-w-0 items-center gap-1.5",
                isLast ? "flex-1" : "shrink-0",
              )}
            >
              {i > 0 && <span className="text-text-muted flex-shrink-0">&rsaquo;</span>}
              {seg.hash && !isLast ? (
                <a
                  href={seg.hash}
                  title={seg.label}
                  className="truncate rounded-md px-1.5 py-1 text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground"
                >
                  {seg.label}
                </a>
              ) : (
                <span
                  title={seg.label}
                  aria-current={isLast ? "page" : undefined}
                  className={clsx(
                    "flex min-w-0 items-center rounded-md px-1.5 py-1",
                    isLast ? "font-medium text-foreground" : "text-text-secondary",
                  )}
                >
                  {isLast && currentContent ? currentContent : (
                    <span className="truncate">{seg.label}</span>
                  )}
                </span>
              )}
            </span>
          );
        })}
      </nav>

      {description && (
        <span
          data-testid="top-bar-description"
          title={description}
          className="hidden min-w-0 flex-1 truncate border-l border-glass-border pl-3 text-xs text-text-muted lg:block"
        >
          {description}
        </span>
      )}

      {/* Right-side actions */}
      {actions && (
        <div className="flex flex-shrink-0 items-center gap-1 border-l border-glass-border pl-2">
          {actions}
        </div>
      )}
    </div>
  );
}
