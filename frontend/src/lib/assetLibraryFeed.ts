import axiosLibrary, { type AxiosResponse } from "axios";
import { API_URL } from "@/lib/apiUrl";
import { apiClient } from "@/lib/httpClient";

export const ASSET_LIBRARY_FEED_SCHEMA_VERSION = 3 as const;
export const ASSET_LIBRARY_PAGE_SIZE = 50;
export const ASSET_LIBRARY_METADATA_TIMEOUT_MS = 15_000;

export type AssetLibraryAssetType = "character" | "scene" | "prop";
export type AssetLibrarySourceKind = "series" | "project" | "global";
export type AssetLibrarySort = "default" | "name" | "recent" | "usage";
export type AssetLibraryOrder = "asc" | "desc";

export interface AssetLibraryThumbnail {
  id: string;
  url: string;
  created_at: number;
  media_id: string;
  revision: string;
  width: number | null;
  height: number | null;
  aspect_ratio: number | null;
  mime_type: string | null;
  byte_size: number | null;
  state: "ready" | "pending" | "failed" | "unavailable";
  derivatives: AssetLibraryDerivative[];
  failure_code: string | null;
}

export interface AssetLibraryDerivative {
  url: string;
  width: number;
  height: number;
  mime_type: "image/webp";
  byte_size: number;
}

export interface AssetLibraryFeedItem {
  id: string;
  name: string;
  description: string;
  asset_type: AssetLibraryAssetType;
  source_kind: AssetLibrarySourceKind;
  source_id: string;
  source_name: string;
  series_id: string | null;
  episode_id: string | null;
  starred: boolean;
  thumbnail: AssetLibraryThumbnail | null;
  variant_count: number;
  updated_at: number;
  usage_count: number;
}

export interface AssetLibraryFacets {
  all: number;
  characters: number;
  scenes: number;
  props: number;
  starred: number;
}

export interface AssetLibraryPageInfo {
  offset: number;
  limit: number;
  count: number;
  total: number;
  has_more: boolean;
  next_offset: number | null;
}

export interface AssetLibraryFeedResponse {
  schema_version: typeof ASSET_LIBRARY_FEED_SCHEMA_VERSION;
  revision: number;
  generated_at: number;
  items: AssetLibraryFeedItem[];
  facets: AssetLibraryFacets;
  page: AssetLibraryPageInfo;
}

export interface AssetLibraryQuery {
  assetType?: AssetLibraryAssetType;
  sourceKind?: AssetLibrarySourceKind;
  projectId?: string;
  seriesId?: string;
  starred?: boolean;
  search?: string;
  sort?: AssetLibrarySort;
  order?: AssetLibraryOrder;
  offset?: number;
  limit?: number;
}

export type AssetLibraryFailureKind =
  | "aborted"
  | "authentication"
  | "permission"
  | "network"
  | "timeout"
  | "rate-limit"
  | "server"
  | "invalid-response"
  | "workspace"
  | "synchronization";

export interface AssetLibraryErrorDiagnostics {
  code: string;
  status: number | null;
  requestId: string;
  clientAttemptId: string;
  attemptedAt: string;
  retryable: boolean;
}

export class AssetLibraryRequestError extends Error {
  readonly kind: AssetLibraryFailureKind;
  readonly diagnostics: AssetLibraryErrorDiagnostics;
  readonly retryAfterMs: number | null;

  constructor(
    message: string,
    options: {
      kind: AssetLibraryFailureKind;
      code: string;
      status?: number | null;
      requestId: string;
      clientAttemptId: string;
      retryable?: boolean;
      retryAfterMs?: number | null;
    },
  ) {
    super(message);
    this.name = "AssetLibraryRequestError";
    this.kind = options.kind;
    this.diagnostics = {
      code: options.code,
      status: options.status ?? null,
      requestId: options.requestId,
      clientAttemptId: options.clientAttemptId,
      attemptedAt: new Date().toISOString(),
      retryable: options.retryable ?? false,
    };
    this.retryAfterMs = options.retryAfterMs ?? null;
  }
}

