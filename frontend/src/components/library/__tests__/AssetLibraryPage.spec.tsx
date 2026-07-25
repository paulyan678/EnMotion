import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithIntl } from "@/test/renderWithIntl";
import { beforeEach, describe, expect, it, vi } from "vitest";

const queryMocks = vi.hoisted(() => ({
  snapshot: null as unknown,
  refresh: vi.fn(),
  loadMore: vi.fn(),
  invalidate: vi.fn(),
  queryArgs: [] as unknown[],
}));
const apiMocks = vi.hoisted(() => ({
  getOwnedAsset: vi.fn(),
  setOwnedAssetFavorite: vi.fn(),
  updateLibraryAsset: vi.fn(),
  toggleSeriesAssetStarred: vi.fn(),
  toggleAssetStarred: vi.fn(),
  getOwnedAssetDeleteImpact: vi.fn(),
  deleteOwnedAsset: vi.fn(),
}));
const toastMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({
    user: {
      id: "user-1",
      username: "admin",
      role: "admin",
      workspace_id: "workspace-1",
    },
  }),
}));
vi.mock("@/lib/assetLibraryQuery", () => ({
  useAssetLibraryQuery: (...args: unknown[]) => {
    queryMocks.queryArgs = args;
    return {
    snapshot: queryMocks.snapshot,
    refresh: queryMocks.refresh,
    loadMore: queryMocks.loadMore,
    };
  },
  invalidateAssetLibraryQueries: (...args: unknown[]) => queryMocks.invalidate(...args),
}));
vi.mock("@/lib/api", () => ({
  api: {
    getOwnedAsset: (...args: unknown[]) => apiMocks.getOwnedAsset(...args),
    setOwnedAssetFavorite: (...args: unknown[]) => apiMocks.setOwnedAssetFavorite(...args),
    updateLibraryAsset: (...args: unknown[]) => apiMocks.updateLibraryAsset(...args),
    toggleSeriesAssetStarred: (...args: unknown[]) => apiMocks.toggleSeriesAssetStarred(...args),
    toggleAssetStarred: (...args: unknown[]) => apiMocks.toggleAssetStarred(...args),
    getOwnedAssetDeleteImpact: (...args: unknown[]) => apiMocks.getOwnedAssetDeleteImpact(...args),
    deleteOwnedAsset: (...args: unknown[]) => apiMocks.deleteOwnedAsset(...args),
  },
}));
vi.mock("@/lib/apiUrl", () => ({ API_URL: "http://127.0.0.1:17177" }));
vi.mock("@/store/toastStore", () => ({
  toast: {
    success: (...args: unknown[]) => toastMocks.success(...args),
    error: (...args: unknown[]) => toastMocks.error(...args),
  },
}));
vi.mock("../AssetInspector", () => ({
  default: ({
    onEdit,
    onDelete,
    deleting,
  }: {
    onEdit: () => void;
    onDelete: () => void;
    deleting: boolean;
  }) => (
    <div>
      <button type="button" onClick={onEdit}>编辑所选资产</button>
      <button type="button" onClick={onDelete} disabled={deleting}>
        {deleting ? "正在删除所选资产" : "删除所选资产"}
      </button>
    </div>
  ),
}));
vi.mock("@/components/assets/SharedAssetEditor", () => ({
  default: ({
    open,
    assetRef,
    onClose,
  }: {
    open: boolean;
    assetRef: {
      ownerKind: string;
      ownerId: string;
      assetType: string;
      assetId: string;
    };
    onClose: () => void;
  }) => open ? (
    <div
      data-testid="library-asset-editor"
      data-source-kind={assetRef.ownerKind}
      data-source-id={assetRef.ownerId}
      data-asset-type={assetRef.assetType}
      data-asset-id={assetRef.assetId}
    >
      <button type="button" onClick={onClose}>关闭编辑器</button>
    </div>
  ) : null,
}));
vi.mock("../NewLibraryAssetDialog", () => ({ default: () => null }));

import AssetLibraryPage from "../AssetLibraryPage";
import {
  AssetLibraryRequestError,
  type AssetLibraryFeedItem,
  type AssetLibraryFeedResponse,
} from "@/lib/assetLibraryFeed";
import {
  notifyAssetLibraryChanged,
  subscribeToAssetLibraryChanges,
} from "@/lib/assetLibrarySync";

