"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import type { CharacterMetadataDraft, CharacterImageKind } from "@/components/modules/CharacterWorkbench";
import type { ScenePropMetadataDraft } from "@/components/modules/ScenePropWorkbench";
import {
  api,
  waitForDurableJob,
  type EditableAssetType,
} from "@/lib/api";
import { normalizeVisualWeight } from "@/lib/assetMetadata";
import {
  publishAssetMutation,
  subscribeToAssetLibraryChanges,
} from "@/lib/assetLibrarySync";
import { isHybridModeEnabled, isServerModeEnabled } from "@/lib/serverMode";
import { toast } from "@/store/toastStore";

import {
  assetRefKey,
  type AssetEditorContext,
  type AssetEditorResponse,
  type AssetGenerationOptions,
  type AssetRef,
  type EditableAsset,
} from "./assetEditorTypes";

export type AssetTaskMarker = {
  task_id?: string;
  _task_id?: string;
  asset?: EditableAsset;
  _editor_context?: AssetEditorContext;
};

export type AssetTaskPollingTarget =
  | { kind: "durable"; taskId: string }
  | { kind: "local"; taskId: string }
  | null;

export function resolveAssetTaskPollingTarget(
  marker: AssetTaskMarker,
): AssetTaskPollingTarget {
  const hybridMode = isHybridModeEnabled();
  if (marker.task_id && isServerModeEnabled() && !hybridMode) {
    return { kind: "durable", taskId: marker.task_id };
  }
  const localTaskId = marker._task_id ?? (hybridMode ? marker.task_id : undefined);
  return localTaskId ? { kind: "local", taskId: localTaskId } : null;
}

function responseAsset(value: unknown): EditableAsset | null {
  if (!value || typeof value !== "object") return null;
  const response = value as AssetTaskMarker & Partial<EditableAsset>;
  const candidate = response.asset ?? response;
  return typeof candidate.id === "string" ? (candidate as EditableAsset) : null;
}

function responseContext(value: unknown): AssetEditorContext | null {
  if (!value || typeof value !== "object") return null;
  return ((value as AssetEditorResponse)._editor_context ?? null);
}

function errorMessage(_error: unknown, fallback: string): string {
  // Provider and transport diagnostics stay out of the end-user interface.
  // They may contain English prose or internal implementation details; the
  // translated operation-specific fallback is both safe and actionable.
  return fallback;
}

