// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import {
  ASSET_LIBRARY_CHANGED_EVENT,
  notifyAssetLibraryChanged,
  notifyAssetUsageChanged,
  notifyProjectAssetChanged,
  subscribeToAssetLibraryChanges,
} from "@/lib/assetLibrarySync";
import {
  resetProjectWorkspaceState,
  useProjectStore,
  type Project,
  type Series,
} from "@/store/projectStore";

vi.mock("@/lib/api", () => ({
  api: {
    reparseProject: vi.fn(),
    getProject: vi.fn(),
    getSeries: vi.fn(),
  },
}));

const baseProject: Project = {
  id: "episode-1",
  title: "Episode",
  originalText: "old",
  characters: [],
  scenes: [],
  props: [],
  frames: [],
  status: "draft",
  createdAt: "2026-07-22T00:00:00.000Z",
  updatedAt: "2026-07-22T00:00:00.000Z",
  series_id: "series-1",
  episode_number: 1,
};

const mergedProject: Project = {
  ...baseProject,
  originalText: "new",
  characters: [
    {
      id: "character-1",
      name: "Hero",
      source: "series",
    },
  ],
};

const series: Series = {
  id: "series-1",
  title: "Series",
  description: "",
  characters: [{ id: "character-1", name: "Hero" }],
  scenes: [],
  props: [],
  episode_ids: [baseProject.id],
  created_at: 1,
  updated_at: 2,
};

beforeEach(() => {
  resetProjectWorkspaceState();
  useProjectStore.setState({
    projects: [baseProject],
    currentProject: baseProject,
    pendingExtraction: { characters: [], scenes: [], props: [] },
    pendingExtractionScript: "new",
    seriesList: [],
    currentSeries: null,
  });
});

afterEach(() => {
  resetProjectWorkspaceState();
  vi.clearAllMocks();
});

describe("asset library invalidation", () => {
  it("delivers change details and unsubscribes cleanly", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToAssetLibraryChanges(listener);

    notifyAssetLibraryChanged({ projectId: "episode-1", seriesId: "series-1" });
    expect(listener).toHaveBeenCalledWith({
      projectId: "episode-1",
      seriesId: "series-1",
      invalidateCollection: true,
    });

    unsubscribe();
    notifyAssetLibraryChanged({ seriesId: "series-1" });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("broadcasts resolved asset mutations using their canonical owner", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToAssetLibraryChanges(listener);

    notifyProjectAssetChanged(
      baseProject,
      { id: "global-character", source: "global", source_id: "global" },
      "character",
    );
    notifyProjectAssetChanged(
      baseProject,
      { id: "series-character", source: "series", source_id: "series-1" },
      "character",
    );
    notifyProjectAssetChanged(
      baseProject,
      { id: "episode-scene", source: "episode", source_id: "episode-1" },
      "scene",
    );

    expect(listener).toHaveBeenNthCalledWith(1, {
      source: "global",
      assetType: "character",
      assetId: "global-character",
      invalidateCollection: true,
    });
    expect(listener).toHaveBeenNthCalledWith(2, {
      source: "series",
      seriesId: "series-1",
      assetType: "character",
      assetId: "series-character",
      invalidateCollection: true,
    });
    expect(listener).toHaveBeenNthCalledWith(3, {
      source: "project",
      projectId: "episode-1",
      seriesId: "series-1",
      assetType: "scene",
      assetId: "episode-scene",
      invalidateCollection: true,
    });

    unsubscribe();
  });

  it("coalesces persisted relationship changes into one authoritative usage refresh", async () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToAssetLibraryChanges(listener);

    notifyAssetUsageChanged();
    notifyAssetUsageChanged();
    await Promise.resolve();

    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith({
      invalidateCollection: true,
      usageChanged: true,
    });
    unsubscribe();
  });

  it("refetches merged project and series data after extraction", async () => {
    vi.mocked(api.reparseProject).mockResolvedValueOnce(baseProject);
    vi.mocked(api.getProject).mockResolvedValueOnce(mergedProject);
    vi.mocked(api.getSeries).mockResolvedValueOnce(series);
    const eventListener = vi.fn();
    window.addEventListener(ASSET_LIBRARY_CHANGED_EVENT, eventListener);

    await useProjectStore.getState().confirmExtraction();

    expect(api.reparseProject).toHaveBeenCalledWith(baseProject.id, "new");
    expect(api.getProject).toHaveBeenCalledWith(baseProject.id);
    expect(api.getSeries).toHaveBeenCalledWith(series.id);
    expect(useProjectStore.getState().currentProject?.characters).toEqual(
      mergedProject.characters,
    );
    expect(useProjectStore.getState().currentSeries).toEqual(series);
    expect(useProjectStore.getState().seriesList).toEqual([series]);
    expect(useProjectStore.getState().pendingExtraction).toBeNull();
    expect(eventListener).toHaveBeenCalledTimes(1);

    window.removeEventListener(ASSET_LIBRARY_CHANGED_EVENT, eventListener);
  });
});
