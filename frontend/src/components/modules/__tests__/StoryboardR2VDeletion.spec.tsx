import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import StoryboardR2V, {
    resolveStoryboardVideoModel,
} from "@/components/modules/StoryboardR2V";
import { api, crudApi } from "@/lib/api";
import { useProjectStore, type Project } from "@/store/projectStore";
import { useToastStore } from "@/store/toastStore";
import { renderWithIntl } from "@/test/renderWithIntl";

vi.mock("@/components/modules/storyboard-r2v/ShotCard", () => ({
  default: ({
    shot,
    index,
    onDelete,
    onGenerateT2I,
    isDeleting,
  }: {
    shot: {
      id: string;
      prompt: string;
      t2iImageUrl?: string;
      t2iTaskId?: string;
      t2iStatus?: string;
    };
    index: number;
    onDelete: () => void;
    onGenerateT2I: () => void;
    isDeleting?: boolean;
  }) => (
    <article data-testid={`shot-${shot.id}`}>
      <span>{index + 1}: {shot.prompt}</span>
      <span>{shot.t2iStatus ?? "idle"}</span>
      <span>{shot.t2iImageUrl ?? "no-image"}</span>
      <button
        type="button"
        aria-label={shot.id}
        aria-busy={isDeleting}
        disabled={isDeleting}
        onClick={onDelete}
      >
        ×
      </button>
      <button type="button" aria-label={`生成-${shot.id}`} onClick={onGenerateT2I}>
        生成
      </button>
    </article>
  ),
}));

const firstFrame = {
  id: "frame-delete",
  scene_id: "scene-1",
  action_description: "First frame",
  camera_movement: "static",
};
const secondFrame = {
  ...firstFrame,
  id: "frame-nearest",
  action_description: "Second frame",
};
const project: Project = {
  id: "project-r2v-delete",
  title: "R2V delete test",
  originalText: "A short scene",
  characters: [],
  scenes: [],
  props: [],
  frames: [firstFrame, secondFrame],
  video_tasks: [],
  status: "draft",
  createdAt: "2026-07-22T00:00:00.000Z",
  updatedAt: "2026-07-22T00:00:00.000Z",
};

function seedProject(selectedFrameId: string | null = firstFrame.id) {
  useProjectStore.setState({
    projects: [project],
    currentProject: project,
    selectedFrameId,
    renderingFrames: new Set([firstFrame.id]),
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  useToastStore.getState().clear();
  useProjectStore.setState({
    projects: [],
    currentProject: null,
    selectedFrameId: null,
    renderingFrames: new Set<string>(),
  });
});

describe("StoryboardR2V frame deletion", () => {
    it("prefers the live project model over a stale device cache", () => {
        expect(
            resolveStoryboardVideoModel(
                "doubao-seedance-2-0-mini-260615",
                "doubao-seedance-2-0-fast-260128",
            ),
        ).toBe("doubao-seedance-2-0-mini-260615");
    });

  it("keeps a frame when the Chinese confirmation is canceled", () => {
    seedProject();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const deleteSpy = vi.spyOn(crudApi, "deleteFrame");
    renderWithIntl(<StoryboardR2V />, { locale: "zh" });

    fireEvent.click(screen.getByRole("button", { name: "frame-delete" }));

    expect(confirmSpy).toHaveBeenCalledWith(
      "确定删除此分镜及其未被其他内容引用的生成图片、视频和生成任务吗？",
    );
    expect(deleteSpy).not.toHaveBeenCalled();
    expect(screen.getByText("1: First frame")).toBeInTheDocument();
  });

  it("waits for the server, blocks duplicates, then renumbers and selects the nearest frame", async () => {
    seedProject();
    let resolveDelete!: (value: Project) => void;
    const pendingDelete = new Promise<Project>((resolve) => {
      resolveDelete = resolve;
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const deleteSpy = vi.spyOn(crudApi, "deleteFrame").mockReturnValue(pendingDelete);
    renderWithIntl(<StoryboardR2V />, { locale: "en" });

    const deleteButton = screen.getByRole("button", { name: "frame-delete" });
    fireEvent.click(deleteButton);
    fireEvent.click(deleteButton);

    expect(deleteSpy).toHaveBeenCalledTimes(1);
    expect(deleteButton).toBeDisabled();
    expect(screen.getByText("1: First frame")).toBeInTheDocument();

    resolveDelete({ ...project, frames: [secondFrame] });
    await waitFor(() => {
      expect(screen.queryByTestId("shot-frame-delete")).not.toBeInTheDocument();
    });
    expect(screen.getByText("1: Second frame")).toBeInTheDocument();
    expect(useProjectStore.getState().currentProject?.frames).toHaveLength(1);
    expect(useProjectStore.getState().selectedFrameId).toBe(secondFrame.id);
    expect(useProjectStore.getState().renderingFrames.has(firstFrame.id)).toBe(false);
  });

  it("keeps the frame and reports a localized error when the server fails", async () => {
    seedProject();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(crudApi, "deleteFrame").mockRejectedValue({
      response: { data: { detail: "media cleanup failed" } },
    });
    renderWithIntl(<StoryboardR2V />, { locale: "zh" });

    fireEvent.click(screen.getByRole("button", { name: "frame-delete" }));

    await waitFor(() => {
      expect(useToastStore.getState().toasts.at(-1)?.title).toBe(
        "删除镜头失败",
      );
    });
    expect(useToastStore.getState().toasts.at(-1)?.title).not.toContain("media cleanup failed");
    expect(screen.getByText("1: First frame")).toBeInTheDocument();
    expect(useProjectStore.getState().currentProject?.frames).toHaveLength(2);
  });

  it("uses the completed project frame as T2I output without polling the project id as a task", async () => {
    seedProject();
    const generatedFrame = {
      ...firstFrame,
      image_url: "storyboard/generated.png",
      rendered_image_url: "storyboard/generated.png",
      rendered_image_asset: {
        selected_id: "generated-variant",
        variants: [{
          id: "generated-variant",
          url: "storyboard/generated.png",
          created_at: 1,
        }],
      },
      t2i_image_urls: ["storyboard/generated.png"],
      t2i_selected_index: 0,
      clip_start_image_id: "generated-variant",
      clip_start_image_url: "storyboard/generated.png",
    };
    const renderSpy = vi.spyOn(api, "renderFrame").mockResolvedValue({
      ...project,
      frames: [generatedFrame, secondFrame],
    });
    const taskStatusSpy = vi.spyOn(api, "getTaskStatus");
    renderWithIntl(<StoryboardR2V />, { locale: "en" });

    fireEvent.click(screen.getByRole("button", { name: "生成-frame-delete" }));

    await waitFor(() => {
      expect(screen.getByText("storyboard/generated.png")).toBeInTheDocument();
      expect(screen.getByText("completed")).toBeInTheDocument();
    });
    expect(renderSpy).toHaveBeenCalledWith(
      project.id,
      firstFrame.id,
      {},
      firstFrame.action_description,
      1,
      { signal: expect.any(AbortSignal) },
    );
    expect(taskStatusSpy).not.toHaveBeenCalled();
    expect(useProjectStore.getState().currentProject?.frames[0]).toMatchObject({
      id: firstFrame.id,
      t2i_image_urls: ["storyboard/generated.png"],
    });
  });
});
