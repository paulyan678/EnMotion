"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Loader2, Save, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { api, type EnvConfigPayload } from "@/lib/api";
import NewApiModelManager from "@/components/settings/NewApiModelManager";
import {
  DEFAULT_ACTIVE_MODELS,
  buildSecretReplacementPatch,
  configuredSecretFields,
  getNewApiValidationErrors,
  normalizeActiveModel,
  type ActiveNewApiSelection,
  type NewApiCapability,
  type NewApiSecretField,
} from "@/lib/newApiModels";

interface EnvConfigDialogProps {
  isOpen: boolean;
  onClose: () => void;
  isRequired?: boolean;
}

interface NewApiDialogConfig {
  baseUrl: string;
  active: ActiveNewApiSelection;
}

const DEFAULT_CONFIG: NewApiDialogConfig = {
  baseUrl: "",
  active: { ...DEFAULT_ACTIVE_MODELS },
};

function normalizeConfig(payload?: EnvConfigPayload): NewApiDialogConfig {
  return {
    baseUrl: payload?.NEWAPI_BASE_URL?.trim() || DEFAULT_CONFIG.baseUrl,
    active: {
      chat: normalizeActiveModel("chat", payload?.NEWAPI_CHAT_MODEL),
      image: normalizeActiveModel("image", payload?.NEWAPI_IMAGE_MODEL),
      video: normalizeActiveModel("video", payload?.NEWAPI_VIDEO_MODEL),
    },
  };
}

export default function EnvConfigDialog({
  isOpen,
  onClose,
  isRequired = false,
}: EnvConfigDialogProps) {
  const t = useTranslations("project");
  const tc = useTranslations("common");
  const [config, setConfig] = useState<NewApiDialogConfig>(DEFAULT_CONFIG);
  const [configured, setConfigured] = useState<Partial<Record<NewApiSecretField, boolean>>>({});
  const [replacements, setReplacements] = useState<Partial<Record<NewApiSecretField, string>>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadConfig = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const payload = await api.getEnvConfig();
      setConfig(normalizeConfig(payload));
      setConfigured(configuredSecretFields(payload as Record<string, unknown>));
      // Saved credentials never become input values. A user must explicitly
      // enter a replacement each time they want to rotate a key.
      setReplacements({});
    } catch {
      setLoadError(t("configLoadFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) void loadConfig();
  }, [isOpen]);

  const validationErrors = getNewApiValidationErrors(
    config.baseUrl,
    config.active,
    configured,
    replacements,
  );
  const canClose = !isRequired || validationErrors.length === 0;

  const handleSave = async () => {
    if (validationErrors.length > 0) {
      alert(t("requiredFields") + "\n- " + validationErrors.join("\n- "));
      return;
    }

    setSaving(true);
    try {
      await api.saveEnvConfig({
        NEWAPI_BASE_URL: config.baseUrl,
        NEWAPI_CHAT_MODEL: config.active.chat,
        NEWAPI_IMAGE_MODEL: config.active.image,
        NEWAPI_VIDEO_MODEL: config.active.video,
        ...buildSecretReplacementPatch(replacements),
      });
      setReplacements({});
      await loadConfig();
      alert(t("configSaved"));
      onClose();
    } catch {
      alert(t("configSaveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const requestClose = () => {
    if (canClose) onClose();
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-overlay p-4 backdrop-blur-sm"
          onClick={requestClose}
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 12 }}
            className="flex max-h-[92vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-glass-border bg-elevated shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="flex items-start justify-between border-b border-glass-border p-5">
              <div>
                <h2 className="text-lg font-bold text-foreground">{t("envConfig")}</h2>
                <p className="mt-1 text-xs text-text-secondary">{t("envConfigSub")}</p>
              </div>
              <button
                type="button"
                onClick={requestClose}
                disabled={!canClose}
                className="rounded-lg p-2 text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground disabled:cursor-not-allowed disabled:opacity-30"
                aria-label={tc("close")}
              >
                <X size={20} />
              </button>
            </header>

            <div className="overflow-y-auto p-5">
              {loading ? (
                <div className="flex items-center justify-center py-20 text-text-secondary">
                  <Loader2 size={24} className="mr-2 animate-spin text-primary" />
                  {t("loadingConfig")}
                </div>
              ) : loadError ? (
                <div className="rounded-lg border border-status-failed-border bg-status-failed-bg p-4 text-sm text-status-failed-fg">
                  {loadError}
                </div>
              ) : (
                <>
                  <NewApiModelManager
                    baseUrl={config.baseUrl}
                    active={config.active}
                    replacements={replacements}
                    configured={configured}
                    onBaseUrlChange={(baseUrl) => setConfig((current) => ({ ...current, baseUrl }))}
                    onActiveChange={(capability: NewApiCapability, modelId: string) => {
                      setConfig((current) => ({
                        ...current,
                        active: { ...current.active, [capability]: modelId },
                      }));
                    }}
                    onSecretChange={(field, value) => {
                      setReplacements((current) => ({ ...current, [field]: value }));
                    }}
                  />
                  {isRequired && validationErrors.length > 0 && (
                    <div className="mt-4 rounded-lg border border-amber-400/20 bg-amber-400/10 p-3 text-xs text-amber-300">
                      <p className="font-semibold">{t("requiredHint")}</p>
                      <ul className="mt-2 list-disc space-y-1 pl-5">
                        {validationErrors.map((error) => <li key={error}>{error}</li>)}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </div>

            <footer className="flex items-center justify-between border-t border-glass-border bg-surface p-5">
              <p className="text-[0.6875rem] text-text-muted">
                {isRequired && !canClose ? t("cannotClose") : ""}
              </p>
              <div className="flex gap-3">
                {canClose && (
                  <button
                    type="button"
                    onClick={onClose}
                    className="px-4 py-2 text-sm text-text-secondary transition-colors hover:text-foreground"
                  >
                    {tc("cancel")}
                  </button>
                )}
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={saving || loading || Boolean(loadError)}
                  className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-on-accent transition-colors hover:bg-primary-hover disabled:opacity-50"
                >
                  {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                  {saving ? t("savingConfig") : t("saveConfig")}
                </button>
              </div>
            </footer>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