export interface AssetLibraryValidatedCache {
  etag: string | null;
  data: AssetLibraryFeedResponse;
}

export interface AssetLibraryPageResult {
  data: AssetLibraryFeedResponse;
  etag: string | null;
  requestId: string;
  clientAttemptId: string;
  notModified: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 0;
}

function exactKeys(record: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(record).sort();
  const wanted = [...expected].sort();
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index]);
}

function parseThumbnail(value: unknown): AssetLibraryThumbnail | null {
  if (value === null) return null;
  const keys = [
    "id",
    "url",
    "created_at",
    "media_id",
    "revision",
    "width",
    "height",
    "aspect_ratio",
    "mime_type",
    "byte_size",
    "state",
    "derivatives",
    "failure_code",
  ] as const;
  if (
    !isRecord(value)
    || !exactKeys(value, keys)
    || !isNonEmptyString(value.id)
    || !isNonEmptyString(value.url)
    || !isFiniteNumber(value.created_at)
    || value.created_at < 0
    || !isNonEmptyString(value.media_id)
    || !/^[0-9a-f]{64}$/.test(value.media_id)
    || !isNonEmptyString(value.revision)
    || !/^[0-9a-f]{64}$/.test(value.revision)
    || (value.width !== null && (!isNonNegativeInteger(value.width) || value.width < 1))
    || (value.height !== null && (!isNonNegativeInteger(value.height) || value.height < 1))
    || ((value.width === null) !== (value.height === null))
    || (
      value.aspect_ratio !== null
      && (!isFiniteNumber(value.aspect_ratio) || value.aspect_ratio <= 0)
    )
    || (
      value.width !== null
      && value.height !== null
      && value.aspect_ratio === null
    )
    || (value.mime_type !== null && !isNonEmptyString(value.mime_type))
    || (value.byte_size !== null && (!isNonNegativeInteger(value.byte_size) || value.byte_size < 1))
    || !["ready", "pending", "failed", "unavailable"].includes(String(value.state))
    || !Array.isArray(value.derivatives)
    || (value.failure_code !== null && !isNonEmptyString(value.failure_code))
  ) {
    throw new Error("缩略图数据无效");
  }
  const derivatives = value.derivatives.map((candidate) => {
    if (
      !isRecord(candidate)
      || !exactKeys(candidate, ["url", "width", "height", "mime_type", "byte_size"])
      || !isNonEmptyString(candidate.url)
      || !isNonNegativeInteger(candidate.width)
      || candidate.width < 1
      || !isNonNegativeInteger(candidate.height)
      || candidate.height < 1
      || candidate.mime_type !== "image/webp"
      || !isNonNegativeInteger(candidate.byte_size)
      || candidate.byte_size < 1
    ) {
      throw new Error("缩略图衍生数据无效");
    }
    return {
      url: candidate.url,
      width: candidate.width,
      height: candidate.height,
      mime_type: candidate.mime_type,
      byte_size: candidate.byte_size,
    } satisfies AssetLibraryDerivative;
  });
  if (
    derivatives.some((candidate, index) => (
      index > 0 && candidate.width <= derivatives[index - 1].width
    ))
    || (value.state === "ready" && derivatives.length === 0)
    || (value.state !== "ready" && derivatives.length > 0)
  ) {
    throw new Error("缩略图衍生状态无效");
  }
  return {
    id: value.id,
    url: value.url,
    created_at: value.created_at,
    media_id: value.media_id,
    revision: value.revision,
    width: value.width,
    height: value.height,
    aspect_ratio: value.aspect_ratio,
    mime_type: value.mime_type,
    byte_size: value.byte_size,
    state: value.state as AssetLibraryThumbnail["state"],
    derivatives,
    failure_code: value.failure_code,
  };
}

