import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const feedMocks = vi.hoisted(() => ({ fetchPage: vi.fn() }));

vi.mock("@/lib/assetLibraryFeed", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/assetLibraryFeed")>();
  return {
    ...actual,
    fetchAssetLibraryPage: (...args: unknown[]) => feedMocks.fetchPage(...args),
  };
});

import {
  AssetLibraryRequestError,
  type AssetLibraryFeedResponse,
  type AssetLibraryPageResult,
} from "@/lib/assetLibraryFeed";
import {
  assetLibraryRetryDelay,
  invalidateAssetLibraryQueries,
  resetAssetLibraryQueryControllerForTests,
  useAssetLibraryQuery,
} from "@/lib/assetLibraryQuery";

function feed(
  revision = 1,
  names: string[] = ["Hero"],
  {
    offset = 0,
    total = names.length,
    hasMore = false,
  } = {},
): AssetLibraryFeedResponse {
  return {
    schema_version: 3,
    revision,
    generated_at: revision,
    items: names.map((name, index) => ({
      id: `${name}-${offset + index}`,
      name,
      description: "",
      asset_type: "character",
      source_kind: "series",
      source_id: "series-1",
      source_name: "Series",
      series_id: "series-1",
      episode_id: null,
      starred: false,
      thumbnail: null,
      variant_count: 0,
      updated_at: revision,
      usage_count: 0,
    })),
    facets: {
      all: total,
      characters: total,
      scenes: 0,
      props: 0,
      starred: 0,
    },
    page: {
      offset,
      limit: 50,
      count: names.length,
      total,
      has_more: hasMore,
      next_offset: hasMore ? offset + names.length : null,
    },
  };
}

function result(data = feed()): AssetLibraryPageResult {
  return {
    data,
    etag: `"revision-${data.revision}"`,
    requestId: `request-${data.revision}`,
    clientAttemptId: `attempt-${data.revision}`,
    notModified: false,
  };
}

