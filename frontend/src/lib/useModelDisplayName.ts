"use client";

import { useCallback } from "react";
import { useTranslations } from "next-intl";

import { getModelTranslationKey } from "@/lib/newApiModels";

/**
 * Resolve an internal provider model identifier to localized, user-facing
 * copy. Unknown identifiers deliberately fall back to a generic label so an
 * implementation detail never leaks into the interface.
 */
export function useModelDisplayName() {
  const t = useTranslations("models");

  return useCallback((modelId?: string | null, fallback?: string) => {
    const translationKey = modelId ? getModelTranslationKey(modelId) : undefined;
    return translationKey ? t(`${translationKey}.name`) : (fallback ?? t("unknownModel"));
  }, [t]);
}
