"use client";

import { useTranslations } from "next-intl";

import { getApprovedModels, getModelTranslationKey } from "@/lib/newApiModels";

interface ChatModelSelectProps {
  id: string;
  label: string;
  value: string;
  onChange: (modelId: string) => void;
  disabled?: boolean;
  className?: string;
}

const CHAT_MODELS = getApprovedModels("chat");

/**
 * Compact, generation-time chat-model choice shared by every prompt-polish
 * surface. The selected value is sent with the next provider request; it is
 * intentionally not persisted as a hidden project or series default.
 */
export default function ChatModelSelect({
  id,
  label,
  value,
  onChange,
  disabled = false,
  className = "",
}: ChatModelSelectProps) {
  const tm = useTranslations("models");

  return (
    <label htmlFor={id} className={`flex min-w-0 items-center gap-2 ${className}`.trim()}>
      <span className="shrink-0 text-xs text-text-muted">{label}</span>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        className="min-h-9 min-w-0 rounded-lg border border-glass-border bg-surface px-2.5 py-1.5 text-xs text-foreground outline-none transition focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/40 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {CHAT_MODELS.map((model) => {
          const translationKey = getModelTranslationKey(model.id);
          return (
            <option key={model.id} value={model.id}>
              {translationKey ? tm(`${translationKey}.name`) : model.name}
            </option>
          );
        })}
      </select>
    </label>
  );
}
