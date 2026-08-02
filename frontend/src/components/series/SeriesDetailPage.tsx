"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import dynamic from "next/dynamic";
import { motion, AnimatePresence } from "framer-motion";
import { Image as ImageIcon, Play, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import {
  useProjectStore,
  type Series,
  type Character,
  type Scene,
  type Prop,
  type Project,
} from "@/store/projectStore";
import { toast } from "@/store/toastStore";
import AssetCard from "@/components/common/AssetCard";
import { useTranslations } from "next-intl";
import SeriesSidebar, { type SidebarItem } from "./SeriesSidebar";
import { subscribeToAssetLibraryChanges } from "@/lib/assetLibrarySync";
import { subscribeToStoryboardFrameChanges } from "@/lib/storyboardFrameSync";
import { selectedStoryboardImage } from "@/lib/clipStartFrame";
import { useAuth } from "@/components/auth/AuthProvider";
import BreadcrumbBar from "@/components/layout/BreadcrumbBar";
import { useTopBarNavigation } from "@/components/layout/TopBarNavigationContext";
import PreviewImage from "@/components/shared/preview/PreviewImage";
import SharedAssetEditor from "@/components/assets/SharedAssetEditor";
import type {
  AssetRef,
  EditableAsset,
} from "@/components/assets/assetEditorTypes";

const ImportAssetsDialog = dynamic(() => import("./ImportAssetsDialog"), { ssr: false });
const SeriesArtDirectionPanel = dynamic(() => import("./SeriesArtDirectionPanel"), { ssr: false });

interface SeriesDetailPageProps {
  seriesId: string;
}

type AssetTab = "characters" | "scenes" | "props";

interface SeriesTopBarTitleProps {
  seriesId: string;
  title: string;
  onSaved: (title: string) => void;
}

function SeriesTopBarTitle({ seriesId, title, onSaved }: SeriesTopBarTitleProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const t = useTranslations("series");

  const saveTitle = async () => {
    const nextTitle = draftTitle.trim();
    setIsEditing(false);

    if (!nextTitle || nextTitle === title) {
      setDraftTitle(title);
      return;
    }

    try {
      await api.updateSeries(seriesId, { title: nextTitle });
      onSaved(nextTitle);
    } catch (error) {
      console.error("Failed to update series title:", error);
      setDraftTitle(title);
    }
  };

  if (isEditing) {
    return (
      <input
        type="text"
        value={draftTitle}
        onChange={(event) => setDraftTitle(event.target.value)}
        onBlur={() => void saveTitle()}
        onKeyDown={(event) => {
          if (event.key === "Enter") void saveTitle();
          if (event.key === "Escape") {
            setDraftTitle(title);
            setIsEditing(false);
          }
        }}
        aria-label={t("editTitleHint")}
        className="min-w-24 max-w-full border-b border-primary bg-transparent font-medium text-foreground outline-none"
        autoFocus
      />
    );
  }

  return (
    <span
      data-testid="series-top-bar-title"
      className="block max-w-full cursor-pointer truncate transition-colors hover:text-primary"
      onDoubleClick={() => setIsEditing(true)}
      title={`${title} · ${t("editTitleHint")}`}
    >
      {title}
    </span>
  );
}

export default function SeriesDetailPage({ seriesId }: SeriesDetailPageProps) {
  const [series, setSeries] = useState<Series | null>(null);
  const [episodes, setEpisodes] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeItem, setActiveItem] = useState<SidebarItem>({ kind: "asset", tab: "characters" });
  const [showAddEpisode, setShowAddEpisode] = useState(false);
  const [newEpisodeTitle, setNewEpisodeTitle] = useState("");
  const [isCreatingEpisode, setIsCreatingEpisode] = useState(false);
  const [showImportAssets, setShowImportAssets] = useState(false);
  const [editorTarget, setEditorTarget] = useState<AssetRef | null>(null);
  const [isDeletingSeries, setIsDeletingSeries] = useState(false);
  const deleteSeries = useProjectStore((state) => state.deleteSeries);
  const { serverMode } = useAuth();
  const { registerNavigation } = useTopBarNavigation();

  const t = useTranslations("series");
  const tc = useTranslations("common");

  const ASSET_LABELS: Record<AssetTab, string> = {
    characters: t("characterLabel"),
    scenes: t("sceneLabel"),
    props: t("propLabel"),
  };

  const fetchData = useCallback(async () => {
    try {
      const [seriesData, episodesData] = await Promise.all([
        api.getSeries(seriesId),
        api.getSeriesEpisodes(seriesId),
      ]);
      setSeries(seriesData);
      setEpisodes(episodesData);
    } catch (error) {
      console.error("Failed to fetch series data:", error);
    } finally {
      setLoading(false);
    }
  }, [seriesId]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void fetchData());
    return () => window.cancelAnimationFrame(frame);
  }, [fetchData]);

  const applyAssetMutation = useCallback((
    updated: EditableAsset,
    ref: AssetRef,
    previousRef?: AssetRef,
  ) => {
    const collection = (type: AssetRef["assetType"]) =>
      type === "character" ? "characters" : type === "scene" ? "scenes" : "props";
    setSeries((current) => {
      if (!current) return current;
      const next = { ...current };
      if (previousRef) {
        const previousCollection = collection(previousRef.assetType);
        next[previousCollection] = (next[previousCollection] || []).filter(
          (candidate) => candidate.id !== previousRef.assetId,
        ) as never;
      }
      const nextCollection = collection(ref.assetType);
      const items = [...(next[nextCollection] || [])] as EditableAsset[];
      const index = items.findIndex((candidate) => candidate.id === ref.assetId);
      if (index >= 0) items[index] = updated;
      else items.push(updated);
      next[nextCollection] = items as never;
      return next;
    });
  }, []);

  useEffect(
    () => subscribeToAssetLibraryChanges((detail) => {
      const isUnscopedUsageChange =
        detail.usageChanged
        && !detail.source
        && !detail.projectId
        && !detail.seriesId;
      const belongsHere =
        detail.source === "global" ||
        detail.seriesId === seriesId ||
        (detail.source === "series" && !detail.seriesId) ||
        isUnscopedUsageChange;
      if (!belongsHere) return;
      if (detail.asset && detail.assetType && detail.assetId && detail.source) {
        applyAssetMutation(
          detail.asset,
          {
            ownerKind: detail.source,
            ownerId:
              detail.source === "global"
                ? "global"
                : detail.seriesId || seriesId,
            assetType: detail.assetType,
            assetId: detail.assetId,
            seriesId,
          },
          detail.previousRef,
        );
        return;
      }
      void fetchData();
    }),
    [applyAssetMutation, fetchData, seriesId],
  );

  useEffect(
    () => subscribeToStoryboardFrameChanges((detail) => {
      if (detail.seriesId === seriesId || episodes.some((episode) => episode.id === detail.projectId)) {
        void fetchData();
      }
    }),
    [episodes, fetchData, seriesId],
  );

  const handleTitleSaved = useCallback((title: string) => {
    setSeries((currentSeries) => currentSeries
      ? { ...currentSeries, title }
      : currentSeries);
  }, []);

  const seriesTitle = series?.title || "";
  const seriesDescription = series?.description || undefined;
  const topBarTitle = useMemo(() => (
    series ? (
      <SeriesTopBarTitle
        key={`${seriesId}:${seriesTitle}`}
        seriesId={seriesId}
        title={seriesTitle}
        onSaved={handleTitleSaved}
      />
    ) : undefined
  ), [handleTitleSaved, series, seriesId, seriesTitle]);
  const seriesNavigation = useMemo(() => (
    series ? {
      segments: [
        { label: "EnMotion", hash: "#/" },
        { label: seriesTitle },
      ],
      currentContent: topBarTitle,
      description: seriesDescription,
    } : null
  ), [series, seriesDescription, seriesTitle, topBarTitle]);

  useEffect(() => {
    if (!serverMode || !seriesNavigation) return;
    return registerNavigation(seriesNavigation);
  }, [registerNavigation, seriesNavigation, serverMode]);

  const handleAddEpisode = async () => {
    if (!newEpisodeTitle.trim()) return;
    setIsCreatingEpisode(true);
    try {
      const nextEpNum = episodes.length + 1;
      const workflowMode = series?.workflow_mode || "i2v_legacy";
      await api.createEpisodeForSeries(seriesId, newEpisodeTitle.trim(), nextEpNum, workflowMode);
      const updatedEpisodes = await api.getSeriesEpisodes(seriesId);
      setEpisodes(updatedEpisodes);
      setNewEpisodeTitle("");
      setShowAddEpisode(false);
    } catch (error) {
      console.error("Failed to add episode:", error);
    } finally {
      setIsCreatingEpisode(false);
    }
  };

  const handleAddEpisodeKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleAddEpisode();
    if (e.key === "Escape") setShowAddEpisode(false);
  };

  const handleOpenEpisode = (episodeId: string) => {
    window.location.hash = `#/series/${seriesId}/episode/${episodeId}`;
  };

  const handleDeleteSeries = async () => {
    if (!series || isDeletingSeries) return;
    const confirmed = window.confirm(
      t("confirmDeleteSeries", {
        title: series.title,
        count: episodes.length,
      }),
    );
    if (!confirmed) return;

    setIsDeletingSeries(true);
    try {
      await deleteSeries(seriesId);
      toast.success(t("deleteSeriesSuccess"));
      window.location.hash = "#/";
    } catch (error) {
      console.error("Failed to delete entire series:", error);
      toast.error(t("deleteSeriesFailed"));
    } finally {
      setIsDeletingSeries(false);
    }
  };

  const refreshSeriesData = async () => {
    await fetchData();
  };

  // ── Loading ──
  if (loading) {
    return (
      <div className="flex h-[100dvh] items-center justify-center bg-background">
        <div className="text-text-secondary">{tc("loading")}</div>
      </div>
    );
  }

  // ── Error ──
  if (!series) {
    return (
      <div className="flex h-[100dvh] items-center justify-center bg-background">
        <div className="text-center">
          <p className="text-text-secondary mb-4">{t("notFound")}</p>
          <a href="#/" className="text-primary hover:underline">{t("backToHome")}</a>
        </div>
      </div>
    );
  }

  // ── Derive content ──
  const getAssets = (tab: AssetTab): (Character | Scene | Prop)[] => {
    if (tab === "characters") return series.characters || [];
    if (tab === "scenes") return series.scenes || [];
    return series.props || [];
  };

  const selectedEpisode =
    activeItem.kind === "episode"
      ? episodes.find((ep) => ep.id === activeItem.episodeId)
      : null;

  return (
    <main className="flex h-[100dvh] w-full flex-col overflow-hidden bg-background">
      {!serverMode && seriesNavigation && (
        <BreadcrumbBar
          segments={seriesNavigation.segments}
          currentContent={seriesNavigation.currentContent}
          description={seriesNavigation.description}
        />
      )}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
        {/* ── Sidebar ── */}
        <SeriesSidebar
          series={series}
          episodes={episodes}
          activeItem={activeItem}
          onItemChange={setActiveItem}
          showAddEpisode={showAddEpisode}
          newEpisodeTitle={newEpisodeTitle}
          isCreatingEpisode={isCreatingEpisode}
          onShowAddEpisode={setShowAddEpisode}
          onNewEpisodeTitleChange={setNewEpisodeTitle}
          onAddEpisode={handleAddEpisode}
          onAddEpisodeKeyDown={handleAddEpisodeKeyDown}
          onOpenImportAssets={() => setShowImportAssets(true)}
          onDeleteSeries={handleDeleteSeries}
          isDeletingSeries={isDeletingSeries}
        />

        {/* ── Content Area ── */}
        <div className="flex flex-1 flex-col overflow-hidden">
          <AnimatePresence mode="wait">
            {activeItem.kind === "art_direction" ? (
              <SeriesArtDirectionPanel
                key="art-direction"
                seriesId={seriesId}
                onSaved={refreshSeriesData}
              />
            ) : activeItem.kind === "asset" ? (
              <AssetContentPanel
                key={`asset-${activeItem.tab}`}
                tab={activeItem.tab}
                assets={getAssets(activeItem.tab)}
                label={ASSET_LABELS[activeItem.tab]}
                onEdit={(asset) => {
                  const source = asset.source === "global" ? "global" : "series";
                  setEditorTarget({
                    ownerKind: source,
                    ownerId:
                      source === "global"
                        ? asset.source_id || "global"
                        : asset.source_id || asset.series_id || seriesId,
                    assetType:
                      activeItem.tab === "characters"
                        ? "character"
                        : activeItem.tab === "scenes"
                          ? "scene"
                          : "prop",
                    assetId: asset.id,
                    seriesId,
                  });
                }}
              />
            ) : selectedEpisode ? (
              <EpisodeContentPanel
                key={`episode-${selectedEpisode.id}`}
                episode={selectedEpisode}
                seriesId={seriesId}
                onOpenEditor={() => handleOpenEpisode(selectedEpisode.id)}
              />
            ) : null}
          </AnimatePresence>
        </div>
      </div>

      {/* ── Modals ── */}
      <ImportAssetsDialog
        isOpen={showImportAssets}
        onClose={() => setShowImportAssets(false)}
        seriesId={seriesId}
        onImported={refreshSeriesData}
      />
      {editorTarget ? (
        <SharedAssetEditor
          open
          assetRef={editorTarget}
          onClose={() => setEditorTarget(null)}
          onMutated={(asset, ref) => applyAssetMutation(asset, ref)}
          onConverted={(asset, previousRef, nextRef) => {
            applyAssetMutation(asset, nextRef, previousRef);
            setEditorTarget(nextRef);
          }}
        />
      ) : null}
    </main>
  );
}

