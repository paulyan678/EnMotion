import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import StoryboardComposer from "@/components/modules/StoryboardComposer";
import { crudApi } from "@/lib/api";
import { renderWithIntl } from "@/test/renderWithIntl";
import { useProjectStore, type Project } from "@/store/projectStore";

vi.mock("@/components/modules/StoryboardFrameEditor", () => ({
  default: ({ frame }: { frame: { id: string } }) => (
    <div data-testid="frame-editor">{frame.id}</div>
  ),
}));

const project = {
  id: "project-storyboard",
  title: "Storyboard test",
  originalText: "A short scene",
  characters: [],
  scenes: [],
  props: [],
  frames: [
    {
      id: "frame-clickable",
      scene_id: "scene-1",
      action_description: "A character enters.",
      camera_movement: "static",
    },
  ],
  status: "draft",
  createdAt: "2026-07-22T00:00:00.000Z",
  updatedAt: "2026-07-22T00:00:00.000Z",
} satisfies Project;

afterEach(() => {
  vi.restoreAllMocks();
  useProjectStore.setState({
    currentProject: null,
    selectedFrameId: null,
    renderingFrames: new Set<string>(),
    isAnalyzingStoryboard: false,
  });
});

describe("StoryboardComposer frame navigation", () => {
  it("renders the exact selected storyboard variant instead of a stale legacy URL", () => {
    useProjectStore.setState({
      currentProject: {
        ...project,
        frames: [{
          ...project.frames[0],
          rendered_image_url: "storyboard/stale.png",
          rendered_image_asset: {
            selected_id: "selected",
            variants: [
              { id: "stale", url: "storyboard/stale.png", created_at: 1 },
              { id: "selected", url: "uploads/selected.webp", created_at: 2 },
            ],
          },
        }],
      },
    });

    renderWithIntl(<StoryboardComposer />, { locale: "en" });

    expect(screen.getByRole("img", { name: "Frame 1" }).getAttribute("src"))
      .toMatch(/\/files\/uploads\/selected\.webp$/);
  });

  it("opens the frame editor when the frame card is clicked", () => {
    useProjectStore.setState({
      currentProject: project,
      selectedFrameId: null,
      renderingFrames: new Set<string>(),
      isAnalyzingStoryboard: false,
    });
    renderWithIntl(<StoryboardComposer />, { locale: "zh" });

    const frameCard = screen.getByRole("button", { name: "打开第 1 帧编辑器" });
    expect(screen.getByText("静止")).toBeInTheDocument();

    fireEvent.click(frameCard);

    expect(screen.getByTestId("frame-editor")).toHaveTextContent("frame-clickable");
    expect(useProjectStore.getState().selectedFrameId).toBe("frame-clickable");
  });

  it("keeps the frame when the localized deletion confirmation is canceled", () => {
    useProjectStore.setState({ currentProject: project });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const deleteSpy = vi.spyOn(crudApi, "deleteFrame");
    renderWithIntl(<StoryboardComposer />, { locale: "zh" });

    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    expect(confirmSpy).toHaveBeenCalledWith("确定要删除此帧吗？");
    expect(deleteSpy).not.toHaveBeenCalled();
    expect(screen.getByText("A character enters.")).toBeInTheDocument();
  });

  it("waits for server confirmation, blocks duplicate requests, and selects the nearest frame", async () => {
    const secondFrame = {
      ...project.frames[0],
      id: "frame-nearest",
      action_description: "The nearest frame remains.",
    };
    const projectWithTwoFrames: Project = {
      ...project,
      frames: [project.frames[0], secondFrame],
    };
    const serverProject: Project = {
      ...projectWithTwoFrames,
      frames: [secondFrame],
    };
    let resolveDelete!: (value: Project) => void;
    const pendingDelete = new Promise<Project>((resolve) => {
      resolveDelete = resolve;
    });
    useProjectStore.setState({ currentProject: projectWithTwoFrames });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const deleteSpy = vi.spyOn(crudApi, "deleteFrame").mockReturnValue(pendingDelete);
    renderWithIntl(<StoryboardComposer />, { locale: "en" });

    fireEvent.click(screen.getByRole("button", { name: "Open frame 1 editor" }));
    const deleteButtons = screen.getAllByRole("button", { name: "Delete" });
    fireEvent.click(deleteButtons[0]);
    fireEvent.click(deleteButtons[0]);

    expect(deleteSpy).toHaveBeenCalledTimes(1);
    expect(deleteButtons[0]).toBeDisabled();
    expect(screen.getByText("A character enters.")).toBeInTheDocument();

    resolveDelete(serverProject);
    await waitFor(() => {
      expect(screen.queryByText("A character enters.")).not.toBeInTheDocument();
    });
    expect(screen.getByText("The nearest frame remains.")).toBeInTheDocument();
    expect(screen.getByTestId("frame-editor")).toHaveTextContent("frame-nearest");
    expect(useProjectStore.getState().selectedFrameId).toBe("frame-nearest");
    expect(useProjectStore.getState().currentProject?.frames).toHaveLength(1);
  });

  it("keeps the frame visible and reports a localized server error", async () => {
    useProjectStore.setState({ currentProject: project });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    vi.spyOn(crudApi, "deleteFrame").mockRejectedValue({
      response: { data: { detail: "media cleanup failed" } },
    });
    renderWithIntl(<StoryboardComposer />, { locale: "en" });

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith(
        "Failed to delete frame: media cleanup failed",
      );
    });
    expect(screen.getByText("A character enters.")).toBeInTheDocument();
    expect(useProjectStore.getState().currentProject?.frames).toHaveLength(1);
  });

  it("does not expose an English server error in the Chinese delete flow", async () => {
    useProjectStore.setState({ currentProject: project });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    vi.spyOn(crudApi, "deleteFrame").mockRejectedValue({
      response: { data: { detail: "media cleanup failed" } },
    });
    renderWithIntl(<StoryboardComposer />, { locale: "zh" });

    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith("删除帧失败");
    });
    expect(alertSpy).not.toHaveBeenCalledWith(expect.stringContaining("media cleanup failed"));
  });
});