function parseItem(value: unknown): AssetLibraryFeedItem {
  const keys = [
    "id",
    "name",
    "description",
    "asset_type",
    "source_kind",
    "source_id",
    "source_name",
    "series_id",
    "episode_id",
    "starred",
    "thumbnail",
    "variant_count",
    "updated_at",
    "usage_count",
  ] as const;
  if (!isRecord(value) || !exactKeys(value, keys)) throw new Error("资产条目无效");
  if (
    !isNonEmptyString(value.id)
    || !isNonEmptyString(value.name)
    || typeof value.description !== "string"
    || !["character", "scene", "prop"].includes(String(value.asset_type))
    || !["series", "project", "global"].includes(String(value.source_kind))
    || !isNonEmptyString(value.source_id)
    || !isNonEmptyString(value.source_name)
    || (value.series_id !== null && typeof value.series_id !== "string")
    || (value.episode_id !== null && typeof value.episode_id !== "string")
    || typeof value.starred !== "boolean"
    || !isNonNegativeInteger(value.variant_count)
    || !isFiniteNumber(value.updated_at)
    || value.updated_at < 0
    || !isNonNegativeInteger(value.usage_count)
  ) {
    throw new Error("资产条目无效");
  }
  return {
    id: value.id,
    name: value.name,
    description: value.description,
    asset_type: value.asset_type as AssetLibraryAssetType,
    source_kind: value.source_kind as AssetLibrarySourceKind,
    source_id: value.source_id,
    source_name: value.source_name,
    series_id: value.series_id,
    episode_id: value.episode_id,
    starred: value.starred,
    thumbnail: parseThumbnail(value.thumbnail),
    variant_count: value.variant_count,
    updated_at: value.updated_at,
    usage_count: value.usage_count,
  };
}

function parseFacets(value: unknown): AssetLibraryFacets {
  if (
    !isRecord(value)
    || !exactKeys(value, ["all", "characters", "scenes", "props", "starred"])
    || !isNonNegativeInteger(value.all)
    || !isNonNegativeInteger(value.characters)
    || !isNonNegativeInteger(value.scenes)
    || !isNonNegativeInteger(value.props)
    || !isNonNegativeInteger(value.starred)
    || value.characters + value.scenes + value.props !== value.all
    || value.starred > value.all
  ) {
    throw new Error("筛选统计无效");
  }
  return {
    all: value.all,
    characters: value.characters,
    scenes: value.scenes,
    props: value.props,
    starred: value.starred,
  };
}

function parsePage(value: unknown, itemCount: number): AssetLibraryPageInfo {
  if (
    !isRecord(value)
    || !exactKeys(value, ["offset", "limit", "count", "total", "has_more", "next_offset"])
    || !isNonNegativeInteger(value.offset)
    || !isNonNegativeInteger(value.limit)
    || value.limit < 1
    || value.limit > ASSET_LIBRARY_PAGE_SIZE
    || !isNonNegativeInteger(value.count)
    || !isNonNegativeInteger(value.total)
    || typeof value.has_more !== "boolean"
    || (value.next_offset !== null && !isNonNegativeInteger(value.next_offset))
    || value.count !== itemCount
    || value.count > value.limit
    || value.offset > value.total
    || value.offset + value.count > value.total
  ) {
    throw new Error("分页数据无效");
  }
  const expectedNext = value.offset + value.count;
  if (
    value.has_more !== (expectedNext < value.total)
    || (value.has_more && value.next_offset !== expectedNext)
    || (!value.has_more && value.next_offset !== null)
  ) {
    throw new Error("分页数据无效");
  }
  return {
    offset: value.offset,
    limit: value.limit,
    count: value.count,
    total: value.total,
    has_more: value.has_more,
    next_offset: value.next_offset,
  };
}

/**
 * Validate the entire public feed at runtime.
 *
 * This intentionally rejects permissive values such as `{}`, `[]`, partial
 * records, duplicate canonical identities, and inconsistent pagination. A
 * TypeScript cast must never turn a proxy or serialization failure into an
 * authoritative empty library.
 */
