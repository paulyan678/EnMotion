"use client";

import { useState, useEffect, useCallback, type ReactNode } from "react";
import { Save, Loader2, WifiOff } from "lucide-react";
import { useTranslations } from "next-intl";
import { api, type EnvConfigPayload } from "@/lib/api";
import { ASPECT_RATIOS } from "@/store/projectStore";
import {
  DEFAULT_MODEL_SETTINGS,
  GLOBAL_CHAT_MODELS,
  GLOBAL_I2V_MODELS,
  GLOBAL_IMAGE_MODELS,
  normalizeModelSettings,
  type FrontendModelSettings,
} from "@/lib/modelCatalog";
import { useSettingsStore, type ThemePreset } from "@/store/settingsStore";
import { toast } from "@/store/toastStore";
import { rovingKeyDown } from "@/lib/a11y";
import { Image, Video, Layout, User, Building, Box } from "lucide-react";
import GroupedModelGrid from "@/components/common/GroupedModelGrid";
import ApiKeyInspector from "./ApiKeyInspector";
import NewApiModelManager from "./NewApiModelManager";
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
import { readWorkspaceItem, writeWorkspaceItem } from "@/lib/workspaceStorage";
import { useAuth } from "@/components/auth/AuthProvider";
import UpdateSettingsCard from "@/components/update/UpdateSettingsCard";
import { isHybridModeEnabled } from "@/lib/serverMode";
type SettingsCategory = "general" | "models" | "prompts" | "apikeys";
import {
  FormRow,
  Toggle,
} from "./SettingsControls";

type EnvConfig = EnvConfigPayload & {
  NEWAPI_BASE_URL: string;
  NEWAPI_CHAT_MODEL: string;
  NEWAPI_IMAGE_MODEL: string;
  NEWAPI_VIDEO_MODEL: string;
};

const DEFAULT_CONFIG: EnvConfig = {
  NEWAPI_BASE_URL: "",
  NEWAPI_CHAT_MODEL: DEFAULT_ACTIVE_MODELS.chat,
  NEWAPI_IMAGE_MODEL: DEFAULT_ACTIVE_MODELS.image,
  NEWAPI_VIDEO_MODEL: DEFAULT_ACTIVE_MODELS.video,
};

const normalizeEnvConfig = (existing: EnvConfig, data?: EnvConfigPayload): EnvConfig => ({
  ...existing,
  NEWAPI_BASE_URL: data?.NEWAPI_BASE_URL?.trim() || existing.NEWAPI_BASE_URL,
  NEWAPI_CHAT_MODEL: normalizeActiveModel("chat", data?.NEWAPI_CHAT_MODEL),
  NEWAPI_IMAGE_MODEL: normalizeActiveModel("image", data?.NEWAPI_IMAGE_MODEL),
  NEWAPI_VIDEO_MODEL: normalizeActiveModel("video", data?.NEWAPI_VIDEO_MODEL),
});

const LS_KEY_MODEL = "enmotion_default_model_settings";
const LS_KEY_PROMPT = "enmotion_default_prompt_config";

interface DefaultPromptConfig {
  storyboard_polish: string;
  video_polish: string;
  entity_extraction: string;
  style_analysis: string;
  storyboard_extraction: string;
}

const EMPTY_PROMPT_CONFIG: DefaultPromptConfig = {
  storyboard_polish: "",
  video_polish: "",
  entity_extraction: "",
  style_analysis: "",
  storyboard_extraction: "",
};

function loadFromLS<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = readWorkspaceItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
}

