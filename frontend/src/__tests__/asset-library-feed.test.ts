import { beforeEach, describe, expect, it, vi } from "vitest";

const httpMocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("@/lib/httpClient", () => ({
  apiClient: { get: (...args: unknown[]) => httpMocks.get(...args) },
}));
vi.mock("@/lib/apiUrl", () => ({ API_URL: "https://studio.example/api" }));

import {
  AssetLibraryRequestError,
  assetLibraryQueryKey,
  fetchAssetLibraryPage,
  normalizeAssetLibraryQuery,
  parseAssetLibraryFeed,
  type AssetLibraryFeedResponse,
} from "@/lib/assetLibraryFeed";

function feed(): AssetLibraryFeedResponse {
  const revision = "a".repeat(64);
  return {
    schema_version: 3,
    revision: 7,
    generated_at: 100,
    items: [{
      id: "asset-1",
      name: "Hero",
      description: "A hero",
      asset_type: "character",
      source_kind: "series",
      source_id: "series-1",
      source_name: "Series",
      series_id: "series-1",
      episode_id: null,
      starred: false,
      thumbnail: {
        id: "image-1",
        url: "assets/image-1.png",
        created_at: 99,
        media_id: revision,
        revision,
        width: 1024,
        height: 1024,
        aspect_ratio: 1,
        mime_type: "image/png",
        byte_size: 750_000,
        state: "ready",
        derivatives: [{
          url: "derivatives/images/image-1/w384.webp",
          width: 384,
          height: 384,
          mime_type: "image/webp",
          byte_size: 24_000,
        }],
        failure_code: null,
      },
      variant_count: 1,
      updated_at: 99,
      usage_count: 3,
    }],
    facets: {
      all: 1,
      characters: 1,
      scenes: 0,
      props: 0,
      starred: 0,
    },
    page: {
      offset: 0,
      limit: 50,
      count: 1,
      total: 1,
      has_more: false,
      next_offset: null,
    },
  };
}

function response(
  data: unknown,
  {
    status = 200,
    workspace = "workspace-1",
    contentType = "application/json; charset=utf-8",
  } = {},
) {
  return {
    data,
    status,
    headers: {
      "content-type": contentType,
      "x-request-id": "server-request-1",
      "x-enmotion-workspace-id": workspace,
      etag: "\"feed-7\"",
    },
  };
}

