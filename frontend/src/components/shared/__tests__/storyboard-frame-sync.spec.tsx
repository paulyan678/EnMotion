import { afterEach, describe, expect, it, vi } from "vitest";

import {
  notifyStoryboardFramesChanged,
  subscribeToStoryboardFrameChanges,
} from "@/lib/storyboardFrameSync";
import { useProjectStore, type Project } from "@/store/projectStore";

const project: Project = {
  id: "episode-1",
  title: "Episode",
  originalText: "",
  characters: [],
  scenes: [],
  props: [],
  frames: [{ id: "frame-1" }],
  status: "draft",
  createdAt: "2026-07-23T00:00:00.000Z",
  updatedAt: "2026-07-23T00:00:00.000Z",
  series_id: "series-1",
};

afterEach(() => {
  useProjectStore.setState({ projects: [], currentProject: null });
});

describe("storyboard frame synchronization", () => {
  it("broadcasts canonical frame updates to mounted cross-view consumers", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToStoryboardFrameChanges(listener);
    useProjectStore.setState({ projects: [project], currentProject: project });

    useProjectStore.getState().updateProject(project.id, {
      frames: [{ id: "frame-1", rendered_image_url: "storyboard/new.png" }],
    });

    expect(listener).toHaveBeenCalledWith({
      projectId: "episode-1",
      seriesId: "series-1",
    });
    unsubscribe();
  });

  it("stops notifying a view after it unsubscribes", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToStoryboardFrameChanges(listener);
    unsubscribe();

    notifyStoryboardFramesChanged({ projectId: "episode-1", frameId: "frame-1" });

    expect(listener).not.toHaveBeenCalled();
  });
});
