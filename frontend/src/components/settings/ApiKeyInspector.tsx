"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Eye, EyeOff, Loader2, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { useAuth } from "@/components/auth/AuthProvider";
import { api, type ApiKeyInspectionPayload } from "@/lib/api";
import { getApprovedModelBySecretField } from "@/lib/newApiModels";
import { useModelDisplayName } from "@/lib/useModelDisplayName";

export default function ApiKeyInspector() {
  const t = useTranslations("settings");
  const modelDisplayName = useModelDisplayName();
  const { serverMode, user } = useAuth();
  const [inspection, setInspection] = useState<ApiKeyInspectionPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestSequence = useRef(0);

  const mayInspect = serverMode && user?.role === "admin";

  const load = useCallback(async (reveal: boolean) => {
    const requestId = ++requestSequence.current;
    setLoading(true);
    setFailed(false);

    // Remove full values from rendered state immediately when an Admin hides
    // them or refreshes the default masked view.
    if (!reveal) setInspection(null);

    try {
      const next = await api.inspectApiKeys(reveal);
      if (requestSequence.current === requestId) setInspection(next);
    } catch {
      if (requestSequence.current === requestId) setFailed(true);
    } finally {
      if (requestSequence.current === requestId) setLoading(false);
    }
  }, []);

  const close = useCallback(() => {
    requestSequence.current += 1;
    setInspection(null);
    setLoading(false);
    setFailed(false);
  }, []);

  useEffect(() => () => {
    requestSequence.current += 1;
  }, []);

  // Frontend defense in depth: ordinary users never receive or render the
  // control. The backend independently enforces the same Admin-only rule.
  if (!mayInspect) return null;

  return (
    <section
      aria-labelledby="api-key-inspector-title"
      className="mt-6 overflow-hidden rounded-xl border border-primary/25 bg-primary/[0.035]"
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-glass-border px-4 py-4">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 rounded-lg bg-primary/10 p-2 text-primary" aria-hidden="true">
            <ShieldCheck size={18} />
          </span>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 id="api-key-inspector-title" className="text-sm font-semibold text-foreground">
                {t("inspectKeysTitle")}
              </h3>
              <span className="rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[0.625rem] font-semibold text-primary">
                {t("adminOnly")}
              </span>
            </div>
          </div>
        </div>

        {inspection ? (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void load(inspection.revealed)}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg border border-glass-border bg-surface px-3 py-2 text-xs font-medium text-text-secondary transition-colors hover:text-foreground disabled:opacity-50"
            >
              {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              {t("refreshKeys")}
            </button>
            <button
              type="button"
              onClick={() => void load(!inspection.revealed)}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-on-accent transition-colors hover:bg-primary-hover disabled:opacity-50"
            >
              {inspection.revealed ? <EyeOff size={14} /> : <Eye size={14} />}
              {inspection.revealed ? t("hideFullKeys") : t("revealFullKeys")}
            </button>
            <button
              type="button"
              onClick={close}
              aria-label={t("closeKeyInspector")}
              className="rounded-lg border border-glass-border bg-surface p-2 text-text-muted transition-colors hover:text-foreground"
            >
              <X size={15} />
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => void load(false)}
            disabled={loading}
            className="flex items-center gap-2 rounded-lg bg-primary px-3.5 py-2 text-xs font-semibold text-on-accent transition-colors hover:bg-primary-hover disabled:opacity-50"
          >
            {loading ? <Loader2 size={15} className="animate-spin" /> : <Eye size={15} />}
            {loading ? t("loadingSavedKeys") : t("viewSavedKeys")}
          </button>
        )}
      </div>

      {failed && (
        <div role="alert" className="mx-4 mt-4 rounded-lg border border-status-failed-border bg-status-failed-bg px-3 py-2 text-xs text-status-failed-fg">
          {t("keyInspectionError")}
        </div>
      )}

      {inspection && (
        <div className="p-4">
          <div
            className={`mb-3 rounded-lg border px-3 py-2 text-[0.6875rem] leading-relaxed ${
              inspection.revealed
                ? "border-amber-400/30 bg-amber-400/10 text-amber-300"
                : "border-glass-border bg-surface text-text-muted"
            }`}
          >
            {inspection.revealed ? t("keyInspectionRevealedWarning") : t("keyInspectionMaskedNotice")}
          </div>

          <div className="overflow-hidden rounded-lg border border-glass-border">
            {inspection.items.map((item) => {
              const model = getApprovedModelBySecretField(item.api_key_field);
              const modelName = modelDisplayName(model?.id, item.display_name);
              const capability = t(
                `capability${item.capability[0].toUpperCase()}${item.capability.slice(1)}`,
              );
              const status = item.in_use
                ? t("activeInUse")
                : item.active
                  ? t("activeMissingKey")
                  : item.configured
                    ? t("savedNotActive")
                    : t("notConfigured");

              return (
                <div
                  key={item.api_key_field}
                  className={`grid gap-3 border-b border-glass-border px-4 py-3 last:border-b-0 md:grid-cols-[1fr_.55fr_1.5fr] md:items-center ${
                    item.in_use ? "bg-primary/5" : "bg-surface"
                  }`}
                >
                  <div className="min-w-0 text-xs font-semibold text-foreground">{modelName}</div>
                  <div className="flex flex-wrap gap-1.5 md:block">
                    <span className="inline-flex rounded-full bg-hover-bg px-2 py-1 text-[0.625rem] font-semibold text-text-secondary">
                      {capability}
                    </span>
                    <span
                      className={`ml-0 inline-flex rounded-full px-2 py-1 text-[0.625rem] font-semibold md:ml-1.5 ${
                        item.in_use
                          ? "bg-emerald-400/10 text-emerald-400"
                          : item.active
                            ? "bg-amber-400/10 text-amber-300"
                            : "bg-hover-bg text-text-muted"
                      }`}
                    >
                      {status}
                    </span>
                  </div>
                  <code className="min-h-9 break-all rounded-md border border-glass-border bg-input-bg px-3 py-2 text-xs text-foreground">
                    {item.configured ? item.value : t("notConfigured")}
                  </code>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