describe("Asset Library strict feed", () => {
  beforeEach(() => vi.clearAllMocks());

  it("accepts only the complete versioned schema", () => {
    expect(parseAssetLibraryFeed(feed())).toEqual(feed());

    const invalidValues: unknown[] = [
      null,
      [],
      {},
      { ...feed(), schema_version: 1 },
      { ...feed(), unexpected: true },
      { ...feed(), facets: { ...feed().facets, all: 0 } },
      { ...feed(), page: { ...feed().page, count: 0 } },
      { ...feed(), page: { ...feed().page, has_more: true, next_offset: null } },
      { ...feed(), items: [{ ...feed().items[0], name: "" }] },
      { ...feed(), items: [{
        ...feed().items[0],
        thumbnail: {
          ...feed().items[0].thumbnail,
          state: "ready",
          derivatives: [],
        },
      }] },
      { ...feed(), items: [feed().items[0], feed().items[0]], page: {
        ...feed().page,
        count: 2,
        total: 2,
      }, facets: { ...feed().facets, all: 2, characters: 2 } },
    ];
    for (const value of invalidValues) {
      expect(() => parseAssetLibraryFeed(value)).toThrow();
    }
  });

  it("normalizes bounded queries into stable workspace keys", () => {
    expect(normalizeAssetLibraryQuery({
      search: `  ${"x".repeat(600)}  `,
      offset: -10,
      limit: 500,
    })).toEqual({
      assetType: undefined,
      sourceKind: undefined,
      projectId: undefined,
      seriesId: undefined,
      starred: false,
      search: "x".repeat(500),
      sort: "default",
      order: "asc",
      offset: 0,
      limit: 50,
    });
    expect(assetLibraryQueryKey("workspace-1", { search: " Hero " }))
      .toBe(assetLibraryQueryKey("workspace-1", { search: "Hero" }));
    expect(assetLibraryQueryKey("workspace-1", {}))
      .not.toBe(assetLibraryQueryKey("workspace-2", {}));
  });

  it("defaults usage sorting to descending and preserves explicit order in keys", () => {
    expect(normalizeAssetLibraryQuery({ sort: "usage" })).toMatchObject({
      sort: "usage",
      order: "desc",
    });
    expect(normalizeAssetLibraryQuery({ sort: "usage", order: "asc" })).toMatchObject({
      sort: "usage",
      order: "asc",
    });
    expect(assetLibraryQueryKey("workspace-1", { sort: "usage", order: "asc" }))
      .not.toBe(assetLibraryQueryKey("workspace-1", { sort: "usage", order: "desc" }));
  });

  it("fetches, validates, and correlates one workspace page", async () => {
    httpMocks.get.mockResolvedValue(response(feed()));

    const result = await fetchAssetLibraryPage({
      workspaceKey: "workspace-1",
      query: { assetType: "character" },
      logicalRequestId: "logical-1",
    });

    expect(result.data).toEqual(feed());
    expect(result.requestId).toBe("server-request-1");
    expect(result.etag).toBe("\"feed-7\"");
    expect(result.notModified).toBe(false);
    expect(httpMocks.get).toHaveBeenCalledWith(
      "https://studio.example/api/library/feed/v3",
      expect.objectContaining({
        timeout: 15_000,
        params: expect.objectContaining({
          asset_type: "character",
          offset: 0,
          limit: 50,
        }),
        headers: expect.objectContaining({
          "X-EnMotion-Client-Request-ID": "logical-1",
        }),
      }),
    );
  });

  it("sends usage sort and order to the server unchanged", async () => {
    httpMocks.get.mockResolvedValue(response(feed()));

    await fetchAssetLibraryPage({
      workspaceKey: "workspace-1",
      query: { sort: "usage", order: "asc" },
      logicalRequestId: "usage-sort",
    });

    expect(httpMocks.get).toHaveBeenCalledWith(
      "https://studio.example/api/library/feed/v3",
      expect.objectContaining({
        params: expect.objectContaining({
          sort: "usage",
          order: "asc",
          offset: 0,
          limit: 50,
        }),
      }),
    );
  });

  it("sends owner, favorite, search, and pagination filters with usage order", async () => {
    httpMocks.get.mockResolvedValue(response(feed()));

    await fetchAssetLibraryPage({
      workspaceKey: "workspace-1",
      query: {
        sourceKind: "project",
        projectId: "episode-1",
        seriesId: "series-1",
        assetType: "character",
        starred: true,
        search: "hero",
        sort: "usage",
        order: "desc",
        offset: 50,
        limit: 25,
      },
    });

    expect(httpMocks.get).toHaveBeenCalledWith(
      "https://studio.example/api/library/feed/v3",
      expect.objectContaining({
        params: {
          source_kind: "project",
          project_id: "episode-1",
          series_id: "series-1",
          asset_type: "character",
          starred: true,
          q: "hero",
          sort: "usage",
          order: "desc",
          offset: 50,
          limit: 25,
        },
      }),
    );
  });

  it("reuses only a validated matching cache for 304", async () => {
    const cached = { etag: "\"feed-7\"", data: feed() };
    httpMocks.get.mockResolvedValue(response(null, { status: 304 }));

    const result = await fetchAssetLibraryPage({
      workspaceKey: "workspace-1",
      query: {},
      cache: cached,
    });
    expect(result.data).toBe(cached.data);
    expect(result.notModified).toBe(true);

    await expect(fetchAssetLibraryPage({
      workspaceKey: "workspace-1",
      query: {},
    })).rejects.toMatchObject({
      kind: "invalid-response",
      diagnostics: { code: "ASSET_LIBRARY_INVALID_304" },
    });
  });

  it("rejects mismatched workspaces, content types, and malformed bodies", async () => {
    httpMocks.get
      .mockResolvedValueOnce(response(feed(), { workspace: "workspace-other" }))
      .mockResolvedValueOnce(response(feed(), { contentType: "text/html" }))
      .mockResolvedValueOnce(response({}));

    await expect(fetchAssetLibraryPage({
      workspaceKey: "workspace-1",
      query: {},
    })).rejects.toMatchObject({
      kind: "workspace",
      diagnostics: { code: "ASSET_LIBRARY_WORKSPACE_MISMATCH" },
    });
    await expect(fetchAssetLibraryPage({
      workspaceKey: "workspace-1",
      query: {},
    })).rejects.toMatchObject({
      kind: "invalid-response",
      diagnostics: { code: "ASSET_LIBRARY_INVALID_CONTENT_TYPE" },
    });
    await expect(fetchAssetLibraryPage({
      workspaceKey: "workspace-1",
      query: {},
    })).rejects.toMatchObject({
      kind: "invalid-response",
      diagnostics: { code: "ASSET_LIBRARY_INVALID_RESPONSE_SCHEMA" },
    });
  });

  it("classifies authentication and retryable server envelopes safely", async () => {
    const axiosFailure = (
      status: number,
      data: unknown,
      headers: Record<string, string> = {},
    ) => ({
      isAxiosError: true,
      response: {
        status,
        data,
        headers: { "x-request-id": "header-request", ...headers },
      },
    });
    httpMocks.get
      .mockRejectedValueOnce(axiosFailure(401, { detail: "private" }))
      .mockRejectedValueOnce(axiosFailure(503, {
        error: {
          code: "ASSET_LIBRARY_UNAVAILABLE",
          message: "Temporarily unavailable",
          request_id: "envelope-request",
          retryable: true,
        },
      }, { "retry-after": "2" }));

    const authentication = await fetchAssetLibraryPage({
      workspaceKey: "workspace-1",
      query: {},
    }).catch((error: unknown) => error);
    expect(authentication).toBeInstanceOf(AssetLibraryRequestError);
    expect(authentication).toMatchObject({
      kind: "authentication",
      diagnostics: {
        code: "AUTHENTICATION_REQUIRED",
        requestId: "header-request",
        retryable: false,
      },
    });

    const unavailable = await fetchAssetLibraryPage({
      workspaceKey: "workspace-1",
      query: {},
    }).catch((error: unknown) => error);
    expect(unavailable).toMatchObject({
      kind: "server",
      retryAfterMs: 2_000,
      diagnostics: {
        code: "ASSET_LIBRARY_UNAVAILABLE",
        requestId: "envelope-request",
        retryable: true,
      },
    });
  });
});