// `name` / `desc` hold i18n keys (relative to the `settings` namespace) so the
// module-scope list can be resolved with t(...) at render time.
const THEME_OPTIONS: { id: ThemePreset; name: string; desc: string; base: string; primary: string; accent: string }[] = [
  { id: "atelier-dark",  name: "themeAtelierDark",  desc: "themeAtelierDarkDesc",  base: "#0c0b0e", primary: "#34d8c4", accent: "#ffa94d" },
  { id: "bridge-dark",   name: "themeBridgeDark",   desc: "themeBridgeDarkDesc",   base: "#0a0a0d", primary: "#646cff", accent: "#ffa94d" },
  { id: "brand-dark",    name: "themeBrandDark",    desc: "themeBrandDarkDesc",    base: "#050508", primary: "#646cff", accent: "#ff0080" },
  { id: "atelier-light", name: "themeAtelierLight", desc: "themeAtelierLightDesc", base: "#f6f1e9", primary: "#1d9c8d", accent: "#e8852b" },
  { id: "brand-light",   name: "themeBrandLight",   desc: "themeBrandLightDesc",   base: "#f8f9fa", primary: "#646cff", accent: "#ff0080" },
];

/* Atelier section panel — restored per Line B mockup `.panel` (translucent
   warm-graphite card via glass-panel + atelier-card: surface + blur + soft
   shadow + hairline border, so sections read as distinct grouped cards).
   The page <header> stays frameless (mockup .main-head has no bg); only the
   content sections are carded. Model cards / inputs keep their own surfaces. */
function Section({
  id,
  title,
  desc,
  children,
}: {
  id?: string;
  title: string;
  desc?: string;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      aria-labelledby={id ? `${id}-title` : undefined}
      className="glass-panel atelier-card rounded-[20px] overflow-hidden"
    >
      <div className="atelier-card-head px-[22px] pt-[18px] pb-3.5 border-b border-glass-border">
        <h2
          id={id ? `${id}-title` : undefined}
          className="font-display atelier-display text-[1.1875rem] font-semibold text-foreground tracking-tight"
        >
          {title}
        </h2>
        {desc && <p className="text-[0.75rem] text-text-secondary mt-1 leading-relaxed">{desc}</p>}
      </div>
      <div className="px-[22px] pt-[18px] pb-[22px]">{children}</div>
    </section>
  );
}

