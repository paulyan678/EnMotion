"use client";

import { useCallback, useMemo, useSyncExternalStore } from "react";
import {
  ASSET_LIBRARY_PAGE_SIZE,
  AssetLibraryRequestError,
  assetLibraryQueryKey,
  createAssetLibraryLogicalRequestId,
  fetchAssetLibraryPage,
  isAssetLibraryAbort,
  isAssetLibraryRetryable,
  normalizeAssetLibraryQuery,
  type AssetLibraryFeedResponse,
  type AssetLibraryQuery,
  type AssetLibraryValidatedCache,
} from "@/lib/assetLibraryFeed";

export type AssetLibraryQueryPhase =
  | "initial-loading"
  | "success-with-data"
  | "success-empty"
  | "refreshing-with-stale-data"
  | "initial-error"
  | "refresh-error-with-stale-data";

export interface AssetLibraryQuerySnapshot {
  phase: AssetLibraryQueryPhase;
  data: AssetLibraryFeedResponse | null;
  error: AssetLibraryRequestError | null;
  requestId: string | null;
  isLoadingMore: boolean;
  loadMoreError: AssetLibraryRequestError | null;
}

interface QueryEntry {
  key: string;
  workspaceKey: string;
  query: AssetLibraryQuery;
  snapshot: AssetLibraryQuerySnapshot;
  listeners: Set<() => void>;
  abortController: AbortController | null;
  inFlight: Promise<void> | null;
  loadMoreInFlight: Promise<void> | null;
  pendingRefresh: boolean;
  pageCache: Map<number, AssetLibraryValidatedCache>;
  logicalRequestId: string | null;
  generation: number;
}

const INITIAL_SNAPSHOT: AssetLibraryQuerySnapshot = Object.freeze({
  phase: "initial-loading",
  data: null,
  error: null,
  requestId: null,
  isLoadingMore: false,
  loadMoreError: null,
});

const entries = new Map<string, QueryEntry>();
const retainedWorkspaceData = new Map<string, AssetLibraryFeedResponse>();
const MAX_RETRY_ATTEMPTS = 2;
const BASE_RETRY_DELAY_MS = 300;
const MAX_RETRY_DELAY_MS = 5_000;

function wait(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (milliseconds <= 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("操作已取消", "AbortError"));
      return;
    }
    const timer = window.setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("操作已取消", "AbortError"));
    }, { once: true });
  });
}

/**
 * Full-jitter retry delay. Retry-After takes precedence but is still capped so
 * a bad proxy header cannot freeze the page.
 */
export function assetLibraryRetryDelay(
  retryIndex: number,
  retryAfterMs: number | null,
  random: () => number = Math.random,
): number {
  if (retryAfterMs !== null) return Math.min(MAX_RETRY_DELAY_MS, Math.max(0, retryAfterMs));
  const ceiling = Math.min(MAX_RETRY_DELAY_MS, BASE_RETRY_DELAY_MS * 2 ** Math.max(0, retryIndex));
  return Math.floor(Math.max(0, Math.min(1, random())) * ceiling);
}

function emptySnapshot(): AssetLibraryQuerySnapshot {
  return { ...INITIAL_SNAPSHOT };
}

function initialSnapshotFor(workspaceKey: string): AssetLibraryQuerySnapshot {
  const retained = retainedWorkspaceData.get(workspaceKey);
  if (!retained) return emptySnapshot();
  return {
    ...INITIAL_SNAPSHOT,
    phase: "refreshing-with-stale-data",
    data: retained,
  };
}

function baseQuery(query: AssetLibraryQuery): AssetLibraryQuery {
  const normalized = normalizeAssetLibraryQuery(query);
  return {
    assetType: normalized.assetType,
    sourceKind: normalized.sourceKind,
    projectId: normalized.projectId,
    seriesId: normalized.seriesId,
    starred: normalized.starred,
    search: normalized.search,
    sort: normalized.sort,
    order: normalized.order,
    offset: 0,
    limit: normalized.limit,
  };
}

function getOrCreateEntry(workspaceKey: string, query: AssetLibraryQuery): QueryEntry {
  const normalized = baseQuery(query);
  const key = assetLibraryQueryKey(workspaceKey, normalized);
  let entry = entries.get(key);
  if (!entry) {
    entry = {
      key,
      workspaceKey,
      query: normalized,
      snapshot: initialSnapshotFor(workspaceKey),
      listeners: new Set(),
      abortController: null,
      inFlight: null,
      loadMoreInFlight: null,
      pendingRefresh: false,
      pageCache: new Map(),
      logicalRequestId: null,
      generation: 0,
    };
    entries.set(key, entry);
  }
  return entry;
}

function publish(entry: QueryEntry, patch: Partial<AssetLibraryQuerySnapshot>): void {
  entry.snapshot = { ...entry.snapshot, ...patch };
  for (const listener of entry.listeners) listener();
}

