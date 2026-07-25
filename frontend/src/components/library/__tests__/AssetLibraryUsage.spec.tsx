import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithIntl } from "@/test/renderWithIntl";
import { beforeEach, describe, expect, it, vi } from "vitest";

const queryMocks = vi.hoisted(() => ({
  snapshot: null as unknown,
  refresh: vi.fn(),
  loadMore: vi.fn(),
  calls: [] as unknown[][],
}));
const authMocks = vi.hoisted(() => ({
  user: {
    id: "user-1",
    username: "admin",
    role: "admin",
    workspace_id: "workspace-1",
  },
}));
const apiMocks = vi.hoisted(() => ({
  getOwnedAsset: vi.fn(),
}));

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({ user: authMocks.user }),
}));
vi.mock("@/lib/assetLibraryQuery", () => ({
  useAssetLibraryQuery: (...args: unknown[]) => {
    queryMocks.calls.push(args);
    return {
      snapshot: queryMocks.snapshot,
      refresh: queryMocks.refresh,
      loadMore: queryMocks.loadMore,
    };
  },
  invalidateAssetLibraryQueries: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  api: {
    getOwnedAsset: (...args: unknown[]) => apiMocks.getOwnedAsset(...args),
  },
}));
vi.mock("@/lib/apiUrl", () => ({ API_URL: "http://127.0.0.1:17177" }));
vi.mock("@/store/toastStore", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("../AssetInspector", () => ({
  default: ({
    asset,
    usageCount,
  }: {
    asset: { name: string };
    usageCount: number;
  }) => (
      <aside data-testid="usage-inspector">
        <span>{asset.name}</span>
        <span data-testid="inspector-usage">{usageCount}</span>
      </aside>
  ),
}));
vi.mock("@/components/assets/SharedAssetEditor", () => ({
  default: () => null,
}));
vi.mock("../NewLibraryAssetDialog", () => ({
  default: () => null,
}));

import AssetLibraryPage from "../AssetLibraryPage";
import {
  AssetLibraryRequestError,
  type AssetLibraryFeedItem,
  type AssetLibraryFeedResponse,
  type AssetLibraryOrder,
  type AssetLibrarySort,
} from "@/lib/assetLibraryFeed";
import { invalidateAssetLibraryQueries } from "@/lib/assetLibraryQuery";
import {
  notifyAssetLibraryChanged,
  notifyAssetUsageChanged,
} from "@/lib/assetLibrarySync";

function character(
  name: string,
  usageCount: number,
  id = name.toLowerCase().replaceAll(" ", "-"),
): AssetLibraryFeedItem {
  return {
    id,
    name,
    description: `${name} description`,
    asset_type: "character",
    source_kind: "series",
    source_id: "series-1",
    source_name: "Series",
    series_id: "series-1",
    episode_id: null,
    starred: false,
    thumbnail: null,
    variant_count: 0,
    updated_at: 1,
    usage_count: usageCount,
  };
}

function feed(items: AssetLibraryFeedItem[], revision = 1): AssetLibraryFeedResponse {
  return {
    schema_version: 3,
    revision,
    generated_at: revision,
    items,
    facets: {
      all: items.length,
      characters: items.length,
      scenes: 0,
      props: 0,
      starred: 0,
    },
    page: {
      offset: 0,
      limit: 50,
      count: items.length,
      total: items.length,
      has_more: false,
      next_offset: null,
    },
  };
}

function success(items: AssetLibraryFeedItem[]) {
  return {
    phase: items.length ? "success-with-data" : "success-empty",
    data: feed(items),
    error: null,
    requestId: "request-usage",
    isLoadingMore: false,
    loadMoreError: null,
  };
}

function lastQuery(): {
  assetType?: string;
  starred?: boolean;
  search?: string;
  sort?: AssetLibrarySort;
  order?: AssetLibraryOrder;
} {
  return queryMocks.calls.at(-1)?.[1] as {
    assetType?: string;
    starred?: boolean;
    search?: string;
    sort?: AssetLibrarySort;
    order?: AssetLibraryOrder;
  };
}

function openSortMenu() {
  fireEvent.click(screen.getByRole("button", { name: "Sort" }));
  return screen.getByRole("listbox", { name: "Sort" });
}

describe("Asset Library usage sorting", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    authMocks.user = {
      id: "user-1",
      username: "admin",
      role: "admin",
      workspace_id: "workspace-1",
    };
    queryMocks.snapshot = success([
      character("First from server", 1),
      character("Second from server", 9),
    ]);
    apiMocks.getOwnedAsset.mockResolvedValue({
      id: "first-from-server",
      name: "First from server",
      description: "First from server description",
    });
  });

  it("enables usage sorting, removes the backend placeholder, and defaults to most-used", async () => {
    renderWithIntl(<AssetLibraryPage />, { locale: "en" });

    openSortMenu();
    const mostUsed = screen.getByRole("option", { name: "Usage · Most used" });
    const leastUsed = screen.getByRole("option", { name: "Usage · Least used" });
    expect(mostUsed).toBeEnabled();
    expect(leastUsed).toBeEnabled();
    expect(screen.queryByText("Needs backend")).not.toBeInTheDocument();

    fireEvent.click(mostUsed);
    await waitFor(() => expect(lastQuery()).toMatchObject({
      sort: "usage",
      order: "desc",
    }));
    expect(screen.getByRole("button", { name: "Sort" }))
      .toHaveTextContent("Usage · Most used");
  });

  it("requests least-used order without re-sorting the server page", async () => {
    renderWithIntl(<AssetLibraryPage />, { locale: "en" });

    openSortMenu();
    fireEvent.click(screen.getByRole("option", { name: "Usage · Least used" }));

    await waitFor(() => expect(lastQuery()).toMatchObject({
      sort: "usage",
      order: "asc",
    }));
    const names = screen
      .getAllByText(/^(First|Second) from server$/)
      .map((node) => node.textContent);
    expect(names).toEqual(["First from server", "Second from server"]);
  });

  it("renders zero, singular, and plural usage labels on cards", async () => {
    queryMocks.snapshot = success([
      character("Unused", 0),
      character("Used once asset", 1),
      character("Frequently used", 4),
    ]);
    renderWithIntl(<AssetLibraryPage />, { locale: "en" });

    openSortMenu();
    fireEvent.click(screen.getByRole("option", { name: "Usage · Most used" }));

    expect(await screen.findByText("Never used")).toBeInTheDocument();
    expect(screen.getByText("Used once")).toBeInTheDocument();
    expect(screen.getByText("Used 4 times")).toBeInTheDocument();
  });

  it("renders natural zero, singular, and plural usage labels in Chinese", async () => {
    queryMocks.snapshot = success([
      character("Unused", 0),
      character("Used once asset", 1),
      character("Frequently used", 4),
    ]);
    renderWithIntl(<AssetLibraryPage />, { locale: "zh" });

    fireEvent.click(screen.getByRole("button", { name: "排序" }));
    fireEvent.click(screen.getByRole("option", { name: "使用频次 · 最多使用" }));

    expect(await screen.findByText("从未使用")).toBeInTheDocument();
    expect(screen.getByText("使用 1 次")).toBeInTheDocument();
    expect(screen.getByText("使用 4 次")).toBeInTheDocument();
  });

  it("isolates session preferences by account and workspace", async () => {
    window.sessionStorage.setItem(
      "enmotion:asset-library-sort:v1:user-1:workspace-1",
      JSON.stringify({ mode: "usage", order: "desc" }),
    );
    window.sessionStorage.setItem(
      "enmotion:asset-library-sort:v1:user-2:workspace-1",
      JSON.stringify({ mode: "name", order: "asc" }),
    );
    window.sessionStorage.setItem(
      "enmotion:asset-library-sort:v1:user-2:workspace-2",
      JSON.stringify({ mode: "usage", order: "asc" }),
    );
    const rendered = renderWithIntl(<AssetLibraryPage />, { locale: "en" });

    await waitFor(() => expect(lastQuery()).toMatchObject({
      sort: "usage",
      order: "desc",
    }));

    authMocks.user = {
      id: "user-2",
      username: "editor",
      role: "user",
      workspace_id: "workspace-1",
    };
    rendered.rerender(<AssetLibraryPage />);
    await waitFor(() => expect(lastQuery()).toMatchObject({
      sort: "name",
      order: "asc",
    }));

    authMocks.user = {
      ...authMocks.user,
      workspace_id: "workspace-2",
    };
    rendered.rerender(<AssetLibraryPage />);
    await waitFor(() => expect(lastQuery()).toMatchObject({
      sort: "usage",
      order: "asc",
    }));
  });

  it("recovers from malformed session storage with safe defaults", async () => {
    const key = "enmotion:asset-library-sort:v1:user-1:workspace-1";
    window.sessionStorage.setItem(key, "{not valid json");
    renderWithIntl(<AssetLibraryPage />, { locale: "en" });

    await waitFor(() => expect(lastQuery()).toMatchObject({
      sort: "default",
      order: "asc",
    }));
    await waitFor(() => expect(JSON.parse(window.sessionStorage.getItem(key) ?? "{}"))
      .toEqual({ mode: "default", order: "asc" }));
  });

  it("rejects obsolete stored enum combinations and repairs them", async () => {
    const key = "enmotion:asset-library-sort:v1:user-1:workspace-1";
    window.sessionStorage.setItem(
      key,
      JSON.stringify({ mode: "usage", order: "sideways" }),
    );
    renderWithIntl(<AssetLibraryPage />, { locale: "en" });

    await waitFor(() => expect(lastQuery()).toMatchObject({
      sort: "default",
      order: "asc",
    }));
    await waitFor(() => expect(JSON.parse(window.sessionStorage.getItem(key) ?? "{}"))
      .toEqual({ mode: "default", order: "asc" }));
  });

  it("preserves usage sort while search, type, and favorite filters change", async () => {
    renderWithIntl(<AssetLibraryPage />, { locale: "en" });
    openSortMenu();
    fireEvent.click(screen.getByRole("option", { name: "Usage · Most used" }));
    fireEvent.click(screen.getByRole("tab", { name: /Characters/ }));
    fireEvent.click(screen.getByRole("button", { name: "Starred only" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "Search assets..." }), {
      target: { value: "hero" },
    });

    await waitFor(() => expect(lastQuery()).toMatchObject({
      assetType: "character",
      starred: true,
      search: "hero",
      sort: "usage",
      order: "desc",
    }));
  });

  it("coalesces a confirmed relationship mutation into an authoritative refresh", async () => {
    renderWithIntl(<AssetLibraryPage />, { locale: "en" });

    notifyAssetUsageChanged();
    notifyAssetUsageChanged();
    notifyAssetLibraryChanged({ source: "global" });
    await waitFor(() => {
      expect(invalidateAssetLibraryQueries).toHaveBeenCalledTimes(1);
    });

    expect(invalidateAssetLibraryQueries).toHaveBeenCalledTimes(1);
  });

  it("retains cards while a usage revalidation is loading and after it fails", async () => {
    const rendered = renderWithIntl(<AssetLibraryPage />, { locale: "en" });
    expect(await screen.findByText("First from server")).toBeInTheDocument();
    const staleFeed = feed([
      character("First from server", 1),
      character("Second from server", 9),
    ]);

    openSortMenu();
    fireEvent.click(screen.getByRole("option", { name: "Usage · Most used" }));
    queryMocks.snapshot = {
      phase: "refreshing-with-stale-data",
      data: staleFeed,
      error: null,
      requestId: "request-loading",
      isLoadingMore: false,
      loadMoreError: null,
    };
    rendered.rerender(<AssetLibraryPage />);
    expect(screen.getByText("First from server")).toBeInTheDocument();

    const error = new AssetLibraryRequestError("usage unavailable", {
      kind: "server",
      code: "ASSET_LIBRARY_USAGE_UNAVAILABLE",
      status: 503,
      requestId: "request-failed",
      clientAttemptId: "attempt-failed",
      retryable: true,
    });
    queryMocks.snapshot = {
      phase: "refresh-error-with-stale-data",
      data: staleFeed,
      error,
      requestId: "request-failed",
      isLoadingMore: false,
      loadMoreError: null,
    };
    rendered.rerender(<AssetLibraryPage />);

    expect(screen.getByText("First from server")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Usage data failed to load");
    expect(screen.getByRole("button", { name: "Sort" }))
      .toHaveTextContent("Usage · Most used");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(queryMocks.refresh).toHaveBeenCalledOnce();
  });

  it("keeps the selected asset open when a sorted response reorders cards", async () => {
    const selected = character("Selected asset", 2, "selected");
    const other = character("Other asset", 5, "other");
    apiMocks.getOwnedAsset.mockResolvedValueOnce({
      id: "selected",
      name: "Selected asset",
      description: "Selected asset description",
    });
    const rendered = renderWithIntl(<AssetLibraryPage />, { locale: "en" });
    queryMocks.snapshot = success([selected, other]);
    rendered.rerender(<AssetLibraryPage />);

    fireEvent.click(await screen.findByText("Selected asset"));
    expect(await screen.findByTestId("usage-inspector")).toHaveTextContent("Selected asset");

    queryMocks.snapshot = success([other, selected]);
    rendered.rerender(<AssetLibraryPage />);

    expect(screen.getByTestId("usage-inspector")).toHaveTextContent("Selected asset");
    expect(screen.getByTestId("inspector-usage")).toHaveTextContent("2");
  });

  it("supports arrow, Home/End, Enter, Space, and Escape keyboard behavior", async () => {
    renderWithIntl(<AssetLibraryPage />, { locale: "en" });
    const trigger = screen.getByRole("button", { name: "Sort" });
    trigger.focus();

    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const listbox = await screen.findByRole("listbox", { name: "Sort" });
    const defaultOption = screen.getByRole("option", { name: "Default" });
    await waitFor(() => expect(defaultOption).toHaveFocus());

    fireEvent.keyDown(listbox, { key: "End" });
    const leastUsed = screen.getByRole("option", { name: "Usage · Least used" });
    expect(leastUsed).toHaveFocus();
    fireEvent.keyDown(listbox, { key: "Escape" });
    expect(screen.queryByRole("listbox", { name: "Sort" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    fireEvent.keyDown(trigger, { key: "ArrowUp" });
    const reopened = await screen.findByRole("listbox", { name: "Sort" });
    fireEvent.keyDown(reopened, { key: "End" });
    fireEvent.keyDown(reopened, { key: "Enter" });
    await waitFor(() => expect(lastQuery()).toMatchObject({
      sort: "usage",
      order: "asc",
    }));
    expect(trigger).toHaveFocus();

    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const spaceMenu = await screen.findByRole("listbox", { name: "Sort" });
    fireEvent.keyDown(spaceMenu, { key: "Home" });
    expect(screen.getByRole("option", { name: "Default" })).toHaveFocus();
    fireEvent.keyDown(spaceMenu, { key: " " });
    await waitFor(() => expect(lastQuery()).toMatchObject({
      sort: "default",
      order: "asc",
    }));
  });
});