function character(
  name = "Visible Hero",
  {
    id = "character-1",
    sourceKind = "series" as const,
    sourceId = "series-1",
    imageUrl = "",
    starred = false,
  }: {
    id?: string;
    sourceKind?: "series" | "project" | "global";
    sourceId?: string;
    imageUrl?: string;
    starred?: boolean;
  } = {},
): AssetLibraryFeedItem {
  return {
    id,
    name,
    description: `${name} description`,
    asset_type: "character",
    source_kind: sourceKind,
    source_id: sourceId,
    source_name: sourceKind === "global" ? "Global" : "Series",
    series_id: sourceKind === "series" ? sourceId : null,
    episode_id: null,
    starred,
    thumbnail: imageUrl
      ? {
          id: `${id}-selected`,
          url: imageUrl,
          created_at: 1,
          media_id: "a".repeat(64),
          revision: "a".repeat(64),
          width: 1024,
          height: 1024,
          aspect_ratio: 1,
          mime_type: "image/png",
          byte_size: 750_000,
          state: "ready",
          derivatives: [{
            url: `derivatives/${sourceKind}-${sourceId}-${id}/w384.webp`,
            width: 384,
            height: 384,
            mime_type: "image/webp",
            byte_size: 24_000,
          }],
          failure_code: null,
        }
      : null,
    variant_count: imageUrl ? 1 : 0,
    updated_at: 1,
    usage_count: 0,
  };
}

function feed(
  items: AssetLibraryFeedItem[],
  {
    revision = 1,
    total = items.length,
    hasMore = false,
  } = {},
): AssetLibraryFeedResponse {
  const characters = items.filter((item) => item.asset_type === "character").length;
  const scenes = items.filter((item) => item.asset_type === "scene").length;
  const props = items.filter((item) => item.asset_type === "prop").length;
  return {
    schema_version: 3,
    revision,
    generated_at: 1,
    items,
    facets: {
      all: total,
      characters,
      scenes,
      props,
      starred: items.filter((item) => item.starred).length,
    },
    page: {
      offset: 0,
      limit: 50,
      count: items.length,
      total,
      has_more: hasMore,
      next_offset: hasMore ? items.length : null,
    },
  };
}

function successSnapshot(items: AssetLibraryFeedItem[]) {
  return {
    phase: items.length ? "success-with-data" : "success-empty",
    data: feed(items),
    error: null,
    requestId: "request-1",
    isLoadingMore: false,
    loadMoreError: null,
  };
}

const defaultImpact = {
  source_kind: "series",
  source_id: "series-1",
  asset_type: "character",
  asset_id: "character-1",
  asset_name: "Visible Hero",
  references: [],
  reference_count: 0,
  has_references: false,
};