function asRequestError(error: unknown): AssetLibraryRequestError {
  if (error instanceof AssetLibraryRequestError) return error;
  return new AssetLibraryRequestError("资产库同步失败", {
    kind: "synchronization",
    code: "ASSET_LIBRARY_SYNCHRONIZATION_FAILED",
    requestId: `client-${createAssetLibraryLogicalRequestId()}`,
    clientAttemptId: createAssetLibraryLogicalRequestId(),
  });
}

async function fetchWithRetry(
  entry: QueryEntry,
  query: AssetLibraryQuery,
  offset: number,
  signal: AbortSignal,
  logicalRequestId: string,
): Promise<Awaited<ReturnType<typeof fetchAssetLibraryPage>>> {
  let lastError: unknown;
  for (let attempt = 0; attempt <= MAX_RETRY_ATTEMPTS; attempt += 1) {
    try {
      return await fetchAssetLibraryPage({
        workspaceKey: entry.workspaceKey,
        query: { ...query, offset },
        cache: entry.pageCache.get(offset) ?? null,
        signal,
        logicalRequestId,
        attempt,
      });
    } catch (error) {
      lastError = error;
      if (
        attempt >= MAX_RETRY_ATTEMPTS
        || !isAssetLibraryRetryable(error)
        || signal.aborted
      ) {
        throw error;
      }
      await wait(assetLibraryRetryDelay(attempt, error.retryAfterMs), signal);
    }
  }
  throw lastError;
}

function mergePage(
  existing: AssetLibraryFeedResponse,
  page: AssetLibraryFeedResponse,
): AssetLibraryFeedResponse {
  if (existing.revision !== page.revision) {
    throw new AssetLibraryRequestError("加载下一页时资产库内容发生变化", {
      kind: "synchronization",
      code: "ASSET_LIBRARY_REVISION_CHANGED",
      requestId: `client-${createAssetLibraryLogicalRequestId()}`,
      clientAttemptId: createAssetLibraryLogicalRequestId(),
      retryable: true,
    });
  }
  const identities = new Set(
    existing.items.map((item) => (
      `${item.source_kind}:${item.source_id}:${item.asset_type}:${item.id}`
    )),
  );
  const appended = page.items.filter((item) => {
    const identity = `${item.source_kind}:${item.source_id}:${item.asset_type}:${item.id}`;
    if (identities.has(identity)) return false;
    identities.add(identity);
    return true;
  });
  const items = [...existing.items, ...appended];
  return {
    ...page,
    items,
    page: {
      ...page.page,
      offset: 0,
      count: items.length,
    },
  };
}

async function runRefresh(entry: QueryEntry): Promise<void> {
  if (entry.inFlight) {
    entry.pendingRefresh = true;
    return entry.inFlight;
  }
  if (entry.loadMoreInFlight) {
    entry.pendingRefresh = true;
    return entry.loadMoreInFlight;
  }
  entry.pendingRefresh = false;
  entry.generation += 1;
  const generation = entry.generation;
  const staleData = entry.snapshot.data;
  publish(entry, {
    phase: staleData ? "refreshing-with-stale-data" : "initial-loading",
    error: null,
    loadMoreError: null,
  });
  const controller = new AbortController();
  entry.abortController = controller;
  const logicalRequestId = createAssetLibraryLogicalRequestId();
  entry.logicalRequestId = logicalRequestId;
  const work = (async () => {
    try {
      const result = await fetchWithRetry(
        entry,
        entry.query,
        0,
        controller.signal,
        logicalRequestId,
      );
      if (generation !== entry.generation || controller.signal.aborted) return;
      entry.pageCache.set(0, { etag: result.etag, data: result.data });
      retainedWorkspaceData.set(entry.workspaceKey, result.data);
      // A new first page supersedes every old continuation page.
      for (const offset of entry.pageCache.keys()) {
        if (offset !== 0) entry.pageCache.delete(offset);
      }
      publish(entry, {
        phase: result.data.facets.all === 0 ? "success-empty" : "success-with-data",
        data: result.data,
        error: null,
        requestId: result.requestId,
        isLoadingMore: false,
        loadMoreError: null,
      });
    } catch (error) {
      if (isAssetLibraryAbort(error) || controller.signal.aborted) return;
      const requestError = asRequestError(error);
      if (generation !== entry.generation) return;
      publish(entry, {
        phase: staleData ? "refresh-error-with-stale-data" : "initial-error",
        data: staleData,
        error: requestError,
        requestId: requestError.diagnostics.requestId,
        isLoadingMore: false,
      });
    } finally {
      if (entry.abortController === controller) entry.abortController = null;
    }
  })();
  entry.inFlight = work;
  try {
    await work;
  } finally {
    if (entry.inFlight === work) entry.inFlight = null;
    if (entry.pendingRefresh && entry.listeners.size > 0) {
      entry.pendingRefresh = false;
      void runRefresh(entry);
    }
  }
}

