import { invalidateAssetLibraryQueries } from "@/lib/assetLibraryQuery";
import type { AssetRef, EditableAsset } from "@/components/assets/assetEditorTypes";
import { getWorkspaceStorageScope } from "@/lib/workspaceStorage";

export const ASSET_LIBRARY_CHANGED_EVENT = "enmotion:asset-library-changed";

export interface AssetLibraryChangeDetail {
    projectId?: string;
    seriesId?: string;
    /** Canonical owner of the changed asset. Global assets affect every
     *  project and series view, while project/series changes can be scoped. */
    source?: "global" | "project" | "series";
    assetType?: "character" | "scene" | "prop";
    assetId?: string;
    /** Server-confirmed asset payload for targeted same-window/cache patches. */
    asset?: EditableAsset;
    /** Previous composite identity when a type conversion moved the asset. */
    previousRef?: AssetRef;
    /** True only for changes that require a collection/count revalidation. */
    invalidateCollection?: boolean;
    /** True when persisted relationship edges changed and usage counts/order
     *  must be revalidated from the backend-derived usage index. */
    usageChanged?: boolean;
    /** Set only for cross-tab delivery so active feeds can avoid duplicating
     *  the synchronous same-window invalidation. */
    remote?: boolean;
}

const ASSET_LIBRARY_CHANNEL = "enmotion:asset-library-updates";
const ASSET_LIBRARY_CHANNEL_ORIGIN = (
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `asset-library-${Date.now()}-${Math.random()}`
);
let pendingUsageInvalidation = false;
const pendingUsageScopes = new Set<string>();
let pendingQueryInvalidation = false;
const pendingQueryScopes = new Set<string>();

interface AssetLibraryBroadcastEnvelope {
    scope: string;
    origin: string;
    detail: AssetLibraryChangeDetail;
    at: number;
}

function activeWorkspaceScope(): string {
    return getWorkspaceStorageScope() ?? "desktop";
}

function scheduleQueryInvalidation(scope = activeWorkspaceScope()): void {
    pendingQueryScopes.add(scope);
    if (pendingQueryInvalidation) return;
    pendingQueryInvalidation = true;
    // The second microtask lets an API wrapper's usage signal and its calling
    // view's collection signal settle into one query refresh.
    queueMicrotask(() => {
        queueMicrotask(() => {
            pendingQueryInvalidation = false;
            const scopes = [...pendingQueryScopes];
            pendingQueryScopes.clear();
            for (const scope of scopes) invalidateAssetLibraryQueries(scope);
        });
    });
}

function dispatchAssetChange(detail: AssetLibraryChangeDetail): void {
    window.dispatchEvent(
        new CustomEvent<AssetLibraryChangeDetail>(ASSET_LIBRARY_CHANGED_EVENT, {
            detail,
        }),
    );
}

function broadcastAssetChange(
    detail: AssetLibraryChangeDetail,
    scope = activeWorkspaceScope(),
): void {
    const envelope: AssetLibraryBroadcastEnvelope = {
        scope,
        origin: ASSET_LIBRARY_CHANNEL_ORIGIN,
        detail,
        at: Date.now(),
    };
    try {
        const channel = new BroadcastChannel(ASSET_LIBRARY_CHANNEL);
        channel.postMessage(envelope);
        channel.close();
    } catch {
        // BroadcastChannel is optional (older Safari/private contexts).
        try {
            localStorage.setItem(
                ASSET_LIBRARY_CHANNEL,
                JSON.stringify(envelope),
            );
        } catch {
            // Same-window delivery remains authoritative.
        }
    }
}

/** Notify mounted library views that their canonical backend data changed. */
export function notifyAssetLibraryChanged(detail: AssetLibraryChangeDetail = {}): void {
    if (typeof window === "undefined") return;
    // The compact Home-library feed has its own coalescing query controller.
    // Invalidate it in the same synchronous mutation signal used by the
    // richer editor views, without forcing callers to understand either
    // cache implementation.
    const scope = activeWorkspaceScope();
    const normalized = { ...detail, invalidateCollection: true };
    scheduleQueryInvalidation(scope);
    dispatchAssetChange(normalized);
    broadcastAssetChange(normalized, scope);
}

/**
 * Coalesce persisted relationship mutations into one usage-feed revalidation.
 * Counts are never adjusted optimistically: the rebuilt backend snapshot is
 * authoritative for owner shadowing, lineage, and cross-project references.
 */