export function parseAssetLibraryFeed(value: unknown): AssetLibraryFeedResponse {
  if (
    !isRecord(value)
    || !exactKeys(value, ["schema_version", "revision", "generated_at", "items", "facets", "page"])
    || value.schema_version !== ASSET_LIBRARY_FEED_SCHEMA_VERSION
    || !isNonNegativeInteger(value.revision)
    || !isFiniteNumber(value.generated_at)
    || value.generated_at < 0
    || !Array.isArray(value.items)
  ) {
    throw new Error("资产库数据无效");
  }
  const items = value.items.map(parseItem);
  const identities = new Set<string>();
  for (const item of items) {
    const identity = `${item.source_kind}:${item.source_id}:${item.asset_type}:${item.id}`;
    if (identities.has(identity)) throw new Error("资产条目标识重复");
    identities.add(identity);
  }
  return {
    schema_version: ASSET_LIBRARY_FEED_SCHEMA_VERSION,
    revision: value.revision,
    generated_at: value.generated_at,
    items,
    facets: parseFacets(value.facets),
    page: parsePage(value.page, items.length),
  };
}

export function normalizeAssetLibraryQuery(query: AssetLibraryQuery): Required<
  Pick<AssetLibraryQuery, "starred" | "search" | "sort" | "order" | "offset" | "limit">
> & Pick<AssetLibraryQuery, "assetType" | "sourceKind" | "projectId" | "seriesId"> {
  const sort = query.sort ?? "default";
  return {
    assetType: query.assetType,
    sourceKind: query.sourceKind,
    projectId: String(query.projectId ?? "").trim().slice(0, 128) || undefined,
    seriesId: String(query.seriesId ?? "").trim().slice(0, 128) || undefined,
    starred: Boolean(query.starred),
    search: String(query.search ?? "").trim().slice(0, 500),
    sort,
    order: query.order ?? (sort === "usage" || sort === "recent" ? "desc" : "asc"),
    offset: Math.max(0, Math.trunc(query.offset ?? 0)),
    limit: Math.min(
      ASSET_LIBRARY_PAGE_SIZE,
      Math.max(1, Math.trunc(query.limit ?? ASSET_LIBRARY_PAGE_SIZE)),
    ),
  };
}

export function assetLibraryQueryKey(workspaceKey: string, query: AssetLibraryQuery): string {
  const normalized = normalizeAssetLibraryQuery(query);
  return JSON.stringify({
    workspace: workspaceKey,
    asset_type: normalized.assetType ?? null,
    source_kind: normalized.sourceKind ?? null,
    project_id: normalized.projectId ?? null,
    series_id: normalized.seriesId ?? null,
    starred: normalized.starred,
    q: normalized.search,
    sort: normalized.sort,
    order: normalized.order,
    offset: normalized.offset,
    limit: normalized.limit,
  });
}

function safeIdentifier(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
  }
}

function header(response: AxiosResponse, name: string): string {
  const value = response.headers[name.toLowerCase()];
  return typeof value === "string" ? value.trim() : "";
}

function retryAfterMilliseconds(value: unknown): number | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.min(seconds * 1000, 10_000);
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return null;
  return Math.min(Math.max(0, timestamp - Date.now()), 10_000);
}

function serverErrorEnvelope(data: unknown): {
  code: string;
  message: string;
  requestId: string;
  retryable: boolean;
} | null {
  if (!isRecord(data) || !isRecord(data.error)) return null;
  const error = data.error;
  if (
    !isNonEmptyString(error.code)
    || !isNonEmptyString(error.message)
    || !isNonEmptyString(error.request_id)
    || typeof error.retryable !== "boolean"
  ) {
    return null;
  }
  return {
    code: error.code,
    message: error.message,
    requestId: error.request_id,
    retryable: error.retryable,
  };
}