describe("AssetLibraryPage strict feed states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryMocks.snapshot = successSnapshot([character()]);
    apiMocks.getOwnedAsset.mockResolvedValue({
      id: "character-1",
      name: "Visible Hero",
      description: "full detail",
    });
    apiMocks.updateLibraryAsset.mockResolvedValue({
      id: "global-tester",
      starred: true,
    });
    apiMocks.setOwnedAssetFavorite.mockResolvedValue({
      id: "global-tester",
      name: "Tester",
      starred: true,
    });
    apiMocks.getOwnedAssetDeleteImpact.mockResolvedValue(defaultImpact);
    apiMocks.deleteOwnedAsset.mockResolvedValue({ status: "deleted" });
  });

  it("renders the strict compact feed without the old decorative overline", async () => {
    renderWithIntl(<AssetLibraryPage />);

    expect(await screen.findByText("Visible Hero")).toBeInTheDocument();
    const title = screen.getByRole("heading", { name: "资产库" });
    expect(title.previousElementSibling).toBeNull();
  });

  it("keeps character previews contained", async () => {
    queryMocks.snapshot = successSnapshot([
      character("Contained Hero", { imageUrl: "assets/master.png" }),
    ]);
    renderWithIntl(<AssetLibraryPage />);

    const image = await screen.findByRole("img", { name: "Contained Hero" });
    expect(image).toHaveClass("object-contain");
    expect(image).not.toHaveClass("object-cover");
    expect(image).toHaveAttribute(
      "src",
      "http://127.0.0.1:17177/files/derivatives/series-series-1-character-1/w384.webp",
    );
  });

  it("shows the genuine empty state only after a validated empty success", () => {
    queryMocks.snapshot = successSnapshot([]);
    renderWithIntl(<AssetLibraryPage />);

    expect(screen.getByText(/每一个角色/)).toBeInTheDocument();
    expect(screen.queryByText("资产库加载失败")).not.toBeInTheDocument();
  });

  it("shows a categorized initial error with safe diagnostics and Retry", () => {
    const error = new AssetLibraryRequestError("network failed", {
      kind: "network",
      code: "ASSET_LIBRARY_NETWORK_UNAVAILABLE",
      requestId: "request-safe-123",
      clientAttemptId: "attempt-1",
      retryable: true,
    });
    queryMocks.snapshot = {
      phase: "initial-error",
      data: null,
      error,
      requestId: "request-safe-123",
      isLoadingMore: false,
      loadMoreError: null,
    };
    renderWithIntl(<AssetLibraryPage />);

    expect(screen.getByRole("alert")).toHaveTextContent("资产库加载失败");
    expect(screen.queryByText(/每一个角色/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(queryMocks.refresh).toHaveBeenCalledOnce();
  });

  it("retains stale cards and reports a background refresh failure", () => {
    const error = new AssetLibraryRequestError("timed out", {
      kind: "timeout",
      code: "ASSET_LIBRARY_TIMEOUT",
      status: 408,
      requestId: "request-timeout",
      clientAttemptId: "attempt-2",
      retryable: true,
    });
    queryMocks.snapshot = {
      ...successSnapshot([character()]),
      phase: "refresh-error-with-stale-data",
      error,
    };
    renderWithIntl(<AssetLibraryPage />);

    expect(screen.getByText("Visible Hero")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("资产库刷新失败");
  });

  it("opens the shared editor with the canonical owner identity", async () => {
    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText("Visible Hero"));
    fireEvent.click(screen.getByRole("button", { name: "编辑所选资产" }));

    const editor = screen.getByTestId("library-asset-editor");
    expect(editor).toHaveAttribute("data-source-kind", "series");
    expect(editor).toHaveAttribute("data-source-id", "series-1");
    expect(editor).toHaveAttribute("data-asset-type", "character");
    expect(editor).toHaveAttribute("data-asset-id", "character-1");
  });

  it("broadcasts global asset updates to synchronized views", async () => {
    queryMocks.snapshot = successSnapshot([
      character("Tester", {
        id: "global-tester",
        sourceKind: "global",
        sourceId: "global",
      }),
    ]);
    const listener = vi.fn();
    const unsubscribe = subscribeToAssetLibraryChanges(listener);
    renderWithIntl(<AssetLibraryPage />);

    fireEvent.click(await screen.findByRole("button", { name: "添加到收藏" }));
    await waitFor(() => expect(apiMocks.setOwnedAssetFavorite).toHaveBeenCalledWith(
      "global",
      "global",
      "character",
      "global-tester",
      true,
    ));
    expect(listener).toHaveBeenCalledWith({
      source: "global",
      assetType: "character",
      assetId: "global-tester",
      asset: expect.objectContaining({
        id: "global-tester",
        starred: true,
      }),
      invalidateCollection: true,
    });
    expect(queryMocks.invalidate).toHaveBeenCalled();
    unsubscribe();
  });

  it("keeps same-id assets from different owners distinct", () => {
    queryMocks.snapshot = successSnapshot([
      character("Series Hero", {
        id: "shared-id",
        sourceKind: "series",
        sourceId: "series-1",
        imageUrl: "assets/series.png",
      }),
      character("Global Hero", {
        id: "shared-id",
        sourceKind: "global",
        sourceId: "global",
        imageUrl: "assets/global.png",
      }),
    ]);
    renderWithIntl(<AssetLibraryPage />);

    expect(screen.getByRole("img", { name: "Series Hero" })).toHaveAttribute(
      "src",
      "http://127.0.0.1:17177/files/derivatives/series-series-1-shared-id/w384.webp",
    );
    expect(screen.getByRole("img", { name: "Global Hero" })).toHaveAttribute(
      "src",
      "http://127.0.0.1:17177/files/derivatives/global-global-shared-id/w384.webp",
    );
  });

  it("waits for server confirmation before deleting, then synchronizes", async () => {
    let resolveDelete: (value: { status: string }) => void = () => undefined;
    apiMocks.deleteOwnedAsset.mockImplementationOnce(
      () => new Promise((resolve) => { resolveDelete = resolve; }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const listener = vi.fn();
    const unsubscribe = subscribeToAssetLibraryChanges(listener);
    renderWithIntl(<AssetLibraryPage />);

    fireEvent.click(await screen.findByText("Visible Hero"));
    const deleteButton = screen.getByRole("button", { name: "删除所选资产" });
    fireEvent.click(deleteButton);
    fireEvent.click(deleteButton);
    await waitFor(() => expect(apiMocks.deleteOwnedAsset).toHaveBeenCalledOnce());
    expect(screen.getByText("Visible Hero")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "正在删除所选资产" })).toBeDisabled();

    await act(async () => resolveDelete({ status: "deleted" }));
    await waitFor(() => expect(screen.queryByText("Visible Hero")).not.toBeInTheDocument());
    expect(listener).toHaveBeenCalledWith({
      source: "series",
      seriesId: "series-1",
      assetType: "character",
      assetId: "character-1",
      invalidateCollection: true,
    });
    expect(toastMocks.success).toHaveBeenCalled();
    unsubscribe();
  });

  it("preserves the asset on cancellation and server failure", async () => {
    const confirmDelete = vi.spyOn(window, "confirm").mockReturnValue(false);
    const rendered = renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText("Visible Hero"));
    fireEvent.click(screen.getByRole("button", { name: "删除所选资产" }));
    await waitFor(() => expect(apiMocks.getOwnedAssetDeleteImpact).toHaveBeenCalledOnce());
    expect(apiMocks.deleteOwnedAsset).not.toHaveBeenCalled();
    expect(screen.getByText("Visible Hero")).toBeInTheDocument();
    await waitFor(() => expect(confirmDelete).toHaveBeenCalledOnce());
    const retryDeleteButton = await screen.findByRole("button", { name: "删除所选资产" });
    await waitFor(() => expect(retryDeleteButton).toBeEnabled());

    confirmDelete.mockReturnValue(true);
    apiMocks.deleteOwnedAsset.mockRejectedValueOnce(new Error("server unavailable"));
    fireEvent.click(retryDeleteButton);
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalled());
    expect(screen.getByText("Visible Hero")).toBeInTheDocument();
    rendered.unmount();
  });

  it("warns before force-deleting an asset with references", async () => {
    apiMocks.getOwnedAssetDeleteImpact.mockResolvedValueOnce({
      ...defaultImpact,
      references: [{ reference_type: "storyboard", owner_id: "episode-1" }],
      reference_count: 1,
      has_references: true,
    });
    const confirmDelete = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText("Visible Hero"));
    fireEvent.click(screen.getByRole("button", { name: "删除所选资产" }));

    await waitFor(() => expect(apiMocks.deleteOwnedAsset).toHaveBeenCalledWith(
      "series",
      "series-1",
      "character",
      "character-1",
      true,
    ));
    expect(confirmDelete).toHaveBeenCalledWith(
      expect.stringContaining("1 个项目、系列、分镜或生成任务关联"),
    );
  });

  it("loads the next strict page through the query controller", () => {
    queryMocks.snapshot = {
      ...successSnapshot([character()]),
      data: feed([character()], { total: 2, hasMore: true }),
    };
    renderWithIntl(<AssetLibraryPage />);

    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    expect(queryMocks.loadMore).toHaveBeenCalledOnce();
  });

  it("invalidates the canonical controller when another asset view changes", async () => {
    renderWithIntl(<AssetLibraryPage />);
    act(() => notifyAssetLibraryChanged({ seriesId: "series-1" }));
    await waitFor(() => expect(queryMocks.invalidate).toHaveBeenCalled());
  });
});
