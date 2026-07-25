"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useTranslations } from "next-intl";
import {
  AlertCircle,
  ArrowDownUp,
  Check,
  ChevronDown,
  Copy,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Star,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  Character,
  Scene,
  Prop,
  ImageVariant,
} from "@/store/projectStore";
import { toast } from "@/store/toastStore";
import { primaryAssetDisplayUrl, primaryAssetImage, type AssetImageKind } from "@/lib/assetImage";
import { coverGradient, GRAIN_URL } from "@/lib/atelierCover";
import { rovingKeyDown } from "@/lib/a11y";
import AssetInspector from "./AssetInspector";
import NewLibraryAssetDialog from "./NewLibraryAssetDialog";
import {
  notifyAssetLibraryChanged,
  publishAssetMutation,
  subscribeToAssetLibraryChanges,
} from "@/lib/assetLibrarySync";
import PreviewImage from "@/components/shared/preview/PreviewImage";
import { useAuth } from "@/components/auth/AuthProvider";
import { useAssetLibraryQuery } from "@/lib/assetLibraryQuery";
import FavoriteButton from "@/components/assets/FavoriteButton";
import SharedAssetEditor from "@/components/assets/SharedAssetEditor";
import type { AssetRef, EditableAsset } from "@/components/assets/assetEditorTypes";
import {
  type AssetLibraryFeedItem,
  type AssetLibraryThumbnail,
  type AssetLibraryRequestError,
} from "@/lib/assetLibraryFeed";

type AssetTab = "characters" | "scenes" | "props";
type TypeFilter = AssetTab | "all";
type SortMode = "default" | "name" | "recent" | "usage";
type SortOrder = "asc" | "desc";
type ViewAxis = "type" | "source";

const SINGULAR: Record<AssetTab, AssetImageKind> = { characters: "character", scenes: "scene", props: "prop" };

interface AssetSource {
  id: string; // `series-X` / `project-X`（列表 key）
  rawId: string; // 裸 series/project id（调 API 用）
  name: string;
  kind: "series" | "project" | "global";
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
}

/** 渲染条目：携带所属 source，使「按类型」视图也能按源显示/操作。 */
interface RenderItem {
  asset: Character | Scene | Prop;
  type: AssetTab;
  src: AssetSource;
}

/** 渲染分组：「按类型」按资产类型、「按项目」按源，统一结构（title + meta + items）。 */
interface RenderGroup {
  key: string;
  title: string;
  meta: string;
  items: RenderItem[];
}

interface SelectedAssetDetail {
  key: string;
  asset: Character | Scene | Prop;
}

interface SelectedAssetContext {
  source: AssetSource;
  asset: Character | Scene | Prop;
  type: AssetTab;
}

interface LocalAssetOverride {
  deleted?: boolean;
  starred?: boolean;
  asset?: Character | Scene | Prop;
}

type LibraryCardAsset = (Character | Scene | Prop) & {
  __libraryVariantCount?: number;
  __libraryUsageCount?: number;
  __libraryOrder?: number;
  __libraryThumbnail?: AssetLibraryThumbnail | null;
};

function feedItemAsset(item: AssetLibraryFeedItem, order: number): LibraryCardAsset {
  const variant: ImageVariant | null = item.thumbnail
    ? {
        id: item.thumbnail.id,
        url: item.thumbnail.url,
        created_at: item.thumbnail.created_at,
      }
    : null;
  const ownerSource = item.source_kind === "project" ? "episode" : item.source_kind;
  const common = {
    id: item.id,
    name: item.name,
    description: item.description,
    starred: item.starred,
    source: ownerSource,
    source_id: item.source_id,
    series_id: item.series_id,
    episode_id: item.episode_id,
    __libraryVariantCount: item.variant_count,
    __libraryUsageCount: item.usage_count,
    __libraryOrder: order,
    __libraryThumbnail: item.thumbnail,
  } as const;
  if (item.asset_type === "character") {
    return {
      ...common,
      full_body_updated_at: item.updated_at,
      reference_sheet: variant
        ? { selected_image_id: variant.id, image_variants: [variant] }
        : undefined,
    } as LibraryCardAsset;
  }
  const imageAsset = variant
    ? { selected_id: variant.id, variants: [variant] }
    : undefined;
  return {
    ...common,
    image_asset: imageAsset,
  } as LibraryCardAsset;
}

function feedSources(
  items: AssetLibraryFeedItem[],
  globalLabel: string,
): AssetSource[] {
  const byOwner = new Map<string, AssetSource>();
  for (const [order, item] of items.entries()) {
    const key = `${item.source_kind}:${item.source_id}`;
    let source = byOwner.get(key);
    if (!source) {
      source = {
        id: item.source_kind === "global"
          ? "global"
          : `${item.source_kind}-${item.source_id}`,
        rawId: item.source_id,
        name: item.source_kind === "global" ? globalLabel : item.source_name,
        kind: item.source_kind,
        characters: [],
        scenes: [],
        props: [],
      };
      byOwner.set(key, source);
    }
    const asset = feedItemAsset(item, order);
    if (item.asset_type === "character") source.characters.push(asset as Character);
    else if (item.asset_type === "scene") source.scenes.push(asset as Scene);
    else source.props.push(asset as Prop);
  }
  return [...byOwner.values()];
}

function responseAsset(value: unknown): Character | Scene | Prop | null {
  if (!value || typeof value !== "object") return null;
  const response = value as {
    asset?: Character | Scene | Prop;
    id?: unknown;
  } & Partial<Character | Scene | Prop>;
  const candidate = response.asset ?? response;
  return typeof candidate.id === "string"
    ? (candidate as Character | Scene | Prop)
    : null;
}

