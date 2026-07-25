import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import {
  resetProjectWorkspaceState,
  useProjectStore,
  type Project,
  type Series,
} from "@/store/projectStore";

vi.mock("@/lib/api", () => ({
  api: {
    deleteProject: vi.fn(),
    deleteSeries: vi.fn(),
  },
}));

const project: Project = {
  id: "project-that-must-remain",
  title: "Deletion contract",
  originalText: "",
  characters: [],
  scenes: [],
  props: [],
  frames: [],
  status: "draft",
  createdAt: "2026-07-21T00:00:00.000Z",
  updatedAt: "2026-07-21T00:00:00.000Z",
};

const series: Series = {
  id: "series-to-delete",
  title: "Delete this series",
  description: "",
  characters: [],
  scenes: [],
  props: [],
  episode_ids: ["series-episode"],
  created_at: Date.parse("2026-07-21T00:00:00.000Z") / 1000,
  updated_at: Date.parse("2026-07-21T00:00:00.000Z") / 1000,
};

const seriesEpisode: Project = {
  ...project,
  id: "series-episode",
  title: "Series episode",
  episode_number: 1,
};

const recoveredSeriesEpisode: Project = {
  ...project,
  id: "recovered-series-episode",
  title: "Recovered series episode",
  series_id: series.id,
  episode_number: 2,
};

let consoleError: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  resetProjectWorkspaceState();
  useProjectStore.setState({
    projects: [project],
    currentProject: project,
  });
  consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  resetProjectWorkspaceState();
  consoleError.mockRestore();
  vi.clearAllMocks();
});

describe("project deletion", () => {
  it("preserves local project state when the server rejects deletion", async () => {
    const serverError = new Error("server refused deletion");
    vi.mocked(api.deleteProject).mockRejectedValueOnce(serverError);

    await expect(
      useProjectStore.getState().deleteProject(project.id),
    ).rejects.toBe(serverError);

    expect(api.deleteProject).toHaveBeenCalledWith(project.id);
    expect(useProjectStore.getState().projects).toEqual([project]);
    expect(useProjectStore.getState().currentProject).toEqual(project);
  });
});

describe("entire series deletion", () => {
  beforeEach(() => {
    useProjectStore.setState({
      projects: [project, seriesEpisode, recoveredSeriesEpisode],
      currentProject: seriesEpisode,
      selectedFrameId: "frame-from-series",
      seriesList: [series],
      currentSeries: series,
    });
  });

  it("removes the series and its episodes only after server confirmation", async () => {
    vi.mocked(api.deleteSeries).mockResolvedValueOnce({
      status: "deleted",
      deleted_episode_count: 1,
    });

    await useProjectStore.getState().deleteSeries(series.id);

    expect(api.deleteSeries).toHaveBeenCalledWith(series.id);
    expect(useProjectStore.getState().seriesList).toEqual([]);
    expect(useProjectStore.getState().currentSeries).toBeNull();
    expect(useProjectStore.getState().projects).toEqual([project]);
    expect(useProjectStore.getState().currentProject).toBeNull();
    expect(useProjectStore.getState().selectedFrameId).toBeNull();
  });

  it("preserves the entire local series when the server rejects deletion", async () => {
    const serverError = new Error("server refused series deletion");
    vi.mocked(api.deleteSeries).mockRejectedValueOnce(serverError);

    await expect(
      useProjectStore.getState().deleteSeries(series.id),
    ).rejects.toBe(serverError);

    expect(useProjectStore.getState().seriesList).toEqual([series]);
    expect(useProjectStore.getState().projects).toEqual([
      project,
      seriesEpisode,
      recoveredSeriesEpisode,
    ]);
    expect(useProjectStore.getState().currentProject).toEqual(seriesEpisode);
  });
});