function wait(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) {
    return Promise.reject(
      signal.reason ?? new DOMException("Operation was aborted", "AbortError"),
    );
  }
  return new Promise((resolve, reject) => {
    const handleAbort = () => {
      window.clearTimeout(timer);
      reject(signal.reason ?? new DOMException("Operation was aborted", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", handleAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}

export interface UseAssetEditorControllerOptions {
  open: boolean;
  assetRef: AssetRef;
  onMutated?: (asset: EditableAsset, ref: AssetRef) => void;
  onConverted?: (asset: EditableAsset, previousRef: AssetRef, nextRef: AssetRef) => void;
}

export function useAssetEditorController({
  open,
  assetRef,
  onMutated,
  onConverted,
}: UseAssetEditorControllerOptions) {
  const t = useTranslations("library");
  const [asset, setAsset] = useState<EditableAsset | null>(null);
  const [context, setContext] = useState<AssetEditorContext | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [generatingTypes, setGeneratingTypes] = useState<Array<{ type: string; batchSize: number }>>([]);
  const [generatingMotion, setGeneratingMotion] = useState(false);
  const requestRevision = useRef(0);
  const mutationLocks = useRef(new Set<string>());
  const generationControllers = useRef(new Map<string, AbortController>());
  const refKey = assetRefKey(assetRef);
  const loading = loadingKey !== refKey;

  const applyServerAsset = useCallback((
    value: unknown,
    effectiveType: EditableAssetType = assetRef.assetType,
    previousRef: AssetRef = assetRef,
  ): EditableAsset => {
    const nextAsset = responseAsset(value);
    if (!nextAsset) throw new Error(t("invalidAssetResponse"));
    const nextRef: AssetRef = { ...assetRef, assetType: effectiveType };
    setAsset(nextAsset);
    const nextContext = responseContext(value);
    if (nextContext) setContext(nextContext);
    publishAssetMutation({
      ref: nextRef,
      previousRef: effectiveType === previousRef.assetType ? undefined : previousRef,
      asset: nextAsset,
    });
    if (effectiveType !== previousRef.assetType) {
      onConverted?.(nextAsset, previousRef, nextRef);
    } else {
      onMutated?.(nextAsset, nextRef);
    }
    return nextAsset;
  }, [assetRef, onConverted, onMutated, t]);

  const load = useCallback(async () => {
    const revision = ++requestRevision.current;
    setLoadError(null);
    try {
      const response = await api.getOwnedAsset(
        assetRef.ownerKind,
        assetRef.ownerId,
        assetRef.assetType,
        assetRef.assetId,
      );
      if (revision !== requestRevision.current) return null;
      const next = responseAsset(response);
      if (!next) throw new Error(t("invalidAssetResponse"));
      setAsset(next);
      setContext(responseContext(response));
      return next;
    } catch (error) {
      if (revision !== requestRevision.current) return null;
      setAsset(null);
      setLoadError(errorMessage(error, t("loadEditorFailed")));
      return null;
    } finally {
      if (revision === requestRevision.current) setLoadingKey(refKey);
    }
  }, [assetRef.assetId, assetRef.assetType, assetRef.ownerId, assetRef.ownerKind, refKey, t]);

  useEffect(() => {
    if (!open) return;
    void load();
    return () => {
      requestRevision.current += 1;
    };
  }, [load, open]);

  useEffect(() => {
    const controllers = generationControllers.current;
    return () => {
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
    };
  }, [open, refKey]);

  useEffect(() => {
    if (!open) return;
    return subscribeToAssetLibraryChanges((detail) => {
      if (
        !detail.asset
        || detail.source !== assetRef.ownerKind
        || detail.assetType !== assetRef.assetType
        || detail.assetId !== assetRef.assetId
      ) {
        return;
      }
      const detailOwnerId =
        detail.source === "global"
          ? "global"
          : detail.source === "series"
            ? detail.seriesId
            : detail.projectId;
      if (detailOwnerId !== assetRef.ownerId) return;
      setAsset(detail.asset);
    });
  }, [
    assetRef.assetId,
    assetRef.assetType,
    assetRef.ownerId,
    assetRef.ownerKind,
    open,
  ]);

  const reload = useCallback(async () => {
    const response = await api.getOwnedAsset(
      assetRef.ownerKind,
      assetRef.ownerId,
      assetRef.assetType,
      assetRef.assetId,
    );
    const next = responseAsset(response);
    if (!next) throw new Error(t("invalidAssetResponse"));
    setAsset(next);
    setContext(responseContext(response));
    return { response, asset: next };
  }, [assetRef, t]);

  const waitForGeneration = useCallback(async (
    response: unknown,
    signal: AbortSignal,
  ) => {
    const target = resolveAssetTaskPollingTarget((response ?? {}) as AssetTaskMarker);
    if (!target) return;
    if (target.kind === "durable") {
      await waitForDurableJob(target.taskId, { signal });
      return;
    }
    for (let attempts = 0; attempts < 900; attempts += 1) {
      await wait(1000, signal);
      const status = await api.getTaskStatus(target.taskId, { signal });
      if (status?.status === "completed") return;
      if (status?.status === "failed" || status?.status === "canceled") {
        throw new Error(status.error || t("genFailed"));
      }
    }
    throw new Error(t("genTimeout"));
  }, [t]);

  const withMutationLock = useCallback(async <T,>(key: string, operation: () => Promise<T>): Promise<T | null> => {
    if (mutationLocks.current.has(key)) return null;
    mutationLocks.current.add(key);
    try {
      return await operation();
    } finally {
      mutationLocks.current.delete(key);
    }
  }, []);

  const mutateVariant = useCallback(async (
    action: "select" | "delete" | "favorite",
    generationType: string,
    variantId: string,
    isFavorited = false,
  ) => {
    const lockKey = `image:${action}:${generationType}:${variantId}`;
    await withMutationLock(lockKey, async () => {
      try {
        const response =
          action === "select"
            ? await api.selectOwnedAssetVariant(assetRef.ownerKind, assetRef.ownerId, assetRef.assetType, assetRef.assetId, variantId, generationType)
            : action === "delete"
              ? await api.deleteOwnedAssetVariant(assetRef.ownerKind, assetRef.ownerId, assetRef.assetType, assetRef.assetId, variantId, generationType)
              : await api.favoriteOwnedAssetVariant(
                  assetRef.ownerKind,
                  assetRef.ownerId,
                  assetRef.assetType,
                  assetRef.assetId,
                  variantId,
                  isFavorited,
                  generationType,
                );
        applyServerAsset(response);
      } catch (error) {
        toast.error(t("variantUpdateFailed"), {
          body: errorMessage(error, t("variantUpdateFailed")),
        });
      }
    });
  }, [applyServerAsset, assetRef, t, withMutationLock]);

  const mutateMotionVariant = useCallback(async (
    action: "select" | "delete" | "favorite",
    motionType: string,
    variantId: string,
    isFavorited = false,
  ) => {
    const lockKey = `motion:${action}:${motionType}:${variantId}`;
    await withMutationLock(lockKey, async () => {
      try {
        const response =
          action === "select"
            ? await api.selectOwnedAssetMotionVariant(assetRef.ownerKind, assetRef.ownerId, assetRef.assetType, assetRef.assetId, variantId, motionType)
            : action === "delete"
              ? await api.deleteOwnedAssetMotionVariant(assetRef.ownerKind, assetRef.ownerId, assetRef.assetType, assetRef.assetId, variantId, motionType)
              : await api.favoriteOwnedAssetMotionVariant(
                  assetRef.ownerKind,
                  assetRef.ownerId,
                  assetRef.assetType,
                  assetRef.assetId,
                  variantId,
                  isFavorited,
                  motionType,
                );
        applyServerAsset(response);
      } catch (error) {
        toast.error(t("motionVariantUpdateFailed"), {
          body: errorMessage(error, t("motionVariantUpdateFailed")),
        });
      }
    });
  }, [applyServerAsset, assetRef, t, withMutationLock]);

  const generate = useCallback(async (
    generationType: string,
    prompt: string,
    applyStyle: boolean,
    negativePrompt: string,
    batchSize: number,
    options: AssetGenerationOptions = {},
  ) => {
    if (generatingTypes.some((entry) => entry.type === generationType)) return;
    const controllerKey = `image:${generationType}`;
    const controller = new AbortController();
    generationControllers.current.get(controllerKey)?.abort();
    generationControllers.current.set(controllerKey, controller);
    setGeneratingTypes((entries) => [...entries, { type: generationType, batchSize }]);
    const toastId = toast.progress(t("generatingVariants"), {
      body: t("generatingVariantsBody", { name: asset?.name ?? "", count: batchSize }),
    });
    try {
      const draft = {
        generation_type: generationType,
        prompt,
        apply_style: applyStyle,
        negative_prompt: negativePrompt,
        batch_size: batchSize,
        model_name: options.modelName,
        aspect_ratio: options.aspectRatio,
        template_id: options.templateId,
      };
      const compiled = await api.previewOwnedAssetGeneration(
        assetRef.ownerKind,
        assetRef.ownerId,
        assetRef.assetType,
        assetRef.assetId,
        draft,
      );
      const response = await api.generateOwnedAsset(
        assetRef.ownerKind,
        assetRef.ownerId,
        assetRef.assetType,
        assetRef.assetId,
        { ...draft, compiled_request_checksum: compiled.checksum },
      );
      await waitForGeneration(response, controller.signal);
      if (controller.signal.aborted) return;
      const marker = (response ?? {}) as AssetTaskMarker;
      const finalResponse = marker.task_id || marker._task_id ? (await reload()).response : response;
      if (controller.signal.aborted) return;
      applyServerAsset(finalResponse);
      toast.update(toastId, {
        kind: "success",
        title: t("variantsGenerated"),
        body: t("variantsAddedBody", { count: batchSize }),
        autoCloseMs: 5000,
      });
    } catch (error) {
      if (controller.signal.aborted) {
        toast.dismiss(toastId);
        return;
      }
      toast.update(toastId, {
        kind: "error",
        title: t("variantsGenFailed"),
        body: errorMessage(error, t("variantsGenFailed")),
        autoCloseMs: 0,
      });
    } finally {
      if (generationControllers.current.get(controllerKey) === controller) {
        generationControllers.current.delete(controllerKey);
      }
      setGeneratingTypes((entries) => entries.filter((entry) => entry.type !== generationType));
    }
  }, [applyServerAsset, asset?.name, assetRef, generatingTypes, reload, t, waitForGeneration]);

  const previewGeneration = useCallback((
    generationType: string,
    prompt: string,
    applyStyle: boolean,
    negativePrompt: string,
    batchSize: number,
    options: AssetGenerationOptions = {},
  ) => api.previewOwnedAssetGeneration(
    assetRef.ownerKind,
    assetRef.ownerId,
    assetRef.assetType,
    assetRef.assetId,
    {
      generation_type: generationType,
      prompt,
      apply_style: applyStyle,
      negative_prompt: negativePrompt,
      batch_size: batchSize,
      model_name: options.modelName,
      aspect_ratio: options.aspectRatio,
      template_id: options.templateId,
    },
  ), [assetRef]);

  const generateMotion = useCallback(async (
    prompt: string,
    duration: number,
    motionType: string,
    options: {
      model?: string;
      batchSize?: number;
      audioUrl?: string;
    } = {},
  ) => {
    if (generatingMotion) return;
    const controllerKey = "motion";
    const controller = new AbortController();
    generationControllers.current.get(controllerKey)?.abort();
    generationControllers.current.set(controllerKey, controller);
    setGeneratingMotion(true);
    const toastId = toast.progress(t("generatingMotion"), { body: asset?.name });
    try {
      const draft = {
        motion_type: motionType,
        prompt,
        duration,
        batch_size: options.batchSize ?? 1,
        model: options.model,
        audio_url: options.audioUrl,
      };
      const compiled = await api.previewOwnedAssetMotion(
        assetRef.ownerKind,
        assetRef.ownerId,
        assetRef.assetType,
        assetRef.assetId,
        draft,
      );
      const response = await api.generateOwnedAssetMotion(
        assetRef.ownerKind,
        assetRef.ownerId,
        assetRef.assetType,
        assetRef.assetId,
        { ...draft, compiled_request_checksum: compiled.checksum },
      );
      await waitForGeneration(response, controller.signal);
      if (controller.signal.aborted) return;
      const marker = (response ?? {}) as AssetTaskMarker;
      const finalResponse = marker.task_id || marker._task_id ? (await reload()).response : response;
      if (controller.signal.aborted) return;
      applyServerAsset(finalResponse);
      toast.update(toastId, {
        kind: "success",
        title: t("motionGenerated"),
        autoCloseMs: 5000,
      });
    } catch (error) {
      if (controller.signal.aborted) {
        toast.dismiss(toastId);
        return;
      }
      toast.update(toastId, {
        kind: "error",
        title: t("motionGenerationFailed"),
        body: errorMessage(error, t("motionGenerationFailed")),
        autoCloseMs: 0,
      });
    } finally {
      if (generationControllers.current.get(controllerKey) === controller) {
        generationControllers.current.delete(controllerKey);
      }
      setGeneratingMotion(false);
    }
  }, [applyServerAsset, asset?.name, assetRef, generatingMotion, reload, t, waitForGeneration]);

  const previewMotion = useCallback((
    prompt: string,
    duration: number,
    motionType: string,
    options: {
      model?: string;
      batchSize?: number;
      audioUrl?: string;
    } = {},
  ) => api.previewOwnedAssetMotion(
    assetRef.ownerKind,
    assetRef.ownerId,
    assetRef.assetType,
    assetRef.assetId,
    {
      motion_type: motionType,
      prompt,
      duration,
      batch_size: options.batchSize ?? 1,
      model: options.model,
      audio_url: options.audioUrl,
    },
  ), [assetRef]);

  const saveCharacter = useCallback(async (draft: CharacterMetadataDraft): Promise<boolean> => {
    if (saving) return false;
    setSaving(true);
    const previousRef = assetRef;
    try {
      const nextType = draft.assetType;
      const attributes =
        nextType === "character"
          ? {
              name: draft.name,
              description: draft.description,
              persona: draft.persona,
              age: draft.age,
              gender: draft.gender,
              clothing: draft.clothing,
              visual_weight: normalizeVisualWeight(draft.visualWeight),
            }
          : nextType === "scene"
            ? { name: draft.name, description: draft.description, visual_weight: normalizeVisualWeight(draft.visualWeight) }
            : { name: draft.name, description: draft.description };
      const prompts =
        nextType === "character"
          ? draft.prompts
          : {
              image_prompt: draft.prompts.reference_sheet_prompt,
              video_prompt: draft.prompts.video_prompt,
            };
      const response = await api.updateOwnedAsset(
        assetRef.ownerKind,
        assetRef.ownerId,
        assetRef.assetType,
        assetRef.assetId,
        {
          attributes,
          prompts,
          target_asset_type: nextType === assetRef.assetType ? undefined : nextType,
        },
      );
      applyServerAsset(response, nextType, previousRef);
      toast.success(t("saveSuccess"));
      return true;
    } catch (error) {
      toast.error(t("saveFailed"), {
        body: errorMessage(error, t("saveFailed")),
      });
      return false;
    } finally {
      setSaving(false);
    }
  }, [applyServerAsset, assetRef, saving, t]);

  const saveSceneProp = useCallback(async (draft: ScenePropMetadataDraft): Promise<boolean> => {
    if (saving) return false;
    setSaving(true);
    const previousRef = assetRef;
    try {
      const response = await api.updateOwnedAsset(
        assetRef.ownerKind,
        assetRef.ownerId,
        assetRef.assetType,
        assetRef.assetId,
        {
          attributes: draft.attributes,
          prompts: { image_prompt: draft.prompt, video_prompt: draft.videoPrompt },
          target_asset_type: draft.assetType === assetRef.assetType ? undefined : draft.assetType,
        },
      );
      applyServerAsset(response, draft.assetType, previousRef);
      toast.success(t("saveSuccess"));
      return true;
    } catch (error) {
      toast.error(t("saveFailed"), {
        body: errorMessage(error, t("saveFailed")),
      });
      return false;
    } finally {
      setSaving(false);
    }
  }, [applyServerAsset, assetRef, saving, t]);

  return {
    asset,
    context,
    loading,
    loadError,
    saving,
    generatingTypes,
    generatingMotion,
    reload: load,
    generate,
    previewGeneration,
    generateMotion,
    previewMotion,
    saveCharacter,
    saveSceneProp,
    selectVariant: (type: CharacterImageKind | string, id: string) => mutateVariant("select", type, id),
    deleteVariant: (type: CharacterImageKind | string, id: string) => mutateVariant("delete", type, id),
    favoriteVariant: (type: CharacterImageKind | string, id: string, value: boolean) => mutateVariant("favorite", type, id, value),
    selectMotionVariant: (type: string, id: string) => mutateMotionVariant("select", type, id),
    deleteMotionVariant: (type: string, id: string) => mutateMotionVariant("delete", type, id),
    favoriteMotionVariant: (type: string, id: string, value: boolean) => mutateMotionVariant("favorite", type, id, value),
  };
}