function deleteConflictReferenceCount(error: unknown): number | null {
  if (!error || typeof error !== "object") return null;
  const response = (error as { response?: unknown }).response;
  if (!response || typeof response !== "object") return null;
  const status = (response as { status?: unknown }).status;
  const data = (response as { data?: unknown }).data;
  if (status !== 409 || !data || typeof data !== "object") return null;
  const detail = (data as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  if ((detail as { error?: unknown }).error !== "asset_in_use") return null;
  const references = (detail as { references?: unknown }).references;
  return Array.isArray(references) ? references.length : 0;
}

function variantCount(asset: Character | Scene | Prop, type: AssetTab): number {
  return (asset as LibraryCardAsset).__libraryVariantCount
    ?? primaryAssetImage(asset, SINGULAR[type])?.variants?.length
    ?? 0;
}

function usageCount(asset: Character | Scene | Prop): number | null {
  const value = (asset as LibraryCardAsset).__libraryUsageCount;
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.trunc(value)
    : null;
}

function serverOrder(asset: Character | Scene | Prop): number {
  return (asset as LibraryCardAsset).__libraryOrder ?? Number.MAX_SAFE_INTEGER;
}

function libraryThumbnail(
  asset: Character | Scene | Prop,
): AssetLibraryThumbnail | null {
  return (asset as LibraryCardAsset).__libraryThumbnail ?? null;
}

function localAssetKey(
  workspaceKey: string,
  source: Pick<AssetSource, "kind" | "rawId">,
  type: AssetTab,
  assetId: string,
): string {
  return `${workspaceKey}:${source.kind}:${source.rawId}:${type}:${assetId}`;
}

const SORT_SESSION_PREFIX = "enmotion:asset-library-sort:v1";
const SORT_SESSION_EVENT = "enmotion:asset-library-sort-changed";

function sortSessionKey(accountId: string, workspaceId: string): string {
  return `${SORT_SESSION_PREFIX}:${accountId}:${workspaceId}`;
}

function readSortPreference(raw: string): { mode: SortMode; order: SortOrder } {
  try {
    const parsed = JSON.parse(raw || "null") as {
      mode?: unknown;
      order?: unknown;
    } | null;
    if (parsed?.mode === "default" && parsed.order === "asc") {
      return { mode: "default", order: "asc" };
    }
    if (parsed?.mode === "name" && parsed.order === "asc") {
      return { mode: "name", order: "asc" };
    }
    if (parsed?.mode === "recent" && parsed.order === "desc") {
      return { mode: "recent", order: "desc" };
    }
    if (
      parsed?.mode === "usage"
      && (parsed.order === "asc" || parsed.order === "desc")
    ) {
      return { mode: "usage", order: parsed.order };
    }
  } catch {
    // Unavailable storage and malformed/legacy values use safe defaults.
  }
  return { mode: "default", order: "asc" };
}

function sortPreferenceSnapshot(key: string): string {
  if (typeof window === "undefined") return "";
  try {
    return window.sessionStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function saveSortPreference(
  key: string,
  preference: { mode: SortMode; order: SortOrder },
): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(key, JSON.stringify(preference));
    window.dispatchEvent(
      new CustomEvent<string>(SORT_SESSION_EVENT, { detail: key }),
    );
  } catch {
    // Sorting remains fully functional when session storage is unavailable.
  }
}

export default function AssetLibraryPage() {
  const t = useTranslations("library");
  const tc = useTranslations("common");
  const tui = useTranslations("ui.library");
  const { user } = useAuth();
  const [localOverrides, setLocalOverrides] = useState<Record<string, LocalAssetOverride>>({});
  const [activeType, setActiveType] = useState<TypeFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [sortOpen, setSortOpen] = useState(false);
  const [viewAxis, setViewAxis] = useState<ViewAxis>("type");
  const [starredOnly, setStarredOnly] = useState(false);
  const [selected, setSelected] = useState<{ sourceId: string; assetId: string; type: AssetTab } | null>(null);
  const [selectedContext, setSelectedContext] = useState<SelectedAssetContext | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<SelectedAssetDetail | null>(null);
  const [editorTarget, setEditorTarget] = useState<AssetRef | null>(null);
  const [newAssetOpen, setNewAssetOpen] = useState(false);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [favoritePendingKeys, setFavoritePendingKeys] = useState<Set<string>>(
    () => new Set(),
  );
  const [slowLoading, setSlowLoading] = useState(false);
  const deleteRequestRef = useRef<string | null>(null);
  const sortButtonRef = useRef<HTMLButtonElement>(null);
  const sortMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedSearch(searchQuery.trim()),
      250,
    );
    return () => window.clearTimeout(timer);
  }, [searchQuery]);

  const workspaceKey = user?.workspace_id || user?.id || "desktop";
  const accountKey = user?.id || "desktop";
  const preferenceKey = sortSessionKey(accountKey, workspaceKey);
  const subscribeToSortPreference = useCallback(
    (onStoreChange: () => void) => {
      if (typeof window === "undefined") return () => undefined;
      const handleStorage = (event: StorageEvent) => {
        if (event.key === preferenceKey) onStoreChange();
      };
      const handleSameWindow = (event: Event) => {
        if ((event as CustomEvent<string>).detail === preferenceKey) {
          onStoreChange();
        }
      };
      window.addEventListener("storage", handleStorage);
      window.addEventListener(SORT_SESSION_EVENT, handleSameWindow);
      return () => {
        window.removeEventListener("storage", handleStorage);
        window.removeEventListener(SORT_SESSION_EVENT, handleSameWindow);
      };
    },
    [preferenceKey],
  );
  const getSortPreferenceSnapshot = useCallback(
    () => sortPreferenceSnapshot(preferenceKey),
    [preferenceKey],
  );
  const sortPreferenceRaw = useSyncExternalStore(
    subscribeToSortPreference,
    getSortPreferenceSnapshot,
    () => "",
  );
  const { mode: sortMode, order: sortOrder } = useMemo(
    () => readSortPreference(sortPreferenceRaw),
    [sortPreferenceRaw],
  );
  const normalizedSortPreference = useMemo(
    () => JSON.stringify({ mode: sortMode, order: sortOrder }),
    [sortMode, sortOrder],
  );

  useEffect(() => {
    if (sortPreferenceRaw !== normalizedSortPreference) {
      saveSortPreference(preferenceKey, { mode: sortMode, order: sortOrder });
    }
  }, [
    normalizedSortPreference,
    preferenceKey,
    sortMode,
    sortOrder,
    sortPreferenceRaw,
  ]);

  const query = useMemo(() => ({
    assetType: activeType === "all" ? undefined : SINGULAR[activeType],
    starred: starredOnly,
    search: debouncedSearch,
    sort: sortMode,
    order: sortOrder,
    limit: 50,
  }), [activeType, debouncedSearch, sortMode, sortOrder, starredOnly]);
  const {
    snapshot: librarySnapshot,
    refresh: refreshLibrary,
    loadMore,
  } = useAssetLibraryQuery(workspaceKey, query);

  useEffect(
    () => subscribeToAssetLibraryChanges((detail) => {
      if (detail.remote && detail.invalidateCollection) refreshLibrary();
    }),
    [refreshLibrary],
  );

  // The query controller deliberately retains the last successful workspace
  // snapshot while a different sort/filter request revalidates in the
  // background. Rendering that snapshot directly avoids a blank library
  // without mutating refs during React's render phase.
  const displayedFeed = librarySnapshot.data;

  const sources = useMemo(() => {
    const baseSources = feedSources(
      displayedFeed?.items ?? [],
      t("globalGroup"),
    );
    return baseSources
      .map((source) => {
        const patchAssets = (
          assets: (Character | Scene | Prop)[],
          type: AssetTab,
        ) =>
          assets.flatMap((asset) => {
            const override = localOverrides[
              localAssetKey(workspaceKey, source, type, asset.id)
            ];
            if (override?.deleted) return [];
            const patchedAsset = override?.asset
              ? ({ ...asset, ...override.asset } as Character | Scene | Prop)
              : asset;
            return override?.starred === undefined
              ? [patchedAsset]
              : [{ ...patchedAsset, starred: override.starred }];
          });
        return {
          ...source,
          characters: patchAssets(source.characters, "characters") as Character[],
          scenes: patchAssets(source.scenes, "scenes") as Scene[],
          props: patchAssets(source.props, "props") as Prop[],
        };
      })
      .filter(
        (source) =>
          source.kind !== "global" ||
          source.characters.length + source.scenes.length + source.props.length > 0,
      );
  }, [displayedFeed?.items, localOverrides, t, workspaceKey]);

  // Facets reflect the active server-side search/source/favorite scope while
  // remaining independent of the selected type pill.
  const counts = displayedFeed?.facets ?? {
    characters: 0,
    scenes: 0,
    props: 0,
    all: 0,
    starred: 0,
  };

  const typePills: { id: TypeFilter; label: string; count: number }[] = [
    { id: "all", label: t("allLabel"), count: counts.all },
    { id: "characters", label: t("characterLabel"), count: counts.characters },
    { id: "scenes", label: t("sceneLabel"), count: counts.scenes },
    { id: "props", label: t("propLabel"), count: counts.props },
  ];

  const TYPE_LABEL: Record<AssetTab, string> = {
    characters: t("characterLabel"),
    scenes: t("sceneLabel"),
    props: t("propLabel"),
  };

  const sortOptions: {
    id: string;
    mode: SortMode;
    order: SortOrder;
    label: string;
  }[] = [
    { id: "default", mode: "default", order: "asc", label: t("sortDefault") },
    { id: "name", mode: "name", order: "asc", label: t("sortName") },
    { id: "recent", mode: "recent", order: "desc", label: t("sortRecent") },
    { id: "usage-desc", mode: "usage", order: "desc", label: `${t("sortUsage")} · ${t("mostUsed")}` },
    { id: "usage-asc", mode: "usage", order: "asc", label: `${t("sortUsage")} · ${t("leastUsed")}` },
  ];
  const currentSort = sortOptions.find(
    (option) => option.mode === sortMode && option.order === sortOrder,
  ) ?? sortOptions[0];

  useEffect(() => {
    if (!sortOpen) return;
    const active = sortMenuRef.current?.querySelector<HTMLElement>(
      '[role="option"][aria-selected="true"]',
    );
    (active ?? sortMenuRef.current?.querySelector<HTMLElement>('[role="option"]'))?.focus();
  }, [sortOpen]);

  // 渲染模型：两种轴。
  //  - "type"（默认）：按资产类型分 3 组（角色/场景/道具），每组含所有 source 的该类型资产，
  //    卡片副标题显示所属 source 名。
  //  - "source"：按 source 分组（系列/项目/全局），保持原行为。
  // 搜索、筛选和排序全部由后端在分页前完成。分组只保留服务端返回
  // 的相对顺序，避免对局部页面进行第二次客户端排序。
  const groups = useMemo<RenderGroup[]>(() => {
    const scopedTypes: AssetTab[] = activeType === "all" ? ["characters", "scenes", "props"] : [activeType];
    const serverSorted = (items: RenderItem[]) =>
      items.sort((left, right) => serverOrder(left.asset) - serverOrder(right.asset));
    const typeLabel = (ty: AssetTab) =>
      ty === "characters" ? t("characterLabel") : ty === "scenes" ? t("sceneLabel") : t("propLabel");

    if (viewAxis === "type") {
      return scopedTypes
        .map((ty): RenderGroup => {
          const items: RenderItem[] = [];
          for (const src of sources)
            for (const a of src[ty] as (Character | Scene | Prop)[]) items.push({ asset: a, type: ty, src });
          serverSorted(items);
          return { key: `type-${ty}`, title: typeLabel(ty), meta: String(items.length), items };
        })
        .filter((grp) => grp.items.length > 0);
    }

    const kindLabel = (k: AssetSource["kind"]) =>
      k === "series" ? t("series") : k === "global" ? t("globalGroup") : t("project");
    return sources
      .map((src): RenderGroup => {
        const items: RenderItem[] = [];
        for (const ty of scopedTypes)
          for (const a of src[ty] as (Character | Scene | Prop)[]) items.push({ asset: a, type: ty, src });
        serverSorted(items);
        return { key: src.id, title: src.name, meta: `${kindLabel(src.kind)} · ${items.length}`, items };
      })
      .filter((grp) => grp.items.length > 0);
  }, [sources, activeType, viewAxis, t]);

  const visibleCount = displayedFeed?.page.total
    ?? groups.reduce((acc, g) => acc + g.items.length, 0);

  const selectAsset = (
    source: AssetSource,
    asset: Character | Scene | Prop,
    type: AssetTab,
  ) => {
    setSelected({ sourceId: source.id, assetId: asset.id, type });
    setSelectedContext({ source, asset, type });
  };

  const patchLocalAsset = (
    source: AssetSource,
    type: AssetTab,
    asset: Character | Scene | Prop,
  ) => {
    const overrideKey = localAssetKey(workspaceKey, source, type, asset.id);
    setLocalOverrides((current) => ({
      ...current,
      [overrideKey]: {
        ...current[overrideKey],
        asset,
        starred: !!asset.starred,
      },
    }));
    const detailKey = `${source.kind}:${source.rawId}:${type}:${asset.id}`;
    setSelectedDetail((current) =>
      current?.key === detailKey ? { key: detailKey, asset } : current
    );
    setSelectedContext((current) =>
      current
      && current.source.id === source.id
      && current.asset.id === asset.id
      && current.type === type
        ? { ...current, asset }
        : current
    );
  };

  const toggleStar = async (sourceId: string, assetId: string, type: AssetTab) => {
    const src = sources.find((source) => source.id === sourceId)
      ?? (
        selectedContext?.source.id === sourceId
        && selectedContext.asset.id === assetId
        && selectedContext.type === type
          ? selectedContext.source
          : undefined
      );
    if (!src) return;
    const cur = (src[type] as (Character | Scene | Prop)[]).find((a) => a.id === assetId);
    const prevStarred = !!cur?.starred;
    const desiredStarred = !prevStarred;
    const overrideKey = localAssetKey(workspaceKey, src, type, assetId);
    const requestKey = `${src.kind}:${src.rawId}:${SINGULAR[type]}:${assetId}`;
    if (favoritePendingKeys.has(requestKey)) return;
    const setStarredTo = (starred: boolean) =>
      setLocalOverrides((current) => ({
        ...current,
        [overrideKey]: { ...current[overrideKey], starred },
      }));
    setFavoritePendingKeys((current) => {
      const next = new Set(current);
      next.add(requestKey);
      return next;
    });
    setStarredTo(desiredStarred);
    try {
      const updated = await api.setOwnedAssetFavorite(
        src.kind,
        src.rawId,
        SINGULAR[type],
        assetId,
        desiredStarred,
      ) as Character | Scene | Prop;
      patchLocalAsset(src, type, updated);
      publishAssetMutation({
        ref: {
          ownerKind: src.kind,
          ownerId: src.rawId,
          assetType: SINGULAR[type],
          assetId,
        },
        asset: updated,
      });
    } catch {
      setStarredTo(prevStarred);
      toast.error(t("favoriteUpdateFailed"), {
        body: t("favoriteUpdateFailedBody"),
      });
    } finally {
      setFavoritePendingKeys((current) => {
        const next = new Set(current);
        next.delete(requestKey);
        return next;
      });
    }
  };

  const deleteAsset = async (sourceId: string, assetId: string, type: AssetTab) => {
    const source = sources.find((item) => item.id === sourceId)
      ?? (
        selectedContext?.source.id === sourceId
        && selectedContext.asset.id === assetId
        && selectedContext.type === type
          ? selectedContext.source
          : undefined
      );
    const asset = source
      ? (source[type] as (Character | Scene | Prop)[]).find((item) => item.id === assetId)
      : undefined;
    if (!source || !asset) return;

    const requestKey = `${source.kind}:${source.rawId}:${type}:${assetId}`;
    if (deleteRequestRef.current) return;
    deleteRequestRef.current = requestKey;
    setDeletingKey(requestKey);
    try {
      const assetType = SINGULAR[type];
      const impact = await api.getOwnedAssetDeleteImpact(
        source.kind,
        source.rawId,
        assetType,
        assetId,
      );
      const confirmation = impact.has_references
        ? t("confirmDeleteReferencedAsset", {
            name: asset.name,
            count: impact.reference_count,
          })
        : t("confirmDeleteAsset", {
            name: asset.name,
            type: TYPE_LABEL[type],
          });
      if (!window.confirm(confirmation)) return;

      let force = impact.has_references;
      try {
        await api.deleteOwnedAsset(source.kind, source.rawId, assetType, assetId, force);
      } catch (error) {
        const changedReferenceCount = deleteConflictReferenceCount(error);
        if (force || changedReferenceCount === null) throw error;
        const confirmedChangedImpact = window.confirm(
          t("confirmDeleteReferencedAsset", {
            name: asset.name,
            count: changedReferenceCount,
          }),
        );
        if (!confirmedChangedImpact) return;
        force = true;
        await api.deleteOwnedAsset(source.kind, source.rawId, assetType, assetId, true);
      }

      // The server is authoritative. Mutate the local feed only after its
      // deletion response succeeds, then broadcast to every other asset view.
      const overrideKey = localAssetKey(workspaceKey, source, type, assetId);
      setLocalOverrides((current) => ({
        ...current,
        [overrideKey]: { ...current[overrideKey], deleted: true },
      }));
      setSelected((current) =>
        current?.sourceId === sourceId && current.assetId === assetId && current.type === type
          ? null
          : current,
      );
      setSelectedContext((current) =>
        current?.source.id === sourceId
        && current.asset.id === assetId
        && current.type === type
          ? null
          : current,
      );
      setEditorTarget((current) =>
        current?.ownerKind === source.kind &&
        current.ownerId === source.rawId &&
        current.assetType === assetType &&
        current.assetId === assetId
          ? null
          : current,
      );
      notifyAssetLibraryChanged({
        source: source.kind,
        ...(source.kind === "series" ? { seriesId: source.rawId } : {}),
        ...(source.kind === "project" ? { projectId: source.rawId } : {}),
        assetType,
        assetId,
      });
      toast.success(t("deleteSuccess"), {
        body: t("deleteSuccessBody", { name: asset.name }),
      });
    } catch (error) {
      console.error("asset delete failed", error);
      toast.error(t("deleteFailed"), { body: t("deleteFailedBody") });
    } finally {
      if (deleteRequestRef.current === requestKey) deleteRequestRef.current = null;
      setDeletingKey((current) => (current === requestKey ? null : current));
    }
  };

  // 选中资产的实时引用（以 sources 为单一数据源，保证星标等变更同步到 inspector）。
  const liveSelectedSource = selected
    ? sources.find((source) => source.id === selected.sourceId)
    : undefined;
  const liveSelectedAsset =
    selected && liveSelectedSource
      ? (liveSelectedSource[selected.type] as (Character | Scene | Prop)[])
          .find((asset) => asset.id === selected.assetId)
      : undefined;
  const contextMatchesSelection = Boolean(
    selected
    && selectedContext
    && selectedContext.source.id === selected.sourceId
    && selectedContext.asset.id === selected.assetId
    && selectedContext.type === selected.type,
  );
  const selectedSource = liveSelectedSource
    ?? (contextMatchesSelection ? selectedContext?.source : undefined);
  const selectedAsset = liveSelectedAsset
    ?? (contextMatchesSelection ? selectedContext?.asset : undefined);
  const selectedDetailKey =
    selected && selectedSource && selectedAsset
      ? `${selectedSource.kind}:${selectedSource.rawId}:${selected.type}:${selectedAsset.id}`
      : null;

  useEffect(() => {
    if (!selected || !liveSelectedSource || !liveSelectedAsset) return;
    setSelectedContext({
      source: liveSelectedSource,
      asset: liveSelectedAsset,
      type: selected.type,
    });
  }, [liveSelectedAsset, liveSelectedSource, selected]);

  useEffect(() => {
    if (!selected || !selectedSource || !selectedAsset || !selectedDetailKey) return;
    let active = true;
    void api
      .getOwnedAsset(
        selectedSource.kind,
        selectedSource.rawId,
        SINGULAR[selected.type],
        selectedAsset.id,
      )
      .then((value) => {
        if (!active) return;
        const asset = responseAsset(value);
        if (asset) setSelectedDetail({ key: selectedDetailKey, asset });
      })
      .catch(() => {
        // The compact, validated card remains usable if the optional detail
        // request fails. Never turn a detail-panel error into an empty library.
      });
    return () => {
      active = false;
    };
  }, [
    selected,
    selectedAsset,
    selectedDetailKey,
    selectedSource,
  ]);

  const errorMessage = (error: AssetLibraryRequestError | null): string => {
    switch (error?.kind) {
      case "authentication":
        return t("errorSessionExpired");
      case "permission":
        return t("errorPermissionDenied");
      case "timeout":
        return t("errorTimeout");
      case "network":
        return t("errorNetworkUnavailable");
      case "server":
      case "rate-limit":
        return t("errorServerUnavailable");
      case "invalid-response":
      case "workspace":
        return t("errorInvalidResponse");
      case "synchronization":
        return t("errorSynchronization");
      default:
        return t("loadFailedBody");
    }
  };
  const initialLoading =
    librarySnapshot.phase === "initial-loading" && !displayedFeed;
  const initialError =
    librarySnapshot.phase === "initial-error" && !displayedFeed;
  const staleRefreshError =
    librarySnapshot.phase === "refresh-error-with-stale-data"
    || (librarySnapshot.phase === "initial-error" && !!displayedFeed);
  const refreshing =
    librarySnapshot.phase === "refreshing-with-stale-data"
    || (librarySnapshot.phase === "initial-loading" && !!displayedFeed);
  const authoritativeEmpty =
    librarySnapshot.phase === "success-empty"
    && librarySnapshot.data?.facets.all === 0;
  const loadFailureTitle = sortMode === "usage"
    ? t("usageDataFailed")
    : t("loadFailed");

  const formatUsageCount = (count: number | null): string => {
    if (count === null) return t("usageDataFailed");
    if (count === 0) return t("neverUsed");
    if (count === 1) return t("usedOnce");
    return t("usedTimes", { count });
  };

  useEffect(() => {
    const timer = window.setTimeout(
      () => setSlowLoading(initialLoading),
      initialLoading ? 5_000 : 0,
    );
    return () => window.clearTimeout(timer);
  }, [initialLoading]);

  const technicalDetails = (error: AssetLibraryRequestError | null) => {
    if (!error) return null;
    const details = error.diagnostics;
    return (
      <dl className="mt-2 grid grid-cols-[auto,minmax(0,1fr)] gap-x-3 gap-y-1 break-all rounded-lg bg-surface-inset p-3 font-mono text-[0.625rem]">
        <dt>{t("errorCode")}</dt>
        <dd>{details.code}</dd>
        <dt>{t("httpStatus")}</dt>
        <dd>{details.status ?? "—"}</dd>
        <dt>{t("requestId")}</dt>
        <dd className="flex min-w-0 items-center gap-1.5">
          <code className="min-w-0 select-all break-all">{details.requestId}</code>
          <button
            type="button"
            aria-label={t("copyRequestId")}
            title={t("copyRequestId")}
            onClick={() => void navigator.clipboard?.writeText(details.requestId)}
            className="shrink-0 rounded p-1 hover:bg-elevated"
          >
            <Copy size={11} />
          </button>
        </dd>
        <dt>{t("attemptedAt")}</dt>
        <dd>{details.attemptedAt}</dd>
        <dt>{t("retryable")}</dt>
        <dd>{details.retryable ? t("yes") : t("no")}</dd>
      </dl>
    );
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <header className="px-4 md:px-7 pt-5 md:pt-6 pb-3 flex items-end gap-5">
        <div className="flex-1 min-w-0">
          <h1 className="text-[1.625rem] md:text-[2.125rem] font-display atelier-display font-semibold text-foreground leading-tight tracking-tight">
            {t("title")}
          </h1>
        </div>
        <div className="flex items-center gap-2.5 pb-1">
          {refreshing && (
            <span className="inline-flex items-center gap-1.5 text-[0.6875rem] text-text-muted">
              <Loader2 size={12} className="animate-spin" />
              {t("refreshing")}
            </span>
          )}
          <span className="min-w-[7rem] text-right font-mono text-[0.6875rem] tabular-nums text-text-muted tracking-[0.1em] uppercase">
            {t("assetCount", { count: visibleCount })}
          </span>
          <button
            type="button"
            onClick={() => setNewAssetOpen(true)}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full bg-primary text-on-accent text-[0.875rem] font-semibold hover:bg-primary-hover transition-colors"
          >
            <Plus size={14} />
            {t("newAsset")}
          </button>
        </div>
      </header>

      {/* Toolbar: 视图切换 + 类型 pills（带计数）+ ★ + 搜索 + 排序 */}
      <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 px-4 pb-2 md:flex md:flex-wrap md:px-7">
        {/* 视图切换：按类型 ↔ 按项目 */}
        <div
          className="col-span-3 inline-flex w-full overflow-x-auto p-[3px] rounded-full bg-surface-inset atelier-pill-tabs md:w-auto md:overflow-visible"
          role="group"
          aria-label={t("viewLabel")}
        >
          {([
            { id: "type", label: t("viewByType") },
            { id: "source", label: t("viewByProject") },
          ] as { id: ViewAxis; label: string }[]).map((v) => {
            const on = viewAxis === v.id;
            return (
              <button
                key={v.id}
                type="button"
                aria-pressed={on}
                onClick={() => setViewAxis(v.id)}
                className={`px-3.5 py-1.5 rounded-full text-[0.6875rem] font-semibold transition-colors ${
                  on ? "text-foreground atelier-pill-tab-active bg-surface shadow-sm" : "text-text-muted hover:text-foreground"
                }`}
              >
                {v.label}
              </button>
            );
          })}
        </div>

        <div className="col-span-3 inline-flex w-full overflow-x-auto p-[3px] rounded-full bg-surface-inset atelier-pill-tabs md:w-auto md:overflow-visible" role="tablist" aria-label={t("assetTypeAria")} onKeyDown={rovingKeyDown}>
          {typePills.map((pill) => {
            const on = activeType === pill.id;
            return (
              <button
                key={pill.id}
                role="tab"
                aria-selected={on}
                tabIndex={on ? 0 : -1}
                onClick={() => setActiveType(pill.id)}
                className={`inline-flex shrink-0 items-center gap-1.5 px-3.5 py-1.5 rounded-full text-[0.6875rem] font-semibold transition-colors ${
                  on ? "text-foreground atelier-pill-tab-active bg-surface shadow-sm" : "text-text-muted hover:text-foreground"
                }`}
              >
                {pill.label}
                <span className={`font-mono text-[0.59375rem] ${on ? "text-text-secondary" : "text-text-muted"}`}>{pill.count}</span>
              </button>
            );
          })}
        </div>

        {/* ★ 加星过滤 */}
        <button
          type="button"
          aria-pressed={starredOnly}
          aria-label={t("starredOnlyAria")}
          onClick={() => setStarredOnly((v) => !v)}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[0.6875rem] font-semibold border transition-colors ${
            starredOnly
              ? "text-status-starred-fg bg-status-starred-bg border-status-starred-border"
              : "text-text-muted border-glass-border hover:text-foreground"
          }`}
        >
          <Star size={12} className={starredOnly ? "fill-current" : ""} />
          {counts.starred}
        </button>

        <div className="relative min-w-0 w-full bg-surface-inset border border-glass-border rounded-full atelier-search-input md:flex-1 md:min-w-[200px] md:max-w-[340px]">
          <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t("searchPlaceholder")}
            aria-label={t("searchPlaceholder")}
            className="w-full bg-transparent border-0 rounded-full py-2 pl-9 pr-4 text-[0.8125rem] text-foreground placeholder-text-muted focus:outline-none"
          />
        </div>

        {/* 排序下拉：后端在分页前完成所有排序。 */}
        <div className="relative">
          <button
            ref={sortButtonRef}
            type="button"
            onClick={() => setSortOpen((v) => !v)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                setSortOpen(true);
              }
            }}
            aria-haspopup="listbox"
            aria-expanded={sortOpen}
            aria-label={t("sortLabel")}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[0.6875rem] font-medium text-text-muted border border-glass-border hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70"
          >
            <ArrowDownUp size={12} />
            {currentSort.label}
            <ChevronDown size={12} className={`transition-transform ${sortOpen ? "rotate-180" : ""}`} />
          </button>
          {sortOpen && (
            <>
              {/* 点外关闭遮罩 */}
              <button
                type="button"
                aria-hidden="true"
                tabIndex={-1}
                onClick={() => setSortOpen(false)}
                className="fixed inset-0 z-40 cursor-default"
              />
              <div
                ref={sortMenuRef}
                role="listbox"
                aria-label={t("sortLabel")}
                onKeyDown={(event) => {
                  const options = Array.from(
                    event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="option"]'),
                  );
                  const currentIndex = options.indexOf(document.activeElement as HTMLButtonElement);
                  if (event.key === "Escape") {
                    event.preventDefault();
                    setSortOpen(false);
                    sortButtonRef.current?.focus();
                    return;
                  }
                  if (event.key === "Enter" || event.key === " ") {
                    if (currentIndex >= 0) {
                      event.preventDefault();
                      options[currentIndex].click();
                    }
                    return;
                  }
                  let nextIndex = currentIndex;
                  if (event.key === "ArrowDown") nextIndex = (currentIndex + 1 + options.length) % options.length;
                  else if (event.key === "ArrowUp") nextIndex = (currentIndex - 1 + options.length) % options.length;
                  else if (event.key === "Home") nextIndex = 0;
                  else if (event.key === "End") nextIndex = options.length - 1;
                  else return;
                  event.preventDefault();
                  options[nextIndex]?.focus();
                }}
                className="absolute right-0 top-full mt-1.5 z-50 min-w-[180px] glass-panel border border-glass-border rounded-xl p-1.5 shadow-xl"
              >
                {sortOptions.map((opt) => {
                  const on = sortMode === opt.mode && sortOrder === opt.order;
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      role="option"
                      aria-selected={on}
                      tabIndex={on ? 0 : -1}
                      onClick={() => {
                        saveSortPreference(preferenceKey, {
                          mode: opt.mode,
                          order: opt.order,
                        });
                        setSortOpen(false);
                        sortButtonRef.current?.focus();
                      }}
                      className={`w-full flex items-center justify-between gap-3 px-3 py-1.5 rounded-lg text-[0.75rem] font-medium text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70 ${
                        on
                          ? "text-primary bg-surface-inset"
                          : "text-text-secondary hover:text-foreground hover:bg-surface-inset"
                      }`}
                    >
                      <span>{opt.label}</span>
                      {on && <Check size={13} />}
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Body: 网格（按系列分组）+ 右侧 inspector */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        <div className="flex-1 overflow-y-auto px-7 pb-10 pt-3">
          {staleRefreshError && (
            <div
              role="alert"
              className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-status-pending-border bg-status-pending-bg px-4 py-3 text-[0.75rem]"
            >
              <AlertCircle size={16} className="text-status-pending-fg shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-foreground">
                  {sortMode === "usage" ? t("usageDataFailed") : t("refreshFailed")}
                </p>
                <p className="text-text-secondary">{t("showingCachedAssets")}</p>
                <details className="mt-1 text-text-muted">
                  <summary className="cursor-pointer">{t("technicalDetails")}</summary>
                  {technicalDetails(librarySnapshot.error)}
                </details>
              </div>
              <button
                type="button"
                onClick={refreshLibrary}
                className="glass-button inline-flex items-center gap-1.5 text-[0.75rem] font-semibold"
              >
                <RefreshCw size={13} />
                {t("retry")}
              </button>
            </div>
          )}
          {initialLoading ? (
            <div className="flex items-center justify-center py-20">
              <div className="inline-flex items-center gap-2 text-text-secondary text-[0.8125rem]">
                <Loader2 size={15} className="animate-spin" />
                {tc("loading")}
              </div>
              {slowLoading && (
                <p role="status" className="mt-3 text-center text-[0.75rem] text-text-muted">
                  {t("loadingLonger")}
                </p>
              )}
            </div>
          ) : initialError ? (
            <div
              role="alert"
              className="mx-auto my-12 max-w-xl rounded-2xl border border-status-failed-border bg-status-failed-bg p-8 text-center"
            >
              <AlertCircle size={32} className="mx-auto mb-3 text-status-failed-fg" />
              <h2 className="font-display text-lg font-semibold text-foreground">
                {loadFailureTitle}
              </h2>
              <p className="mt-2 text-[0.8125rem] text-text-secondary">
                {errorMessage(librarySnapshot.error)}
              </p>
              <details className="mx-auto mt-3 max-w-md text-left text-[0.6875rem] text-text-muted">
                <summary className="cursor-pointer text-center">{t("technicalDetails")}</summary>
                {technicalDetails(librarySnapshot.error)}
              </details>
              <button
                type="button"
                onClick={refreshLibrary}
                className="mt-5 inline-flex items-center gap-2 rounded-full bg-primary px-5 py-2 text-[0.8125rem] font-semibold text-on-accent"
              >
                <RefreshCw size={14} />
                {t("retry")}
              </button>
            </div>
          ) : authoritativeEmpty ? (
            <div className="flex flex-col items-center justify-center py-16">
              <div className="glass-panel atelier-card p-10 rounded-2xl border border-glass-border text-center max-w-[620px] w-full relative overflow-hidden">
                <div className="relative z-[1] flex flex-col items-center gap-4">
                  <div className="font-mono text-[0.625rem] uppercase tracking-[0.22em] text-text-muted">
                    {tui("categories")}
                  </div>
                  <p className="text-[2.125rem] font-display atelier-display font-medium italic leading-[1.25] tracking-tight text-foreground">
                    {t("emptyQuote")}
                  </p>
                  <p className="text-[0.9375rem] text-text-secondary max-w-[440px]">{t("noAssetsHint")}</p>
                </div>
              </div>
            </div>
          ) : groups.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-text-muted">
              <Search size={48} className="mb-3 opacity-60" />
              <p className="text-[0.9375rem] font-display atelier-display text-foreground">{t("noMatchTitle")}</p>
              <p className="text-[0.75rem] text-text-muted mt-1">{tc("noMatchHint")}</p>
              <button
                type="button"
                onClick={() => { setActiveType("all"); setSearchQuery(""); setStarredOnly(false); }}
                className="mt-4 glass-button text-[0.8125rem] font-semibold"
              >
                {tc("clearFilters")}
              </button>
            </div>
          ) : (
            <div className="space-y-6">
              {groups.map((grp) => (
                <div key={grp.key}>
                  {/* 分组标题 + 尾线 + 计数 */}
                  <div className="flex items-baseline gap-3 mb-4">
                    <span className="text-[1.5rem] font-display atelier-display font-semibold text-foreground tracking-tight">{grp.title}</span>
                    <span className="font-mono text-[0.625rem] text-text-muted tracking-wide uppercase">{grp.meta}</span>
                    <span className="atelier-group-line flex-1 h-px bg-border-subtle" />
                  </div>

                  {/* 卡片网格（库专用富卡片） */}
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                    {grp.items.map(({ asset, type, src }, i) => {
                      const url = primaryAssetDisplayUrl(asset, SINGULAR[type]);
                      const vc = variantCount(asset, type);
                      const isSel = selected?.sourceId === src.id && selected?.assetId === asset.id && selected?.type === type;
                      const isStar = !!asset.starred;
                      const isChar = type === "characters";
                      const thumbnail = libraryThumbnail(asset);
                      const initialOrder = serverOrder(asset);
                      const responsiveSources = thumbnail?.state === "ready"
                        ? thumbnail.derivatives.map((derivative) => ({
                            src: derivative.url,
                            width: derivative.width,
                            type: derivative.mime_type,
                          }))
                        : [];
                      return (
                        <div
                          key={`${src.id}-${type}-${asset.id}`}
                          data-asset-library-card
                          role="button"
                          tabIndex={0}
                          onClick={() => selectAsset(src, asset, type)}
                          onKeyDown={(e) => {
                            // 仅当卡片自身获得焦点时才响应；避免嵌套的 star <button> 在 Enter/Space 时双触发
                            if (e.target !== e.currentTarget) return;
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              selectAsset(src, asset, type);
                            }
                          }}
                          aria-current={isSel ? "true" : undefined}
                          className={`atelier-asset-card atelier-reveal group relative text-left rounded-xl overflow-hidden border transition-all cursor-pointer ${
                            isSel ? "border-primary/60 ring-1 ring-primary/40" : "border-glass-border hover:-translate-y-1"
                          }`}
                          style={{ animationDelay: `${Math.min(i * 50, 250)}ms` }}
                        >
                          <div className={`${isChar ? "aspect-[4/3]" : "aspect-square"} bg-surface-inset overflow-hidden relative`}>
                            {url ? (
                              isChar ? (
                                <PreviewImage
                                  src={url}
                                  alt={asset.name}
                                  className="relative h-full w-full bg-[radial-gradient(circle_at_center,var(--color-bg-elevated),var(--color-bg-inset))]"
                                  imgClassName="object-contain transition-transform group-hover:scale-105"
                                  noLightbox
                                  diagnosticContext="asset-library-card"
                                  responsiveSources={responsiveSources}
                                  mediaKey={thumbnail
                                    ? `${thumbnail.media_id}:${thumbnail.revision}`
                                    : undefined}
                                  intrinsicWidth={thumbnail?.width ?? undefined}
                                  intrinsicHeight={thumbnail?.height ?? undefined}
                                  sizes="(max-width: 767px) 50vw, (max-width: 1023px) 33vw, (max-width: 1279px) 25vw, 20vw"
                                  loading={initialOrder < 12 ? "eager" : "lazy"}
                                  fetchPriority={initialOrder === 0 ? "high" : "auto"}
                                  sizeBucket="large"
                                />
                              ) : (
                                <PreviewImage
                                  src={url}
                                  alt={asset.name}
                                  className="h-full w-full"
                                  imgClassName="object-cover transition-transform group-hover:scale-105"
                                  noLightbox
                                  diagnosticContext="asset-library-card"
                                  responsiveSources={responsiveSources}
                                  mediaKey={thumbnail
                                    ? `${thumbnail.media_id}:${thumbnail.revision}`
                                    : undefined}
                                  intrinsicWidth={thumbnail?.width ?? undefined}
                                  intrinsicHeight={thumbnail?.height ?? undefined}
                                  sizes="(max-width: 767px) 50vw, (max-width: 1023px) 33vw, (max-width: 1279px) 25vw, 20vw"
                                  loading={initialOrder < 12 ? "eager" : "lazy"}
                                  fetchPriority={initialOrder === 0 ? "high" : "auto"}
                                  sizeBucket="large"
                                />
                              )
                            ) : (
                              // 无图：atelier 文字/渐变封面（取代发灰占位图标）— 确定性渐变 + 颗粒 + 首字母
                              <div
                                className="absolute inset-0 grid place-items-center overflow-hidden"
                                style={{ background: coverGradient(asset.id || asset.name) }}
                                aria-hidden="true"
                              >
                                <div
                                  className="pointer-events-none absolute inset-0 mix-blend-overlay opacity-50"
                                  style={{ backgroundImage: GRAIN_URL }}
                                />
                                <span className="relative font-display atelier-display font-semibold leading-none select-none text-foreground/90 text-[clamp(1.75rem,4.5vw,2.75rem)]">
                                  {(Array.from(asset.name.trim())[0] || "?").toUpperCase()}
                                </span>
                              </div>
                            )}
                            {isStar && (
                              <div className="pointer-events-none absolute inset-0 shadow-[inset_0_0_44px_-8px_var(--color-status-starred-bg)]" aria-hidden="true" />
                            )}
                            {/* top row: star chip + variant chip */}
                            <div className="absolute top-2 left-2 right-2 flex items-center justify-between">
                              <FavoriteButton
                                pressed={isStar}
                                pending={
                                  favoritePendingKeys.has(
                                    `${src.kind}:${src.rawId}:${SINGULAR[type]}:${asset.id}`
                                  )
                                }
                                onChange={() => {
                                  void toggleStar(src.id, asset.id, type);
                                }}
                              />
                              {vc > 0 && (
                                <span className="px-2 py-[3px] rounded-full font-mono text-[0.5625rem] font-semibold text-white bg-black/55 backdrop-blur-md tracking-wide">
                                  {t("variantCount", { count: vc })}
                                </span>
                              )}
                            </div>
                            {/* kind chip（仅「按项目」视图 + 「全部」类型下显示，告知卡片类型） */}
                            {viewAxis === "source" && activeType === "all" && (
                              <span className="absolute bottom-2 left-2 px-2 py-[3px] rounded-full font-mono text-[0.53125rem] font-semibold uppercase tracking-[0.06em] text-white bg-black/55 backdrop-blur-md">
                                {TYPE_LABEL[type]}
                              </span>
                            )}
                          </div>
                          <div className="p-3">
                            <div className="text-sm font-medium text-foreground truncate">{asset.name}</div>
                            {viewAxis === "type" ? (
                              <div className="text-[0.6875rem] text-text-muted truncate mt-0.5">{src.name}</div>
                            ) : (
                              asset.description && <div className="text-[0.6875rem] text-text-muted truncate mt-0.5">{asset.description}</div>
                            )}
                            {sortMode === "usage" && (
                              <div
                                className="mt-1 font-mono text-[0.625rem] font-medium text-primary"
                                aria-label={`${t("usage")}: ${formatUsageCount(usageCount(asset))}`}
                              >
                                {formatUsageCount(usageCount(asset))}
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
              {librarySnapshot.data?.page.has_more && (
                <div className="flex flex-col items-center gap-2 pt-2">
                  {librarySnapshot.loadMoreError && (
                    <p role="alert" className="text-[0.6875rem] text-status-failed-fg">
                      {errorMessage(librarySnapshot.loadMoreError)}
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={loadMore}
                    disabled={librarySnapshot.isLoadingMore}
                    className="glass-button inline-flex items-center gap-2 text-[0.8125rem] font-semibold disabled:opacity-60"
                  >
                    {librarySnapshot.isLoadingMore
                      ? <Loader2 size={14} className="animate-spin" />
                      : <Plus size={14} />}
                    {librarySnapshot.isLoadingMore ? t("loadingMore") : t("loadMore")}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* 右侧 inspector（选中才出现） */}
        {selected && selectedAsset && selectedSource && (
          <AssetInspector
            asset={
              selectedDetail?.key === selectedDetailKey
                ? selectedDetail.asset
                : selectedAsset
            }
            type={selected.type}
            sourceName={selectedSource.name}
            sourceId={selected.sourceId}
            sourceKind={selectedSource.kind}
            usageCount={usageCount(selectedAsset)}
            starred={!!selectedAsset.starred}
            favoritePending={favoritePendingKeys.has(
              `${selectedSource.kind}:${selectedSource.rawId}:${SINGULAR[selected.type]}:${selectedAsset.id}`
            )}
            onClose={() => {
              setSelected(null);
              setSelectedContext(null);
            }}
            onToggleStar={() => {
              void toggleStar(selected.sourceId, selected.assetId, selected.type);
            }}
            onEdit={() => setEditorTarget({
              ownerKind: selectedSource.kind,
              ownerId: selectedSource.rawId,
              assetType: SINGULAR[selected.type],
              assetId: selectedAsset.id,
            })}
            onDelete={() => deleteAsset(selected.sourceId, selected.assetId, selected.type)}
            deleting={
              deletingKey ===
              `${selectedSource.kind}:${selectedSource.rawId}:${selected.type}:${selectedAsset.id}`
            }
          />
        )}
      </div>

      {/* 新建全局资产弹窗（T6-entries） */}
      {newAssetOpen && (
        <NewLibraryAssetDialog onClose={() => setNewAssetOpen(false)} />
      )}

      {editorTarget && (
        <SharedAssetEditor
          open
          assetRef={editorTarget}
          onClose={() => setEditorTarget(null)}
          onMutated={(updated, ref) => {
            const source = sources.find(
              (candidate) =>
                candidate.kind === ref.ownerKind && candidate.rawId === ref.ownerId
            );
            if (!source) return;
            const type = (
              ref.assetType === "character"
                ? "characters"
                : ref.assetType === "scene"
                  ? "scenes"
                  : "props"
            ) as AssetTab;
            patchLocalAsset(source, type, updated as EditableAsset);
          }}
          onConverted={(updated, previousRef, nextRef) => {
            const source = sources.find(
              (candidate) =>
                candidate.kind === previousRef.ownerKind &&
                candidate.rawId === previousRef.ownerId
            );
            if (!source) return;
            const previousType = (
              previousRef.assetType === "character"
                ? "characters"
                : previousRef.assetType === "scene"
                  ? "scenes"
                  : "props"
            ) as AssetTab;
            const previousKey = localAssetKey(
              workspaceKey,
              source,
              previousType,
              previousRef.assetId,
            );
            setLocalOverrides((current) => ({
              ...current,
              [previousKey]: { ...current[previousKey], deleted: true },
            }));
            const nextType = (
              nextRef.assetType === "character"
                ? "characters"
                : nextRef.assetType === "scene"
                  ? "scenes"
                  : "props"
            ) as AssetTab;
            patchLocalAsset(source, nextType, updated as EditableAsset);
            setEditorTarget(nextRef);
            setSelected({
              sourceId: source.id,
              assetId: updated.id,
              type: nextType,
            });
            setSelectedContext({
              source,
              asset: updated as EditableAsset,
              type: nextType,
            });
          }}
        />
      )}
    </div>
  );
}
