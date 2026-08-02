import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import StoryboardFrameEditor from "@/components/modules/StoryboardFrameEditor";
import { api } from "@/lib/api";
import { renderWithIntl } from "@/test/renderWithIntl";
import { useProjectStore, type Project } from "@/store/projectStore";

vi.mock("@/components/common/VariantSelector", () => ({
  VariantSelector: ({
    currentImageUrl,
    onGenerate,
  }: {
    currentImageUrl?: string;
    onGenerate?: (batchSize: number) => void;
  }) => (
    <div data-testid="variant-selector" data-current-image-url={currentImageUrl}>
      <button type="button" onClick={() => onGenerate?.(3)}>×3</button>
    </div>
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

  it("previews and submits the exact edited prompt and selected model", async () => {
    const frame = {
      id: "frame-generation",
      scene_id: "scene-1",
      action_description: "The camera follows the actor.",
      image_prompt: "Original prompt",
    };
    const initialProject = projectWithFrame(frame);
    const updatedProject = projectWithFrame(frame);
    useProjectStore.setState({ currentProject: initialProject });
    const preview = vi.spyOn(api, "previewStoryboardFrame").mockResolvedValue({
      compiler_version: "1",
      compiled_request_id: "compiled-storyboard-request",
      checksum: "compiled-storyboard-checksum",
      category: "image",
      mode: "storyboard_frame",
      source: "workspace",
      user_prompt: "Edited provider prompt",
      prompt_parts: [{ kind: "user", label: "Prompt", text: "Edited provider prompt" }],
      target: { project_id: "project-frame-editor", frame_id: "frame-generation" },
      provider_requests: [{
        phase: "storyboard_frame",
        model: "gpt-image-2",
        prompt: "Edited provider prompt",
        parameters: { aspect_ratio: "3:4", batch_size: 3 },
        input_media: [],
      }],
    });
    const renderFrame = vi.spyOn(api, "renderFrame").mockResolvedValue(updatedProject);

    renderWithIntl(
      <StoryboardFrameEditor frame={frame} onClose={vi.fn()} />,
      { locale: "en" },
    );

    fireEvent.change(screen.getByPlaceholderText("Enter prompt description..."), {
      target: { value: "Edited provider prompt" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Aspect ratio" }), {
      target: { value: "3:4" },
    });
    fireEvent.click(screen.getByRole("button", { name: "×3" }));

    await waitFor(() => {
      expect(preview).toHaveBeenCalledWith("project-frame-editor", {
        frame_id: "frame-generation",
        composition_data: {
          character_ids: [],
          prop_ids: [],
          scene_id: "scene-1",
          reference_image_urls: [],
        },
        prompt: "Edited provider prompt",
        batch_size: 3,
        model_name: "gpt-image-2",
        aspect_ratio: "3:4",
      });
    });
    expect(renderFrame).toHaveBeenCalledWith(
      "project-frame-editor",
      "frame-generation",
      expect.any(Object),
      "Edited provider prompt",
      3,
      expect.objectContaining({
        modelName: "gpt-image-2",
        aspectRatio: "3:4",
        compiledRequestChecksum: "compiled-storyboard-checksum",
      }),
    );
  });
});