function requestError(
  error: unknown,
  {
    clientAttemptId,
    fallbackRequestId,
  }: {
    clientAttemptId: string;
    fallbackRequestId: string;
  },
): AssetLibraryRequestError {
  if (error instanceof AssetLibraryRequestError) return error;
  if (axiosLibrary.isCancel(error) || (error instanceof DOMException && error.name === "AbortError")) {
    return new AssetLibraryRequestError("资产库请求已取消", {
      kind: "aborted",
      code: "ASSET_LIBRARY_REQUEST_ABORTED",
      requestId: fallbackRequestId,
      clientAttemptId,
    });
  }
  if (axiosLibrary.isAxiosError(error)) {
    const status = error.response?.status ?? null;
    const responseRequestId = String(error.response?.headers?.["x-request-id"] ?? "").trim();
    const envelope = serverErrorEnvelope(error.response?.data);
    const requestId = envelope?.requestId || responseRequestId || fallbackRequestId;
    const retryAfterMs = retryAfterMilliseconds(error.response?.headers?.["retry-after"]);
    if (error.code === "ECONNABORTED" || error.code === "ETIMEDOUT") {
      return new AssetLibraryRequestError("资产库请求超时", {
        kind: "timeout",
        code: "ASSET_LIBRARY_TIMEOUT",
        status,
        requestId,
        clientAttemptId,
        retryable: true,
        retryAfterMs,
      });
    }
    if (!error.response) {
      return new AssetLibraryRequestError("资产库网络请求失败", {
        kind: "network",
        code: "ASSET_LIBRARY_NETWORK_UNAVAILABLE",
        requestId,
        clientAttemptId,
        retryable: true,
      });
    }
    if (status === 401) {
      return new AssetLibraryRequestError("需要登录后继续", {
        kind: "authentication",
        code: envelope?.code || "AUTHENTICATION_REQUIRED",
        status,
        requestId,
        clientAttemptId,
      });
    }
    if (status === 403) {
      return new AssetLibraryRequestError("没有执行该操作的权限", {
        kind: "permission",
        code: envelope?.code || "PERMISSION_DENIED",
        status,
        requestId,
        clientAttemptId,
      });
    }
    if (status === 408) {
      return new AssetLibraryRequestError("资产库请求超时", {
        kind: "timeout",
        code: envelope?.code || "ASSET_LIBRARY_TIMEOUT",
        status,
        requestId,
        clientAttemptId,
        retryable: true,
        retryAfterMs,
      });
    }
    if (status === 429) {
      return new AssetLibraryRequestError("资产库请求过于频繁", {
        kind: "rate-limit",
        code: envelope?.code || "ASSET_LIBRARY_RATE_LIMITED",
        status,
        requestId,
        clientAttemptId,
        retryable: true,
        retryAfterMs,
      });
    }
    const transientGateway = status === 502 || status === 503 || status === 504;
    const retryableServer = transientGateway || (status === 500 && envelope?.retryable === true);
    return new AssetLibraryRequestError("资产库请求失败", {
      kind: status !== null && status >= 500 ? "server" : "synchronization",
      code: envelope?.code || (status !== null && status >= 500
        ? "ASSET_LIBRARY_SERVER_UNAVAILABLE"
        : "ASSET_LIBRARY_SYNCHRONIZATION_FAILED"),
      status,
      requestId,
      clientAttemptId,
      retryable: retryableServer,
      retryAfterMs,
    });
  }
  return new AssetLibraryRequestError("资产库请求失败", {
    kind: "synchronization",
    code: "ASSET_LIBRARY_SYNCHRONIZATION_FAILED",
    requestId: fallbackRequestId,
    clientAttemptId,
  });
}

export interface FetchAssetLibraryPageOptions {
  workspaceKey: string;
  query: AssetLibraryQuery;
  cache?: AssetLibraryValidatedCache | null;
  signal?: AbortSignal;
  logicalRequestId?: string;
  attempt?: number;
}