// ── Shared animation config ──

const contentTransition = {
  duration: 0.25,
  ease: [0.25, 1, 0.5, 1] as const, // ease-out-quart
};

// ── Asset Content Panel ──

function AssetContentPanel({
  tab,
  assets,
  label,
  onEdit,
}: {
  tab: AssetTab;
  assets: (Character | Scene | Prop)[];
  label: string;
  onEdit: (asset: Character | Scene | Prop) => void;
}) {
  const t = useTranslations("series");

  return (
    <motion.div
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -16 }}
      transition={contentTransition}
      className="flex-1 flex flex-col overflow-hidden"
    >
      {/* Header */}
      <div className="px-8 pt-6 pb-4">
        <h2 className="text-xl font-display font-bold text-foreground">
          {label}
          <span className="text-sm font-normal text-text-secondary ml-2">
            {t("itemCount", { count: assets.length })}
          </span>
        </h2>
        <p className="text-xs text-text-muted mt-1">
          {t("sharedAssetsEditHint")}
        </p>
      </div>

      {/* Grid */}
      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {assets.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-text-secondary">
            <motion.div
              animate={{ y: [0, -6, 0] }}
              transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
              className="w-16 h-16 rounded-2xl bg-glass border border-glass-border flex items-center justify-center mb-4"
            >
              <ImageIcon size={28} className="text-text-muted" />
            </motion.div>
            <p className="text-sm font-medium">{t("noAssets", { label })}</p>
            <p className="text-xs text-text-muted mt-1">{t("assetsSharedHint")}</p>
          </div>
        ) : (
          <motion.div
            className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4"
            initial="hidden"
            animate="visible"
            variants={{
              visible: { transition: { staggerChildren: 0.04 } },
            }}
          >
            {assets.map((asset) => (
              <motion.div
                key={asset.id}
                variants={{
                  hidden: { opacity: 0, y: 16, scale: 0.97 },
                  visible: {
                    opacity: 1,
                    y: 0,
                    scale: 1,
                    transition: { duration: 0.3, ease: [0.25, 1, 0.5, 1] },
                  },
                }}
              >
                <AssetCard
                  asset={asset}
                  type={tab}
                  onEdit={() => onEdit(asset)}
                />
              </motion.div>
            ))}
          </motion.div>
        )}
      </div>
    </motion.div>
  );
}

