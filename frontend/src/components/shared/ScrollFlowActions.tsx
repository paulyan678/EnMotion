"use client";

import clsx from "clsx";
import type { ReactNode } from "react";

interface ScrollFlowActionsProps {
  children: ReactNode;
  className?: string;
  align?: "start" | "between" | "end";
  label?: string;
}

/**
 * A lightweight action row that intentionally belongs to its screen's scroll
 * owner. Unlike StepPageHeader it never reserves fixed vertical space and it
 * naturally leaves the viewport with the content it controls.
 */
export default function ScrollFlowActions({
  children,
  className,
  align = "end",
  label,
}: ScrollFlowActionsProps) {
  return (
    <div
      role={label ? "toolbar" : undefined}
      aria-label={label}
      data-scroll-flow-actions="true"
      className={clsx(
        "flex min-w-0 flex-wrap items-center gap-2",
        align === "start" && "justify-start",
        align === "between" && "justify-between",
        align === "end" && "justify-end",
        className,
      )}
    >
      {children}
    </div>
  );
}