function failure(
  kind: ConstructorParameters<typeof AssetLibraryRequestError>[1]["kind"],
  retryable = false,
) {
  return new AssetLibraryRequestError("failed", {
    kind,
    code: `FAILED_${kind.toUpperCase()}`,
    status: kind === "authentication" ? 401 : 503,
    requestId: `request-${kind}`,
    clientAttemptId: `attempt-${kind}`,
    retryable,
    retryAfterMs: retryable ? 0 : null,
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

describe("Asset Library query controller", () => {
  beforeEach(() => {
    resetAssetLibraryQueryControllerForTests();
    vi.clearAllMocks();
  });

  it("uses deterministic full-jitter delays with a bounded Retry-After", () => {
    expect(assetLibraryRetryDelay(0, null, () => 0)).toBe(0);
    expect(assetLibraryRetryDelay(0, null, () => 1)).toBe(300);
    expect(assetLibraryRetryDelay(4, null, () => 1)).toBe(4_800);
    expect(assetLibraryRetryDelay(10, null, () => 1)).toBe(5_000);
    expect(assetLibraryRetryDelay(0, 99_000)).toBe(5_000);
  });

  it("single-flights identical subscribers and publishes validated data", async () => {
    const pending = deferred<AssetLibraryPageResult>();
    feedMocks.fetchPage.mockReturnValue(pending.promise);

    const first = renderHook(() => useAssetLibraryQuery("workspace-1", {}));
    const second = renderHook(() => useAssetLibraryQuery("workspace-1", {}));
    expect(first.result.current.snapshot.phase).toBe("initial-loading");
    expect(feedMocks.fetchPage).toHaveBeenCalledOnce();

    await act(async () => pending.resolve(result()));
    await waitFor(() => {
      expect(first.result.current.snapshot.phase).toBe("success-with-data");
      expect(second.result.current.snapshot.data?.items[0]?.name).toBe("Hero");
    });
    expect(feedMocks.fetchPage).toHaveBeenCalledOnce();
    first.unmount();
    second.unmount();
  });

  it("never turns a non-retryable initial failure into an empty success", async () => {
    feedMocks.fetchPage.mockRejectedValue(failure("authentication"));
    const hook = renderHook(() => useAssetLibraryQuery("workspace-1", {}));

    await waitFor(() => expect(hook.result.current.snapshot.phase).toBe("initial-error"));
    expect(hook.result.current.snapshot.data).toBeNull();
    expect(hook.result.current.snapshot.error?.kind).toBe("authentication");
    expect(feedMocks.fetchPage).toHaveBeenCalledOnce();
    hook.unmount();
  });

  it("retries transient failures at most twice after the first attempt", async () => {
    feedMocks.fetchPage
      .mockRejectedValueOnce(failure("server", true))
      .mockRejectedValueOnce(failure("server", true))
      .mockResolvedValueOnce(result());
    const hook = renderHook(() => useAssetLibraryQuery("workspace-1", {}));

    await waitFor(() => expect(hook.result.current.snapshot.phase).toBe("success-with-data"));
    expect(feedMocks.fetchPage).toHaveBeenCalledTimes(3);
    expect(feedMocks.fetchPage.mock.calls.map(([options]) => (
      (options as { attempt: number }).attempt
    ))).toEqual([0, 1, 2]);
    hook.unmount();
  });

  it("retains stale data when a background refresh fails", async () => {
    feedMocks.fetchPage.mockResolvedValueOnce(result());
    const hook = renderHook(() => useAssetLibraryQuery("workspace-1", {}));
    await waitFor(() => expect(hook.result.current.snapshot.phase).toBe("success-with-data"));

    feedMocks.fetchPage.mockRejectedValueOnce(failure("authentication"));
    act(() => hook.result.current.refresh());
    expect(hook.result.current.snapshot.data?.items[0]?.name).toBe("Hero");
    await waitFor(() => {
      expect(hook.result.current.snapshot.phase).toBe("refresh-error-with-stale-data");
    });
    expect(hook.result.current.snapshot.data?.items[0]?.name).toBe("Hero");
    hook.unmount();
  });

  it("coalesces a burst of invalidations into one active and one trailing refresh", async () => {
    feedMocks.fetchPage.mockResolvedValueOnce(result());
    const hook = renderHook(() => useAssetLibraryQuery("workspace-1", {}));
    await waitFor(() => expect(hook.result.current.snapshot.phase).toBe("success-with-data"));

    const active = deferred<AssetLibraryPageResult>();
    feedMocks.fetchPage
      .mockReturnValueOnce(active.promise)
      .mockResolvedValueOnce(result(feed(2, ["Updated"])));
    act(() => {
      invalidateAssetLibraryQueries("workspace-1");
      invalidateAssetLibraryQueries("workspace-1");
      invalidateAssetLibraryQueries("workspace-1");
    });
    expect(feedMocks.fetchPage).toHaveBeenCalledTimes(2);

    await act(async () => active.resolve(result()));
    await waitFor(() => expect(feedMocks.fetchPage).toHaveBeenCalledTimes(3));
    await waitFor(() => {
      expect(hook.result.current.snapshot.data?.items[0]?.name).toBe("Updated");
    });
    hook.unmount();
  });

  it("aborts an in-flight request after the final subscriber unmounts", async () => {
    let requestSignal: AbortSignal | undefined;
    feedMocks.fetchPage.mockImplementation(
      ({ signal }: { signal?: AbortSignal }) => {
        requestSignal = signal;
        return new Promise<never>((_resolve, reject) => {
          signal?.addEventListener("abort", () => reject(failure("aborted")), {
            once: true,
          });
        });
      },
    );
    const hook = renderHook(() => useAssetLibraryQuery("workspace-1", {}));
    await waitFor(() => expect(feedMocks.fetchPage).toHaveBeenCalledOnce());

    hook.unmount();
    expect(requestSignal?.aborted).toBe(true);
  });

  it("keeps the newest search/filter/sort request when an older request finishes late", async () => {
    const oldRequest = deferred<AssetLibraryPageResult>();
    const newRequest = deferred<AssetLibraryPageResult>();
    feedMocks.fetchPage
      .mockReturnValueOnce(oldRequest.promise)
      .mockReturnValueOnce(newRequest.promise);
    const hook = renderHook(
      ({ search }) => useAssetLibraryQuery("workspace-1", {
        assetType: "character",
        starred: true,
        search,
        sort: "usage",
        order: "desc",
      }),
      { initialProps: { search: "old" } },
    );
    await waitFor(() => expect(feedMocks.fetchPage).toHaveBeenCalledOnce());

    hook.rerender({ search: "new" });
    await waitFor(() => expect(feedMocks.fetchPage).toHaveBeenCalledTimes(2));
    expect(feedMocks.fetchPage.mock.calls[1]?.[0]).toMatchObject({
      query: {
        assetType: "character",
        starred: true,
        search: "new",
        sort: "usage",
        order: "desc",
        offset: 0,
      },
    });

    await act(async () => newRequest.resolve(result(feed(2, ["Newest"]))));
    await waitFor(() => {
      expect(hook.result.current.snapshot.data?.items[0]?.name).toBe("Newest");
    });
    await act(async () => oldRequest.resolve(result(feed(1, ["Obsolete"]))));
    expect(hook.result.current.snapshot.data?.items[0]?.name).toBe("Newest");
    hook.unmount();
  });

  it("merges a matching next page without duplicating the first page", async () => {
    feedMocks.fetchPage.mockResolvedValueOnce(
      result(feed(1, ["First"], { total: 2, hasMore: true })),
    );
    const hook = renderHook(() => useAssetLibraryQuery("workspace-1", {}));
    await waitFor(() => expect(hook.result.current.snapshot.phase).toBe("success-with-data"));

    feedMocks.fetchPage.mockResolvedValueOnce(
      result(feed(1, ["Second"], { offset: 1, total: 2 })),
    );
    act(() => hook.result.current.loadMore());
    await waitFor(() => expect(hook.result.current.snapshot.data?.items).toHaveLength(2));
    expect(hook.result.current.snapshot.data?.items.map((item) => item.name))
      .toEqual(["First", "Second"]);
    hook.unmount();
  });

  it("preserves usage order for the initial request and subsequent pages", async () => {
    feedMocks.fetchPage.mockResolvedValueOnce(
      result(feed(1, ["Least"], { total: 2, hasMore: true })),
    );
    const hook = renderHook(() => useAssetLibraryQuery("workspace-1", {
      sort: "usage",
      order: "asc",
    }));
    await waitFor(() => expect(hook.result.current.snapshot.phase).toBe("success-with-data"));

    expect(feedMocks.fetchPage.mock.calls[0]?.[0]).toMatchObject({
      query: {
        sort: "usage",
        order: "asc",
        offset: 0,
      },
    });

    feedMocks.fetchPage.mockResolvedValueOnce(
      result(feed(1, ["Most"], { offset: 1, total: 2 })),
    );
    act(() => hook.result.current.loadMore());
    await waitFor(() => expect(hook.result.current.snapshot.data?.items).toHaveLength(2));
    expect(feedMocks.fetchPage.mock.calls[1]?.[0]).toMatchObject({
      query: {
        sort: "usage",
        order: "asc",
        offset: 1,
      },
    });
    expect(hook.result.current.snapshot.data?.items.map((item) => item.name))
      .toEqual(["Least", "Most"]);
    hook.unmount();
  });
});
