import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import StoryboardFrameEditor from "@/components/modules/StoryboardFrameEditor";
import { api } from "@/lib/api";
import { renderWithIntl } from "@/test/renderWithIntl";
import { useProjectStore, type Project } from "@/store/projectStore";

vi.mock("@/components/common/VariantSelector", () => ({
  VariantSelector: ({ currentImageUrl }: { currentImageUrl?: string }) => (
    <div data-testid="variant-selector" data-current-image-url={currentImageUrl} />
  ),
}));

function projectWithFrame(frame: Record<string, unknown>): Project {
  return {
    id: "project-frame-editor",
    title: "Frame editor test",
    originalText: "A short scene",
    characters: [],
    scenes: [],
    props: [],
    frames: [frame],
    status: "draft",
    createdAt: "2026-07-22T00:00:00.000Z",
    updatedAt: "2026-07-22T00:00:00.000Z",
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  useProjectStore.setState({
    currentProject: null,
    selectedFrameId: null,
  });
});

describe("StoryboardFrameEditor frame type", () => {
  it("passes the exact selected image variant to the shared frame editor", () => {
    const frame = {
      id: "frame-selected",
      scene_id: "scene-1",
      action_description: "A selected frame.",
      rendered_image_url: "storyboard/stale.png",
      rendered_image_asset: {
        selected_id: "selected",
        variants: [
          { id: "stale", url: "storyboard/stale.png" },
          { id: "selected", url: "uploads/selected.webp" },
        ],
      },
    };
    useProjectStore.setState({ currentProject: projectWithFrame(frame) });

    renderWithIntl(
      <StoryboardFrameEditor frame={frame} onClose={vi.fn()} />,
      { locale: "en" },
    );

    expect(screen.getByTestId("variant-selector")).toHaveAttribute(
      "data-current-image-url",
      "uploads/selected.webp",
    );
  });

  it("normalizes an existing Chinese type and persists a new preset", async () => {
    const frame = {
      id: "frame-1",
      scene_id: "scene-1",
      action_description: "A character crosses the room.",
      camera_movement: "静止",
    };
    const initialProject = projectWithFrame(frame);
    const updatedProject = projectWithFrame({
      ...frame,
      camera_movement: "follow",
      camera_movement_structured: {
        primary: "follow",
        speed: "normal",
      },
    });
    useProjectStore.setState({ currentProject: initialProject });
    const updateFrame = vi.spyOn(api, "updateFrame").mockResolvedValue(updatedProject);

    renderWithIntl(
      <StoryboardFrameEditor frame={frame} onClose={vi.fn()} />,
      { locale: "zh" },
    );

    const selector = screen.getByRole("combobox", { name: "分镜类型" });
    expect(selector).toHaveValue("static");

    fireEvent.change(selector, { target: { value: "follow" } });

    await waitFor(() => {
      expect(updateFrame).toHaveBeenCalledWith("project-frame-editor", "frame-1", {
        camera_movement: "follow",
      });
    });
    expect(selector).toHaveValue("follow");
    expect(screen.getByRole("option", { name: "跟拍" })).toBeInTheDocument();
  });

  it("renders English frame-type names in the English interface", () => {
    const frame = {
      id: "frame-2",
      scene_id: "scene-1",
      action_description: "The camera follows the actor.",
      camera_movement_structured: {
        primary: "follow",
        speed: "normal",
      },
    };
    useProjectStore.setState({ currentProject: projectWithFrame(frame) });

    renderWithIntl(
      <StoryboardFrameEditor frame={frame} onClose={vi.fn()} />,
      { locale: "en" },
    );

    expect(screen.getByRole("combobox", { name: "Frame type" })).toHaveValue("follow");
    expect(screen.getByRole("option", { name: "Static" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Tracking" })).toBeInTheDocument();
  });
});