export function notifyAssetUsageChanged(): void {
    if (typeof window === "undefined") return;
    pendingUsageScopes.add(activeWorkspaceScope());
    if (pendingUsageInvalidation) return;
    pendingUsageInvalidation = true;
    queueMicrotask(() => {
        pendingUsageInvalidation = false;
        const scopes = [...pendingUsageScopes];
        pendingUsageScopes.clear();
        for (const scope of scopes) {
            const detail: AssetLibraryChangeDetail = {
                invalidateCollection: true,
                usageChanged: true,
            };
            scheduleQueryInvalidation(scope);
            // Do not deliver an old workspace's queued mutation into a newly
            // authenticated same-window view.
            if (scope === activeWorkspaceScope()) dispatchAssetChange(detail);
            broadcastAssetChange(detail, scope);
        }
    });
}

/**
 * Publish one exact server-confirmed mutation without evicting the whole
 * library feed. Other tabs receive the same composite-keyed update through
 * BroadcastChannel and can revalidate only the affected owner if needed.
 */
export function publishAssetMutation({
    ref,
    previousRef,
    asset,
}: {
    ref: AssetRef;
    previousRef?: AssetRef;
    asset: EditableAsset;
}): void {
    if (typeof window === "undefined") return;
    const scope = activeWorkspaceScope();
    const detail: AssetLibraryChangeDetail = {
        source: ref.ownerKind,
        ...(ref.ownerKind === "project" || ref.projectId
            ? { projectId: ref.ownerKind === "project" ? ref.ownerId : ref.projectId }
            : {}),
        ...(ref.ownerKind === "series" || ref.seriesId
            ? { seriesId: ref.ownerKind === "series" ? ref.ownerId : ref.seriesId }
            : {}),
        assetType: ref.assetType,
        assetId: ref.assetId,
        ...(previousRef ? { previousRef } : {}),
        asset,
        invalidateCollection: true,
        ...(previousRef ? { usageChanged: true } : {}),
    };
    scheduleQueryInvalidation(scope);
    dispatchAssetChange(detail);
    broadcastAssetChange(detail, scope);
}

interface ProjectAssetOwner {
    id: string;
    series_id?: string | null;
}

interface ResolvedAssetOwner {
    id: string;
    source?: "episode" | "series" | "global";
    source_id?: string | null;
    series_id?: string | null;
}

/** Invalidate every view that resolves the mutated project asset owner. */
export function notifyProjectAssetChanged(
    project: ProjectAssetOwner,
    asset: ResolvedAssetOwner,
    assetType: "character" | "scene" | "prop",
): void {
    if (asset.source === "global") {
        notifyAssetLibraryChanged({
            source: "global",
            assetType,
            assetId: asset.id,
        });
        return;
    }
    if (asset.source === "series") {
        notifyAssetLibraryChanged({
            source: "series",
            seriesId: asset.series_id ?? asset.source_id ?? project.series_id ?? undefined,
            assetType,
            assetId: asset.id,
        });
        return;
    }
    notifyAssetLibraryChanged({
        source: "project",
        projectId: project.id,
        seriesId: project.series_id ?? undefined,
        assetType,
        assetId: asset.id,
    });
}

/** Subscribe to shared-asset invalidation without coupling pages to Zustand. */
export function subscribeToAssetLibraryChanges(
    listener: (detail: AssetLibraryChangeDetail) => void,
): () => void {
    if (typeof window === "undefined") return () => undefined;
    const handler = (event: Event) => {
        listener((event as CustomEvent<AssetLibraryChangeDetail>).detail ?? {});
    };
    window.addEventListener(ASSET_LIBRARY_CHANGED_EVENT, handler);
    let channel: BroadcastChannel | null = null;
    const storageHandler = (event: StorageEvent) => {
        if (event.key !== ASSET_LIBRARY_CHANNEL || !event.newValue) return;
        try {
            const parsed = JSON.parse(event.newValue) as Partial<AssetLibraryBroadcastEnvelope>;
            if (
                parsed.detail
                && parsed.scope === activeWorkspaceScope()
                && parsed.origin !== ASSET_LIBRARY_CHANNEL_ORIGIN
            ) {
                listener({ ...parsed.detail, remote: true });
            }
        } catch {
            // Ignore malformed/legacy storage notifications.
        }
    };
    try {
        channel = new BroadcastChannel(ASSET_LIBRARY_CHANNEL);
        channel.addEventListener("message", (event: MessageEvent<AssetLibraryBroadcastEnvelope>) => {
            const envelope = event.data;
            if (
                envelope?.detail
                && envelope.scope === activeWorkspaceScope()
                && envelope.origin !== ASSET_LIBRARY_CHANNEL_ORIGIN
            ) {
                listener({ ...envelope.detail, remote: true });
            }
        });
    } catch {
        window.addEventListener("storage", storageHandler);
    }
    return () => {
        window.removeEventListener(ASSET_LIBRARY_CHANGED_EVENT, handler);
        window.removeEventListener("storage", storageHandler);
        channel?.close();
    };
}
