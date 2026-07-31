import type { ReactNode } from "react";

import GlobalPageTitle from "./GlobalPageTitle";

interface GlobalPageHeaderProps {
  title: ReactNode;
  actions?: ReactNode;
}

/**
 * Canonical header for the four primary application pages.
 *
 * Keeping the title, responsive alignment, and vertical rhythm here prevents
 * individual pages from drifting or reintroducing page-specific dividers.
 */
export default function GlobalPageHeader({
  title,
  actions,
}: GlobalPageHeaderProps) {
  return (
    <header
      data-global-page-header
      className="flex shrink-0 flex-col gap-3 px-4 pb-3 pt-5 md:flex-row md:items-end md:gap-5 md:px-7 md:pt-6"
    >
      <div className="min-w-0 flex-1">
        <GlobalPageTitle>{title}</GlobalPageTitle>
      </div>
      {actions ? (
        <div className="flex flex-wrap items-center gap-2.5 md:pb-1">
          {actions}
        </div>
      ) : null}
    </header>
  );
}
