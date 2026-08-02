"use client";

import { Check, CircleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import {
  APPROVED_NEWAPI_MODELS,
  getModelTranslationKey,
  type NewApiSecretField,
} from "@/lib/newApiModels";
import { FieldLabel, KeyField, settingsInputClass } from "./SettingsControls";

interface NewApiModelManagerProps {
  baseUrl: string;
  replacements: Partial<Record<NewApiSecretField, string>>;
  configured: Partial<Record<NewApiSecretField, boolean>>;
  onBaseUrlChange: (value: string) => void;
  onSecretChange: (field: NewApiSecretField, value: string) => void;
}

export default function NewApiModelManager({
  baseUrl,
  replacements,
  configured,
  onBaseUrlChange,
  onSecretChange,
}: NewApiModelManagerProps) {
  const t = useTranslations("settings");
  const tm = useTranslations("models");

  return (
    <div className="space-y-5">
      <div>
        <FieldLabel>{t("baseUrlField")}</FieldLabel>
        <input
          type="url"
          value={baseUrl}
          onChange={(event) => onBaseUrlChange(event.target.value)}
          placeholder="https://example.com/v1"
          className={settingsInputClass + " font-mono"}
        />
      </div>

      <div className="overflow-hidden rounded-xl border border-glass-border">
        <div className="hidden grid-cols-[1.35fr_.55fr_.65fr_1.45fr] gap-3 border-b border-glass-border bg-hover-bg px-4 py-2 text-[0.625rem] font-semibold uppercase tracking-wider text-text-muted lg:grid">
          <span>{t("modelDisplayName")}</span>
          <span>{t("capability")}</span>
          <span>{t("configuredStatus")}</span>
          <span>{t("replacementApiKey")}</span>
        </div>
        {APPROVED_NEWAPI_MODELS.map((model) => {
          const isConfigured = configured[model.secretField] === true;
          const translationKey = getModelTranslationKey(model.id)!;
          return (
            <div
              key={model.id}
              className="grid gap-3 border-b border-glass-border bg-surface px-4 py-4 last:border-b-0 lg:grid-cols-[1.35fr_.55fr_.65fr_1.45fr] lg:items-center"
            >
              <div>
                <div className="text-sm font-semibold text-foreground">{tm(`${translationKey}.name`)}</div>
                <div className="mt-1 text-[0.6875rem] text-text-muted">{tm(`${translationKey}.description`)}</div>
              </div>
              <span className="w-fit rounded-full bg-primary/10 px-2 py-1 text-[0.625rem] font-semibold uppercase text-primary">
                {t(`capability${model.capability[0].toUpperCase()}${model.capability.slice(1)}`)}
              </span>
              <div className="space-y-1 text-xs">
                <span className={`flex items-center gap-1.5 ${isConfigured ? "text-emerald-400" : "text-amber-400"}`}>
                  {isConfigured ? <Check size={13} /> : <CircleAlert size={13} />}
                  {isConfigured ? t("configured") : t("notConfigured")}
                </span>
              </div>
              <div>
                <KeyField
                  value={replacements[model.secretField] ?? ""}
                  onChange={(value) => onSecretChange(model.secretField, value)}
                  placeholder={isConfigured ? t("enterKeyToReplace") : t("enterApiKey")}
                />
                <code className="mt-1 block text-[0.5625rem] text-text-muted">{model.secretField}</code>
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-[0.6875rem] leading-relaxed text-text-muted">{t("secretStorageHint")}</p>
    </div>
  );
}
