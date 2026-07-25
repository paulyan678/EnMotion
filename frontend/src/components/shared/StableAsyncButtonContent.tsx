"use client";

import { LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";

interface StableAsyncButtonContentProps {
  loading: boolean;
  idleLabel: ReactNode;
  loadingLabel: ReactNode;
  idleIcon?: ReactNode;
  iconSize?: number;
}

/**
 * Keeps both button labels in one stable grid cell and intentionally avoids a
 * transform-based spinner. WebKit can leave stale text layers behind when an
 * animated SVG is composited inside a blurred or glowing button.
 */
export default function StableAsyncButtonContent({
  loading,
  idleLabel,
  loadingLabel,
  idleIcon,
  iconSize = 16,
}: StableAsyncButtonContentProps) {
  const stateClassName =
    "col-start-1 row-start-1 inline-flex items-center justify-center gap-2 whitespace-nowrap";

  return (
    <span className="inline-grid grid-cols-1 grid-rows-1 place-items-center isolate [contain:layout_paint]">
      <span
        data-async-button-state="idle"
        aria-hidden={loading}
        className={`${stateClassName} ${loading ? "invisible" : "visible"}`}
      >
        {idleIcon}
        <span>{idleLabel}</span>
      </span>
      <span
        data-async-button-state="loading"
        aria-hidden={!loading}
        role={loading ? "status" : undefined}
        aria-live={loading ? "polite" : undefined}
        className={`${stateClassName} ${loading ? "visible" : "invisible"}`}
      >
        <LoaderCircle
          data-loading-indicator="static"
          className="shrink-0"
          size={iconSize}
          aria-hidden="true"
        />
        <span>{loadingLabel}</span>
      </span>
    </span>
  );
}