// ── Episode Content Panel ──

function EpisodeContentPanel({
  episode,
  seriesId,
  onOpenEditor,
}: {
  episode: Project;
  seriesId: string;
  onOpenEditor: () => void;
}) {
  const t = useTranslations("series");

  const frames = episode.frames || [];
  const characters = episode.characters || [];
  const scenes = episode.scenes || [];
  const originalText = episode.originalText || "";

  return (
    <motion.div
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -16 }}
      transition={contentTransition}
      className="flex-1 flex flex-col overflow-hidden"
    >
      {/* Header */}
      <div className="px-8 pt-6 pb-4 flex items-start justify-between border-b border-glass-border">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <span className="text-xs bg-primary/20 text-primary px-2.5 py-1 rounded-lg font-mono font-bold">
              {t("episodeNumber", { number: episode.episode_number || "?" })}
            </span>
            <h2 className="text-xl font-display font-bold text-foreground">
              {episode.title}
            </h2>
          </div>
          <p className="text-xs text-text-secondary">
            {t("videoPipelineLabel")} · {t("frameCount", { count: frames.length })}
          </p>
        </div>
        <motion.button
          whileHover={{ scale: 1.03 }}
          whileTap={{ scale: 0.97 }}
          onClick={onOpenEditor}
          className="flex items-center gap-2 bg-primary hover:bg-primary/90 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors shadow-lg shadow-primary/20 hover:shadow-primary/30"
        >
          <Play size={14} />
          {t("enterEditor")}
          <ChevronRight size={14} />
        </motion.button>
      </div>

      {/* Episode Overview */}
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
        {/* Script Summary */}
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-foreground">{t("scriptSummary")}</h3>
          {originalText ? (
            <p className="text-xs text-text-secondary leading-relaxed line-clamp-4 bg-surface rounded-lg p-3 border border-glass-border">
              {originalText.slice(0, 300)}{originalText.length > 300 ? "..." : ""}
            </p>
          ) : (
            <p className="text-xs text-text-muted italic">{t("noScript")}</p>
          )}
        </div>

        {/* Storyboard Overview */}
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-foreground">{t("storyboardOverview")}</h3>
          {frames.length === 0 ? (
            <div className="flex items-center gap-3 bg-surface rounded-lg p-4 border border-glass-border">
              <div className="w-10 h-10 rounded-lg bg-glass border border-glass-border flex items-center justify-center">
                <Play size={16} className="text-text-muted" />
              </div>
              <div>
                <p className="text-xs font-medium text-text-secondary">{t("noFrames")}</p>
                <p className="text-[0.6875rem] text-text-muted">{t("startCreating")}</p>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-4 lg:grid-cols-6 gap-2">
              {frames.slice(0, 12).map((frame, i) => {
                const selectedImage = selectedStoryboardImage(frame);
                const emptyImagePlaceholder = (
                  <div className="flex h-full w-full flex-col items-center justify-center gap-1 text-[0.625rem] text-text-muted font-mono">
                    <ImageIcon size={14} aria-hidden="true" />
                    <span>{t("noImage")}</span>
                  </div>
                );
                return (
                  <div
                    key={frame.id}
                    className="aspect-video bg-surface rounded-lg border border-glass-border overflow-hidden cursor-pointer hover:border-primary/50 transition-colors"
                    onClick={onOpenEditor}
                  >
                    <PreviewImage
                      src={selectedImage?.url}
                      alt={t("frameNum", { number: i + 1 })}
                      className="h-full w-full"
                      imgClassName="object-cover"
                      noLightbox
                      diagnosticContext="series-storyboard-overview"
                      placeholder={emptyImagePlaceholder}
                    />
                  </div>
                );
              })}
              {frames.length > 12 && (
                <div className="aspect-video bg-surface rounded-lg border border-glass-border flex items-center justify-center text-xs text-text-muted">
                  +{frames.length - 12}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Characters & Scenes count */}
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-surface rounded-lg p-3 border border-glass-border text-center">
            <p className="text-lg font-bold text-foreground">{characters.length}</p>
            <p className="text-[0.6875rem] text-text-muted">{t("characters")}</p>
          </div>
          <div className="bg-surface rounded-lg p-3 border border-glass-border text-center">
            <p className="text-lg font-bold text-foreground">{scenes.length}</p>
            <p className="text-[0.6875rem] text-text-muted">{t("scenes")}</p>
          </div>
          <div className="bg-surface rounded-lg p-3 border border-glass-border text-center">
            <p className="text-lg font-bold text-foreground">{frames.length}</p>
            <p className="text-[0.6875rem] text-text-muted">{t("storyboardFrames")}</p>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
