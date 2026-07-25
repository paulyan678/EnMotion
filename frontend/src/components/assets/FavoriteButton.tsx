"use client";

import { Loader2, Star } from "lucide-react";
import { useTranslations } from "next-intl";
import type { KeyboardEvent, MouseEvent } from "react";

interface FavoriteButtonProps {
  pressed: boolean;
  pending?: boolean;
  disabled?: boolean;
  variant?: "icon" | "labeled";
  className?: string;
  onChange: (desired: boolean) => void;
}

/**
 * Shared asset-level favorite control.
 *
 * This deliberately models an explicit desired state. Variant favorites use
 * their own APIs and must not be routed through this component.
 */
export default function FavoriteButton({
  pressed,
  pending = false,
  disabled = false,
  variant = "icon",
  className = "",
  onChange,
}: FavoriteButtonProps) {
  const t = useTranslations("library");
  const blocked = disabled || pending;
  const label = pressed ? t("removeFromFavorites") : t("addToFavorites");

  const activate = () => {
    if (!blocked) onChange(!pressed);
  };

  const stopNestedActivation = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    activate();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate();
    }
  };

  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={pressed}
      aria-busy={pending}
      disabled={blocked}
      onClick={stopNestedActivation}
      onKeyDown={handleKeyDown}
      className={[
        "group/favorite relative inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-full border shadow-lg",
        "transition-[background-color,border-color,color,box-shadow] duration-150",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
        "disabled:cursor-not-allowed disabled:opacity-70",
        pressed
          ? "border-status-starred-border bg-status-starred-bg text-status-starred-fg"
          : "border-foreground/45 bg-black/85 text-white hover:border-status-starred-border hover:text-status-starred-fg",
        variant === "labeled"
          ? "gap-2 px-3.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em]"
          : "h-11 w-11 p-0",
        className,
      ].join(" ")}
    >
      {pending ? (
        <Loader2 size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
      ) : (
        <Star size={16} className={pressed ? "fill-current" : ""} aria-hidden="true" />
      )}
      {variant === "labeled" ? (
        <span className="whitespace-nowrap">
          {pressed ? t("favorited") : t("addFavorite")}
        </span>
      ) : null}
    </button>
  );
}