export default function SettingsPage() {
  const t = useTranslations("settings");
  const { theme, animations, setTheme, setAnimations } = useSettingsStore();
  const { serverMode, user } = useAuth();
  const hybridMode = isHybridModeEnabled();
  const mayManageServer = !serverMode || user?.role === "admin";
  const mayManageProviderConfig = mayManageServer && !hybridMode;
  const mayInspectApiKeys = serverMode && user?.role === "admin" && !hybridMode;

  const [active, setActive] = useState<SettingsCategory>("general");

  // ── API Config ──
  const [config, setConfig] = useState<EnvConfig>(DEFAULT_CONFIG);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [secretReplacements, setSecretReplacements] = useState<Partial<Record<NewApiSecretField, string>>>({});
  const [configuredSecrets, setConfiguredSecrets] = useState<Partial<Record<NewApiSecretField, boolean>>>({});

  // ── Default Model Settings ──
  const [modelSettings, setModelSettings] = useState<FrontendModelSettings>(() =>
    normalizeModelSettings(loadFromLS(LS_KEY_MODEL, DEFAULT_MODEL_SETTINGS), "global_settings")
  );

  // ── Default Prompt Config ──
  // `promptConfig` is the displayed/editable text. localStorage (LS_KEY_PROMPT)
  // only ever stores DELTAS: an empty value means "use the built-in default".
  // `promptDefaults` holds the real built-in defaults fetched from the backend
  // so we can pre-fill the fields and run the delta comparison on save.
  const [promptConfig, setPromptConfig] = useState<DefaultPromptConfig>(() =>
    loadFromLS(LS_KEY_PROMPT, EMPTY_PROMPT_CONFIG)
  );
  const [promptDefaults, setPromptDefaults] = useState<Record<string, string>>({});

  // ── Connectivity ──
  const [online, setOnline] = useState(true);

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api.getEnvConfig();
      const normalizedConfig = normalizeEnvConfig(DEFAULT_CONFIG, data);
      setConfig(normalizedConfig);
      setModelSettings((current) => normalizeModelSettings({
        ...current,
        chat_model: normalizedConfig.NEWAPI_CHAT_MODEL,
        t2i_model: normalizedConfig.NEWAPI_IMAGE_MODEL,
        i2i_model: normalizedConfig.NEWAPI_IMAGE_MODEL,
        image_model: normalizedConfig.NEWAPI_IMAGE_MODEL,
        i2v_model: normalizedConfig.NEWAPI_VIDEO_MODEL,
        video_model: normalizedConfig.NEWAPI_VIDEO_MODEL,
      }, "global_settings"));
      setConfiguredSecrets(configuredSecretFields(data as Record<string, unknown>));
      setSecretReplacements({});
    } catch {
      setLoadError(t("loadConfigFailed"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (mayManageProviderConfig) void loadConfig();
  }, [loadConfig, mayManageProviderConfig]);

  // Pre-fill the prompt fields with the real built-in defaults so users can see
  // and edit from them. We remember the fetched defaults for the delta-save
  // comparison, and only fill a field the user has NOT overridden (empty in LS).
  // If the fetch fails we leave the fields empty (placeholder) — no crash.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const defaults = await api.fetchPromptDefaults();
        if (cancelled || !defaults) return;
        setPromptDefaults(defaults);
        setPromptConfig((prev) => {
          const next = { ...prev };
          (Object.keys(EMPTY_PROMPT_CONFIG) as (keyof DefaultPromptConfig)[]).forEach((k) => {
            const d = defaults[k];
            if (typeof d === "string" && d && !prev[k]) next[k] = d;
          });
          return next;
        });
      } catch {
        /* defaults unavailable — fields fall back to empty placeholders */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Online/offline detection for the banner.
  useEffect(() => {
    const update = () => setOnline(typeof navigator === "undefined" ? true : navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  const handleSaveApiConfig = async () => {
    const activeSelection: ActiveNewApiSelection = {
      chat: config.NEWAPI_CHAT_MODEL,
      image: config.NEWAPI_IMAGE_MODEL,
      video: config.NEWAPI_VIDEO_MODEL,
    };
    const errors = getNewApiValidationErrors(
      config.NEWAPI_BASE_URL,
      activeSelection,
      configuredSecrets,
      secretReplacements,
    );
    if (errors.length > 0) {
      toast.error(t("fillRequired"), { body: `- ${errors.join("\n- ")}` });
      return;
    }
    setSaving(true);
    try {
      await api.saveEnvConfig({
        NEWAPI_BASE_URL: config.NEWAPI_BASE_URL,
        NEWAPI_CHAT_MODEL: activeSelection.chat,
        NEWAPI_IMAGE_MODEL: activeSelection.image,
        NEWAPI_VIDEO_MODEL: activeSelection.video,
        ...buildSecretReplacementPatch(secretReplacements),
      });
      await loadConfig();
      toast.success(t("saveSuccess"));
    } catch {
      toast.error(t("saveConfigFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleChange = (key: keyof EnvConfig, value: string) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  const handleSaveModelDefaults = async () => {
    const normalized = normalizeModelSettings(modelSettings, "global_settings");
    // T2I and I2I share one image model in the UI; persist both backend
    // fields plus image_model so per-project backfill stays consistent.
    const merged: FrontendModelSettings = {
      ...normalized,
      i2i_model: normalized.t2i_model,
      image_model: normalized.t2i_model,
      video_model: normalized.i2v_model,
    };
    if (hybridMode) {
      writeWorkspaceItem(LS_KEY_MODEL, JSON.stringify(merged));
      setModelSettings(merged);
      toast.success(t("saved"));
      return;
    }
    const activeSelection: ActiveNewApiSelection = {
      chat: merged.chat_model,
      image: merged.image_model,
      video: merged.video_model,
    };
    const errors = getNewApiValidationErrors(
      config.NEWAPI_BASE_URL,
      activeSelection,
      configuredSecrets,
      secretReplacements,
    );
    if (errors.length) {
      toast.error(t("fillRequired"), { body: `- ${errors.join("\n- ")}` });
      return;
    }
    writeWorkspaceItem(LS_KEY_MODEL, JSON.stringify(merged));
    setModelSettings(merged);
    try {
      await api.saveEnvConfig({
        NEWAPI_BASE_URL: config.NEWAPI_BASE_URL,
        NEWAPI_CHAT_MODEL: activeSelection.chat,
        NEWAPI_IMAGE_MODEL: activeSelection.image,
        NEWAPI_VIDEO_MODEL: activeSelection.video,
      });
      setConfig((current) => ({
        ...current,
        NEWAPI_CHAT_MODEL: activeSelection.chat,
        NEWAPI_IMAGE_MODEL: activeSelection.image,
        NEWAPI_VIDEO_MODEL: activeSelection.video,
      }));
      toast.success(t("saved"));
    } catch {
      toast.error(t("saveConfigFailed"));
    }
  };

  const handleSavePromptDefaults = () => {
    // DELTA persistence: a field equal to its built-in default is stored as ""
    // (=> use built-in, no snapshot pinning); only genuine overrides are saved.
    const delta: DefaultPromptConfig = { ...EMPTY_PROMPT_CONFIG };
    (Object.keys(EMPTY_PROMPT_CONFIG) as (keyof DefaultPromptConfig)[]).forEach((k) => {
      const text = promptConfig[k] ?? "";
      delta[k] = text === promptDefaults[k] ? "" : text;
    });
    writeWorkspaceItem(LS_KEY_PROMPT, JSON.stringify(delta));
    toast.success(t("saved"));
  };

  /* ── Section renderers ──────────────────────────────────────── */

  const renderGeneral = () => (
    <Section id="general" title={t("secGeneralTitle")}>
      <FormRow label={t("theme")} hint={t("themeDesc")}>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2" role="radiogroup" aria-label={t("theme")} onKeyDown={rovingKeyDown}>
          {THEME_OPTIONS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              role="radio"
              aria-checked={theme === preset.id}
              tabIndex={theme === preset.id ? 0 : -1}
              onClick={() => setTheme(preset.id)}
              className={`group relative flex flex-col gap-2 p-3 rounded-xl border text-left transition-all ${
                theme === preset.id
                  ? "border-primary/60 bg-primary/10 ring-1 ring-primary/30"
                  : "border-glass-border bg-hover-bg hover:border-text-muted"
              }`}
            >
              <div
                className="h-10 w-full rounded-lg border border-glass-border overflow-hidden flex items-end p-1.5 gap-1"
                style={{ background: preset.base }}
              >
                <span className="h-3 w-3 rounded-full" style={{ background: preset.primary }} />
                <span className="h-3 w-3 rounded-full" style={{ background: preset.accent }} />
              </div>
              <div className="min-w-0">
                <div className="text-xs font-medium text-foreground truncate">{t(preset.name)}</div>
                <div className="text-[0.625rem] text-text-muted truncate">{t(preset.desc)}</div>
              </div>
              {theme === preset.id && (
                <span className="absolute top-2 right-2 h-2 w-2 rounded-full bg-primary" />
              )}
            </button>
          ))}
        </div>
      </FormRow>

      <FormRow label={t("motionLabel")} hint={t("motionHint")}>
        <Toggle
          checked={animations}
          onChange={setAnimations}
          label={animations ? t("motionOn") : t("motionReduced")}
          sub={t("motionSub")}
          ariaLabel={t("motionToggleAria")}
        />
      </FormRow>
      <UpdateSettingsCard />
    </Section>
  );

  const aspectButtons = (key: keyof FrontendModelSettings) => (
    <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label={t("aspectRatioAria")} onKeyDown={rovingKeyDown}>
      {ASPECT_RATIOS.map((ratio) => (
        <button
          key={ratio.id}
          type="button"
          role="radio"
          aria-checked={modelSettings[key] === ratio.id}
          tabIndex={modelSettings[key] === ratio.id ? 0 : -1}
          onClick={() => setModelSettings((s) => ({ ...s, [key]: ratio.id }))}
          className={`flex flex-col items-center py-2 px-2 rounded-lg border transition-all ${
            modelSettings[key] === ratio.id
              ? "border-primary/50 bg-primary/10"
              : "border-glass-border hover:border-text-muted bg-glass"
          }`}
        >
          <span className="text-xs font-medium text-foreground">{ratio.name}</span>
        </button>
      ))}
    </div>
  );

  const renderModels = () => (
    <Section
      id="models"
      title={t("secModelsTitle")}
      desc={t("secModelsDesc")}
    >
      <FormRow label={t("chatModelLabel")} hint={t("chatModelHint")}>
        <GroupedModelGrid
          models={GLOBAL_CHAT_MODELS}
          selectedId={modelSettings.chat_model}
          onSelect={(id) => setModelSettings((settings) => ({ ...settings, chat_model: id }))}
        />
      </FormRow>

      {/* Image model (T2I + I2I unified) */}
      <FormRow label={t("imageModelLabel")} hint={t("imageModelHint")}>
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground mb-3">
          <Image size={15} className="text-emerald-400" />
          <span>{t("imageModelCaption")}</span>
        </div>
        <GroupedModelGrid
          models={GLOBAL_IMAGE_MODELS}
          selectedId={modelSettings.t2i_model}
          onSelect={(id) => setModelSettings((s) => ({ ...s, t2i_model: id, i2i_model: id, image_model: id }))}
        />
      </FormRow>

      {/* Asset aspect ratios */}
      <FormRow label={t("assetAspectLabel")} hint={t("assetAspectHint")}>
        <div className="grid grid-cols-3 gap-4">
          {(
            [
              { key: "character_aspect_ratio" as const, label: t("assetCharacter"), icon: User },
              { key: "scene_aspect_ratio" as const, label: t("assetScene"), icon: Building },
              { key: "prop_aspect_ratio" as const, label: t("assetProp"), icon: Box },
            ] as const
          ).map(({ key, label, icon: Icon }) => (
            <div key={key} className="space-y-2">
              <div className="flex items-center gap-1 text-xs text-text-secondary">
                <Icon size={12} />
                <label>{label}</label>
              </div>
              <div className="space-y-1">
                {ASPECT_RATIOS.map((ratio) => (
                  <button
                    key={ratio.id}
                    type="button"
                    onClick={() => setModelSettings((s) => ({ ...s, [key]: ratio.id }))}
                    className={`w-full flex flex-col items-center py-2 px-2 rounded border transition-all ${
                      modelSettings[key] === ratio.id
                        ? "border-emerald-500/50 bg-emerald-500/10"
                        : "border-glass-border hover:border-text-muted bg-glass"
                    }`}
                  >
                    <span className="text-xs font-medium text-foreground">{ratio.name}</span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </FormRow>

      {/* Storyboard aspect ratio */}
      <FormRow label={t("storyboardAspectLabel")} hint={t("storyboardAspectHint")}>
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground mb-3">
          <Layout size={15} className="text-primary" />
          <span>{t("storyboardAspectTitle")}</span>
        </div>
        {aspectButtons("storyboard_aspect_ratio")}
      </FormRow>

      {/* I2V */}
      <FormRow label={t("i2vModelLabel")} hint={t("i2vModelHint")}>
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground mb-3">
          <Video size={15} className="text-purple-400" />
          <span>{t("imageToVideoTitle")}</span>
        </div>
        <GroupedModelGrid
          models={GLOBAL_I2V_MODELS}
          selectedId={modelSettings.i2v_model}
          onSelect={(id) => setModelSettings((s) => ({ ...s, i2v_model: id, video_model: id }))}
        />
      </FormRow>

      <div className="flex justify-end pt-4">
        <button
          type="button"
          onClick={handleSaveModelDefaults}
          className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-on-accent text-sm font-medium rounded-lg transition-all"
        >
          <Save size={16} />
          {t("saveDefaults")}
        </button>
      </div>
    </Section>
  );

  const PROMPT_FIELDS: { key: keyof DefaultPromptConfig; label: string; desc: string }[] = [
    { key: "entity_extraction", label: t("promptEntityLabel"), desc: t("promptEntityDesc") },
    { key: "style_analysis", label: t("promptStyleLabel"), desc: t("promptStyleDesc") },
    { key: "storyboard_extraction", label: t("promptStoryboardExtractLabel"), desc: t("promptStoryboardExtractDesc") },
    { key: "storyboard_polish", label: t("promptStoryboardPolishLabel"), desc: t("promptStoryboardPolishDesc") },
    { key: "video_polish", label: t("promptVideoPolishLabel"), desc: t("promptVideoPolishDesc") },
  ];

  const renderPrompts = () => (
    <Section
      id="prompts"
      title={t("secPromptsTitle")}
      desc={t("secPromptsDesc")}
    >
      <div className="space-y-5">
        {PROMPT_FIELDS.map((f) => (
          <div key={f.key} className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">{f.label}</h3>
            <p className="text-[0.6875rem] text-text-muted">{f.desc}</p>
            <textarea
              value={promptConfig[f.key]}
              onChange={(e) => setPromptConfig((prev) => ({ ...prev, [f.key]: e.target.value }))}
              placeholder={t("promptPlaceholder")}
              className="w-full h-32 bg-input-bg border border-glass-border rounded-lg p-3 text-xs text-foreground resize-y focus:outline-none focus:border-primary/50 font-mono placeholder-text-muted"
            />
          </div>
        ))}
      </div>
      <div className="flex justify-end pt-4">
        <button
          type="button"
          onClick={handleSavePromptDefaults}
          className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-on-accent text-sm font-medium rounded-lg transition-colors"
        >
          <Save size={16} />
          {t("saveDefaults")}
        </button>
      </div>
    </Section>
  );

  const renderApiKeys = () => (
    <Section
      id="apikeys"
      title={t("secApiTitle")}
      desc={t("secApiDesc")}
    >
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 size={24} className="animate-spin text-primary" />
          <span className="ml-2 text-text-secondary">{t("loadingConfig")}</span>
        </div>
      ) : loadError ? (
        <div className="bg-status-failed-bg border border-status-failed-border rounded-lg p-4 text-sm text-status-failed-fg">
          {loadError}
        </div>
      ) : (
        <>
          <NewApiModelManager
            baseUrl={config.NEWAPI_BASE_URL}
            active={{
              chat: config.NEWAPI_CHAT_MODEL,
              image: config.NEWAPI_IMAGE_MODEL,
              video: config.NEWAPI_VIDEO_MODEL,
            }}
            replacements={secretReplacements}
            configured={configuredSecrets}
            onBaseUrlChange={(value) => handleChange("NEWAPI_BASE_URL", value)}
            onActiveChange={(capability: NewApiCapability, modelId: string) => {
              const field = capability === "chat"
                ? "NEWAPI_CHAT_MODEL"
                : capability === "image"
                  ? "NEWAPI_IMAGE_MODEL"
                  : "NEWAPI_VIDEO_MODEL";
              setConfig((current) => ({ ...current, [field]: modelId }));
              setModelSettings((current) => ({
                ...current,
                ...(capability === "chat" ? { chat_model: modelId } : {}),
                ...(capability === "image"
                  ? { image_model: modelId, t2i_model: modelId, i2i_model: modelId }
                  : {}),
                ...(capability === "video"
                  ? { video_model: modelId, i2v_model: modelId }
                  : {}),
              }));
            }}
            onSecretChange={(field, value) => {
              setSecretReplacements((current) => ({ ...current, [field]: value }));
            }}
          />
          {mayInspectApiKeys && <ApiKeyInspector />}
          <div className="flex justify-end pt-5">
            <button
              type="button"
              onClick={handleSaveApiConfig}
              disabled={saving || loading || !online}
              className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-on-accent text-sm font-medium rounded-lg transition-all disabled:opacity-50"
            >
              {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
              {saving ? t("saving") : t("saveConfig")}
            </button>
          </div>
        </>
      )}
    </Section>
  );

  const renderActive = () => {
    switch (active) {
      case "general":
        return renderGeneral();
      case "models":
        return renderModels();
      case "prompts":
        return renderPrompts();
      case "apikeys":
        return renderApiKeys();
      default:
        return null;
    }
  };

  // 横向 Tab 短标签（取代竖向 SettingsSidebar；与全局品牌侧栏轴向正交，不再撞脸）。
  const allTabs: { id: SettingsCategory; label: string }[] = [
    { id: "general", label: t("tabGeneral") },
    { id: "models", label: t("tabModels") },
    { id: "prompts", label: t("tabPrompts") },
    { id: "apikeys", label: t("tabApikeys") },
  ];
  const TABS = allTabs.filter((tab) => {
    if (tab.id === "apikeys") return mayManageProviderConfig;
    if (!mayManageServer) return tab.id === "general" || tab.id === "prompts";
    return true;
  });

  return (
    <div className="relative h-full flex flex-col">
      {/* Atelier signature layers — inert on non-atelier themes. */}
      <div className="atelier-page-bloom" aria-hidden="true" />
      <div className="atelier-page-grain" aria-hidden="true" />

      {/* Head: 「设置」标题 + 横向 Tab —— 取代竖向子栏 */}
      <header className="flex-shrink-0 border-b border-glass-border px-4 md:px-7 pt-6 pb-4 relative z-10">
        <div className="w-full">
        <h1 className="font-display atelier-display text-[1.625rem] md:text-[2.125rem] font-semibold text-foreground tracking-tight">
          {t("title")}
        </h1>
        <nav className="flex flex-wrap gap-1 mt-5" role="tablist" aria-label={t("tabsAria")} onKeyDown={rovingKeyDown}>
          {TABS.map((tab) => {
            const isActive = active === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                tabIndex={isActive ? 0 : -1}
                onClick={() => setActive(tab.id)}
                className={`px-3.5 py-1.5 rounded-full text-[0.8125rem] transition-colors ${
                  isActive
                    ? "bg-primary/10 text-foreground font-semibold"
                    : "text-text-muted hover:text-foreground hover:bg-hover-bg font-medium"
                }`}
              >
                {tab.label}
              </button>
            );
          })}
        </nav>
        </div>
      </header>

      {/* Scroll area */}
      <div className="relative z-10 flex-1 overflow-y-auto px-4 py-6 sm:px-6 sm:py-8 lg:px-10">
        <div className="max-w-6xl mx-auto flex flex-col gap-6">
          {!online && (
            <div
              role="status"
              className="flex items-center gap-3 px-4 py-3 rounded-lg bg-status-processing-bg border border-status-processing-border"
            >
              <WifiOff size={18} className="text-status-processing-fg flex-shrink-0" />
              <div className="flex-1">
                <div className="text-[0.78125rem] font-semibold text-foreground">{t("offlineTitle")}</div>
                <div className="text-[0.6875rem] text-text-secondary mt-0.5">
                  {t("offlineBody")}
                </div>
              </div>
            </div>
          )}

          {renderActive()}
          <div className="pb-8" />
        </div>
      </div>
    </div>
  );
}