async function runLoadMore(entry: QueryEntry): Promise<void> {
  const data = entry.snapshot.data;
  const offset = data?.page.next_offset;
  if (
    !data
    || !data.page.has_more
    || typeof offset !== "number"
    || entry.snapshot.isLoadingMore
    || entry.inFlight
    || entry.loadMoreInFlight
  ) {
    return;
  }
  const controller = new AbortController();
  entry.abortController = controller;
  const logicalRequestId = createAssetLibraryLogicalRequestId();
  const work = (async () => {
    publish(entry, { isLoadingMore: true, loadMoreError: null });
    try {
      const result = await fetchWithRetry(
        entry,
        entry.query,
        offset,
        controller.signal,
        logicalRequestId,
      );
      if (controller.signal.aborted) return;
      entry.pageCache.set(offset, { etag: result.etag, data: result.data });
      let merged: AssetLibraryFeedResponse;
      try {
        merged = mergePage(data, result.data);
      } catch {
        // A mutation committed between pages. Re-fetch from page one rather than
        // mixing revisions or showing an empty/partial false success.
        entry.pendingRefresh = true;
        return;
      }
      publish(entry, {
        phase: "success-with-data",
        data: merged,
        requestId: result.requestId,
        isLoadingMore: false,
        loadMoreError: null,
      });
      retainedWorkspaceData.set(entry.workspaceKey, merged);
    } catch (error) {
      if (isAssetLibraryAbort(error) || controller.signal.aborted) return;
      publish(entry, {
        isLoadingMore: false,
        loadMoreError: asRequestError(error),
      });
    } finally {
      if (entry.abortController === controller) entry.abortController = null;
      publish(entry, { isLoadingMore: false });
    }
  })();
  entry.loadMoreInFlight = work;
  try {
    await work;
  } finally {
    if (entry.loadMoreInFlight === work) entry.loadMoreInFlight = null;
    if (entry.pendingRefresh && entry.listeners.size > 0) {
      entry.pendingRefresh = false;
      void runRefresh(entry);
    }
  }
}

function subscribe(entry: QueryEntry, listener: () => void): () => void {
  const wasInactive = entry.listeners.size === 0;
  entry.listeners.add(listener);
  // Re-entering a cached route paints stale data immediately, then validates
  // it in the background. The single-flight guard prevents duplicate fetches
  // when React re-subscribes during development or concurrent rendering.
  if (wasInactive && !entry.inFlight && !entry.loadMoreInFlight) {
    void runRefresh(entry);
  }
  return () => {
    entry.listeners.delete(listener);
    if (entry.listeners.size === 0) {
      entry.abortController?.abort();
      entry.abortController = null;
      entry.inFlight = null;
      entry.loadMoreInFlight = null;
      entry.pendingRefresh = false;
    }
  };
}

export function invalidateAssetLibraryQueries(workspaceKey?: string): void {
  for (const entry of entries.values()) {
    if (workspaceKey && entry.workspaceKey !== workspaceKey) continue;
    if (entry.listeners.size > 0) void runRefresh(entry);
    else entry.pendingRefresh = true;
  }
}

export function clearAssetLibraryQueryCache(workspaceKey?: string): void {
  for (const [key, entry] of entries) {
    if (workspaceKey && entry.workspaceKey !== workspaceKey) continue;
    entry.generation += 1;
    entry.abortController?.abort();
    entry.listeners.clear();
    entries.delete(key);
  }
  if (workspaceKey) retainedWorkspaceData.delete(workspaceKey);
  else retainedWorkspaceData.clear();
}

/** Test-only reset exported to make cache isolation explicit and deterministic. */
export function resetAssetLibraryQueryControllerForTests(): void {
  clearAssetLibraryQueryCache();
}

export function useAssetLibraryQuery(
  workspaceKey: string,
  query: AssetLibraryQuery,
): {
  snapshot: AssetLibraryQuerySnapshot;
  refresh: () => void;
  loadMore: () => void;
} {
  const normalized = useMemo(
    () => baseQuery(query),
    [
      query.assetType,
      query.sourceKind,
      query.projectId,
      query.seriesId,
      query.starred,
      query.search,
      query.sort,
      query.order,
      query.limit,
    ],
  );
  const entry = useMemo(
    () => getOrCreateEntry(workspaceKey, normalized),
    [workspaceKey, normalized],
  );
  const snapshot = useSyncExternalStore(
    useCallback((listener) => subscribe(entry, listener), [entry]),
    useCallback(() => entry.snapshot, [entry]),
    () => INITIAL_SNAPSHOT,
  );

  return {
    snapshot,
    refresh: useCallback(() => void runRefresh(entry), [entry]),
    loadMore: useCallback(() => void runLoadMore(entry), [entry]),
  };
}
