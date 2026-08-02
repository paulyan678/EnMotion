"use client";

import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

import CharacterWorkbench from "@/components/modules/CharacterWorkbench";
import ScenePropWorkbench from "@/components/modules/ScenePropWorkbench";
import type { EditableAssetType } from "@/lib/api";
import type { Prop, Scene } from "@/store/projectStore";

import {
  assetRefKey,
  type AssetRef,
  type EditableAsset,
} from "./assetEditorTypes";
import { useAssetEditorController } from "./useAssetEditorController";

export interface SharedAssetEditorProps {
  open: boolean;
  assetRef: AssetRef;
  onClose: () => void;
  onMutated?: (asset: EditableAsset, ref: AssetRef) => void;
  onConverted?: (
    asset: EditableAsset,
    previousRef: AssetRef,
    nextRef: AssetRef,
  ) => void;
}

/**
 * The sole public editor for canonical character, scene, and prop records.
 *
 * Feature surfaces provide only an exact composite AssetRef. Loading,
 * persistence, generation, polling, variant actions, and synchronization all
 * stay inside the shared owner-aware controller.
 */
export default function SharedAssetEditor({
  open,
  assetRef,
  onClose,
  onMutated,
  onConverted,
}: SharedAssetEditorProps) {
  const t = useTranslations("library");
  const controller = useAssetEditorController({
    open,
    assetRef,
    onMutated,
    onConverted,
  });

  if (!open) return null;

  if (controller.loading || (!controller.asset && !controller.loadError)) {
    return (
      <EditorStateDialog label={t("loadingEditor")}>
        <Loader2 className="animate-spin text-primary" size={30} aria-hidden="true" />
        <p className="text-sm text-text-secondary">{t("loadingEditor")}</p>
      </EditorStateDialog>
    );
  }

  if (!controller.asset || controller.loadError) {
    return (
      <EditorStateDialog label={t("loadEditorFailed")}>
        <AlertTriangle className="text-status-error-fg" size={30} aria-hidden="true" />
        <h2 className="text-lg font-bold text-foreground">{t("loadEditorFailed")}</h2>
        <p className="max-w-md text-center text-sm text-text-secondary">
          {controller.loadError || t("errorInvalidResponse")}
        </p>
        <div className="flex gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-glass-border px-4 py-2 text-sm text-text-secondary hover:bg-hover-bg hover:text-foreground"
          >
            {t("cancel")}
          </button>
          <button
            type="button"
            onClick={() => void controller.reload()}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-bold text-primary-foreground hover:bg-primary/90"
          >
            <RefreshCw size={15} aria-hidden="true" />
            {t("retry")}
          </button>
        </div>
      </EditorStateDialog>
    );
  }

  const context = controller.context;
  const renderedType = (
    (controller.asset as EditableAsset & { asset_type?: EditableAssetType })
      .asset_type || assetRef.assetType
  ) as EditableAssetType;
  const settings = context?.modelSettings;
  const styleConfig = context?.artDirection?.style_config;
  const defaultAspectRatio =
    renderedType === "character"
      ? settings?.character_aspect_ratio
      : renderedType === "scene"
        ? settings?.scene_aspect_ratio
        : settings?.prop_aspect_ratio;
  const motionDisabledReason = context?.capabilities.motionGeneration
    ? undefined
    : context?.capabilities.motionDisabledReason || t("motionRequiresImage");

  if (renderedType === "character") {
    return (
      <CharacterWorkbench
        key={assetRefKey(assetRef)}
        asset={controller.asset}
        onClose={onClose}
        onGenerate={(type, prompt, applyStyle, negativePrompt, batchSize, options) =>
          void controller.generate(
            type,
            prompt,
            applyStyle,
            negativePrompt,
            batchSize,
            options,
          )
        }
        generatingTypes={controller.generatingTypes}
        stylePrompt={styleConfig?.positive_prompt || ""}
        styleNegativePrompt={styleConfig?.negative_prompt || ""}
        onGenerateVideo={(prompt, duration, motionType, options) =>
          void controller.generateMotion(
            prompt,
            duration,
            motionType || "full_body",
            options,
          )
        }
        onPreviewGeneration={(type, prompt, applyStyle, negativePrompt, batchSize, options) =>
          controller.previewGeneration(
            type,
            prompt,
            applyStyle,
            negativePrompt,
            batchSize,
            options,
          )
        }
        onPreviewVideo={(prompt, duration, motionType, options) =>
          controller.previewMotion(
            prompt,
            duration,
            motionType || "full_body",
            options,
          )
        }
        isGeneratingVideo={controller.generatingMotion}
        onSelectVariant={(type, id) => controller.selectVariant(type, id)}
        onDeleteVariant={(type, id) => controller.deleteVariant(type, id)}
        onFavoriteVariant={(type, id, value) =>
          controller.favoriteVariant(type, id, value)
        }
        onSelectVideoVariant={(type, id) =>
          controller.selectMotionVariant(type, id)
        }
        onDeleteVideo={(type, id) =>
          controller.deleteMotionVariant(type, id)
        }
        onFavoriteVideoVariant={(type, id, value) =>
          controller.favoriteMotionVariant(type, id, value)
        }
        onSaveMetadata={(draft) => controller.saveCharacter(draft)}
        canChangeAssetType
        isSavingMetadata={controller.saving}
        supportsMotion={Boolean(context?.capabilities.motionGeneration)}
        defaultModelName={settings?.image_model}
        defaultVideoModelName={settings?.video_model}
        defaultAspectRatio={defaultAspectRatio}
        motionDisabledReason={motionDisabledReason}
      />
    );
  }

  return (
    <ScenePropWorkbench
      key={assetRefKey(assetRef)}
      asset={controller.asset as Scene | Prop}
      assetType={renderedType}
      onClose={onClose}
      onGenerate={(prompt, applyStyle, negativePrompt, batchSize, options) =>
        void controller.generate(
          "all",
          prompt,
          applyStyle,
          negativePrompt,
          batchSize,
          options,
        )
      }
      isGenerating={controller.generatingTypes.length > 0}
      generatingBatchSize={controller.generatingTypes[0]?.batchSize}
      stylePrompt={styleConfig?.positive_prompt || ""}
      styleNegativePrompt={styleConfig?.negative_prompt || ""}
      onGenerateVideo={(prompt, duration, options) =>
        void controller.generateMotion(prompt, duration, renderedType, options)
      }
      onPreviewGeneration={(prompt, applyStyle, negativePrompt, batchSize, options) =>
        controller.previewGeneration(
          "all",
          prompt,
          applyStyle,
          negativePrompt,
          batchSize,
          options,
        )
      }
      onPreviewVideo={(prompt, duration, options) =>
        controller.previewMotion(
          prompt,
          duration,
          renderedType,
          options,
        )
      }
      onDeleteVideo={(id) =>
        controller.deleteMotionVariant(renderedType, id)
      }
      onSelectVideo={(id) =>
        controller.selectMotionVariant(renderedType, id)
      }
      onFavoriteVideo={(id, value) =>
        controller.favoriteMotionVariant(renderedType, id, value)
      }
      isGeneratingVideo={controller.generatingMotion}
      onSelectVariant={(id) => controller.selectVariant("image", id)}
      onDeleteVariant={(id) => controller.deleteVariant("image", id)}
      onFavoriteVariant={(id, value) =>
        controller.favoriteVariant("image", id, value)
      }
      onSaveMetadata={(draft) => controller.saveSceneProp(draft)}
      canChangeAssetType
      isSavingMetadata={controller.saving}
      supportsMotion={Boolean(context?.capabilities.motionGeneration)}
      defaultModelName={settings?.image_model}
      defaultVideoModelName={settings?.video_model}
      defaultAspectRatio={defaultAspectRatio}
      motionDisabledReason={motionDisabledReason}
    />
  );
}

function EditorStateDialog({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  if (typeof document === "undefined") return null;
  return createPortal(
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-overlay p-6 backdrop-blur-md"
      role="dialog"
      aria-modal="true"
      aria-label={label}
    >
      <div className="flex min-h-48 w-full max-w-lg flex-col items-center justify-center gap-4 rounded-2xl border border-glass-border bg-surface p-8 shadow-lg">
        {children}
      </div>
    </div>,
    document.body,
  );
}