/** Fetch and strictly validate one metadata page. */
export async function fetchAssetLibraryPage({
  workspaceKey,
  query,
  cache,
  signal,
  logicalRequestId = safeIdentifier(),
  attempt = 0,
}: FetchAssetLibraryPageOptions): Promise<AssetLibraryPageResult> {
  const normalized = normalizeAssetLibraryQuery(query);
  const clientAttemptId = `${logicalRequestId}.${attempt + 1}.${safeIdentifier()}`;
  const fallbackRequestId = `client-${clientAttemptId}`;
  try {
    const response = await apiClient.get(`${API_URL}/library/feed/v3`, {
      params: {
        ...(normalized.assetType ? { asset_type: normalized.assetType } : {}),
        ...(normalized.sourceKind ? { source_kind: normalized.sourceKind } : {}),
        ...(normalized.projectId ? { project_id: normalized.projectId } : {}),
        ...(normalized.seriesId ? { series_id: normalized.seriesId } : {}),
        starred: normalized.starred,
        q: normalized.search,
        sort: normalized.sort,
        order: normalized.order,
        offset: normalized.offset,
        limit: normalized.limit,
      },
      headers: {
        "X-EnMotion-Client-Request-ID": logicalRequestId,
        "X-EnMotion-Client-Attempt-ID": clientAttemptId,
        ...(cache?.etag ? { "If-None-Match": cache.etag } : {}),
      },
      timeout: ASSET_LIBRARY_METADATA_TIMEOUT_MS,
      signal,
      validateStatus: (status) => status === 200 || status === 304,
    });
    const requestId = header(response, "x-request-id") || fallbackRequestId;
    const responseWorkspace = header(response, "x-enmotion-workspace-id");
    if (workspaceKey !== "desktop" && responseWorkspace !== workspaceKey) {
      throw new AssetLibraryRequestError("资产库响应属于其他工作区", {
        kind: "workspace",
        code: "ASSET_LIBRARY_WORKSPACE_MISMATCH",
        status: response.status,
        requestId,
        clientAttemptId,
      });
    }
    if (response.status === 304) {
      if (!cache) {
        throw new AssetLibraryRequestError("条件响应没有匹配的有效缓存", {
          kind: "invalid-response",
          code: "ASSET_LIBRARY_INVALID_304",
          status: 304,
          requestId,
          clientAttemptId,
        });
      }
      return {
        data: cache.data,
        etag: cache.etag,
        requestId,
        clientAttemptId,
        notModified: true,
      };
    }
    const contentType = header(response, "content-type").toLowerCase();
    if (!contentType.startsWith("application/json")) {
      throw new AssetLibraryRequestError("服务器返回的资产库响应无效", {
        kind: "invalid-response",
        code: "ASSET_LIBRARY_INVALID_CONTENT_TYPE",
        status: response.status,
        requestId,
        clientAttemptId,
      });
    }
    let data: AssetLibraryFeedResponse;
    try {
      data = parseAssetLibraryFeed(response.data);
    } catch {
      throw new AssetLibraryRequestError("服务器返回的资产库响应无效", {
        kind: "invalid-response",
        code: "ASSET_LIBRARY_INVALID_RESPONSE_SCHEMA",
        status: response.status,
        requestId,
        clientAttemptId,
      });
    }
    return {
      data,
      etag: header(response, "etag") || null,
      requestId,
      clientAttemptId,
      notModified: false,
    };
  } catch (error) {
    throw requestError(error, { clientAttemptId, fallbackRequestId });
  }
}

export function isAssetLibraryAbort(error: unknown): boolean {
  return error instanceof AssetLibraryRequestError && error.kind === "aborted";
}

export function isAssetLibraryRetryable(error: unknown): error is AssetLibraryRequestError {
  return error instanceof AssetLibraryRequestError && error.diagnostics.retryable;
}

export function createAssetLibraryLogicalRequestId(): string {
  return safeIdentifier();
}
