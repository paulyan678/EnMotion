import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VideoCreator from "@/components/modules/VideoCreator";
import { api, type VideoTask } from "@/lib/api";
import { clipImageId } from "@/lib/clipStartFrame";
import { DEFAULT_I2V_MODEL_ID } from "@/lib/modelCatalog";
import { useProjectStore, type Project, type StoryboardFrame, type VideoParams } from "@/store/projectStore";
import { useToastStore } from "@/store/toastStore";
import { renderWithIntl } from "@/test/renderWithIntl";

const params: VideoParams = {
  resolution: "720p",
  duration: 5,
  seed: 42,
  generateAudio: true,
  batchSize: 1,
  model: DEFAULT_I2V_MODEL_ID,
  ratio: "16:9",
  watermark: false,
};

function frame(index: number, withImage = true): StoryboardFrame {
  const url = `storyboard/shot-${index}.png`;
  return {
    id: `frame-${index}`,
    scene_id: `scene-${index}`,
    action_description: `Action ${index}`,
    camera_movement: index % 2 ? "跟拍" : "push_in",
    video_prompt: `Move through shot ${index}`,
    ...(withImage ? {
      rendered_image_asset: {
        selected_id: `variant-${index}`,
        variants: [{ id: `variant-${index}`, url, created_at: index }],
      },
      clip_start_image_id: `variant-${index}`,
      clip_start_image_url: url,
    } : {}),
  };
}

function project(frames: StoryboardFrame[], tasks: VideoTask[] = []): Project {
  return {
    id: "motion-project",
    title: "Motion workflow",
    originalText: "A fictional sequence",
    characters: [],
    scenes: [],
    props: [],
    frames,
    video_tasks: tasks,
    status: "draft",
    createdAt: "2026-07-23T00:00:00.000Z",
    updatedAt: "2026-07-23T00:00:00.000Z",
  };
}

function seed(frames: StoryboardFrame[], tasks: VideoTask[] = []) {
  const currentProject = project(frames, tasks);
  useProjectStore.setState({ projects: [currentProject], currentProject });
  return currentProject;
}

function renderCreator(locale: "en" | "zh" = "en") {
  return renderWithIntl(
    <VideoCreator
      onTaskCreated={(updated) => useProjectStore.getState().updateProject(updated.id, updated)}
      remixData={null}
      onRemixClear={vi.fn()}
      params={params}
    />,
    { locale },
  );
}

beforeEach(() => {
  window.__ENMOTION_RUNTIME_CONFIG__ = undefined;
  useProjectStore.setState({ projects: [], currentProject: null });
  useToastStore.getState().clear();
  vi.spyOn(api, "previewVideoTask").mockImplementation(async (_projectId, request) => ({
    compiler_version: "1.0",
    compiled_request_id: "genreq_video_creator",
    checksum: "b".repeat(64),
    category: "video",
    mode: request.generation_mode || "i2v",
    source: "workspace",
    user_prompt: request.prompt,
    prompt_parts: [{ kind: "user", label: "Prompt", text: request.prompt, editable: true }],
    target: { surface: "motion", frame_id: request.frame_id },
    provider_requests: [{
      phase: "video",
      model: request.model || DEFAULT_I2V_MODEL_ID,
      prompt: request.prompt,
      parameters: {},
      input_media: request.image_url ? [request.image_url] : [],
    }],
  }));
});

afterEach(() => {
  window.__ENMOTION_RUNTIME_CONFIG__ = undefined;
  vi.restoreAllMocks();
  useProjectStore.setState({ projects: [], currentProject: null });
  useToastStore.getState().clear();
});

describe("shot-specific Motion Creator", () => {
  it.each([1, 10, 50, 100])("renders an ordered, non-overlapping grid for %i shots", (count) => {
    seed(Array.from({ length: count }, (_, index) => frame(index + 1, index % 3 !== 0)));
    const view = renderCreator();

    const grid = screen.getByTestId("clip-start-frame-grid");
    expect(grid).toHaveClass("grid");
    expect(grid.className).toContain("auto-fill");
    expect(grid.className).not.toContain("columns-");
    const cards = screen.getAllByRole("button", { name: /Configure clip for shot/ });
    expect(cards).toHaveLength(count);
    expect(cards.map((card) => card.getAttribute("aria-label"))).toEqual(
      Array.from({ length: count }, (_, index) => `Configure clip for shot ${index + 1}`),
    );
    expect(view.container.querySelectorAll(".aspect-video")).toHaveLength(count);
    view.container.querySelectorAll('img[data-nimg="fill"]').forEach((image) => {
      expect(image.parentElement).toHaveClass("relative", "aspect-video");
    });
  });

  it("keeps no-image shots clickable and exposes no global upload or generate action", () => {
    seed([frame(1, false)]);
    renderCreator();

    expect(screen.getByText("No image")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Generate Video" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Upload" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Configure clip for shot 1" }));
    const dialog = screen.getByRole("dialog", { name: "Clip configuration" });
    expect(within(dialog).getAllByText("Upload an image for this shot.")).toHaveLength(2);
    expect(within(dialog).getByRole("button", { name: "Generate Clip" })).toBeDisabled();
  });

  it("opens the correct shot, saves an exact image variant and preserves the shot prompt", async () => {
    const first = frame(1);
    const secondUrl = "uploads/frame-1-custom.webp";
    first.t2i_image_urls = [secondUrl];
    first.clip_start_image_id = "variant-1";
    first.clip_start_image_url = "storyboard/shot-1.png";
    seed([first, frame(2)]);

    const updateSpy = vi.spyOn(api, "updateFrameWorkbench").mockImplementation(
      async (_projectId, frameId, patch) => {
        const current = useProjectStore.getState().currentProject!.frames.find(
          (item: StoryboardFrame) => item.id === frameId,
        ) as StoryboardFrame;
        return { ...current, ...patch };
      },
    );
    renderCreator();

    fireEvent.click(screen.getByRole("button", { name: "Configure clip for shot 1" }));
    const dialog = screen.getByRole("dialog", { name: "Clip configuration" });
    expect(within(dialog).getByText("Action 1")).toBeInTheDocument();
    expect(within(dialog).getByText("Tracking")).toBeInTheDocument();

    const variantButton = within(dialog)
      .getAllByRole("button", { name: "Clip start image variant" })
      .find((button) => button.getAttribute("aria-pressed") === "false");
    expect(variantButton).toBeDefined();
    fireEvent.click(variantButton!);
    await waitFor(() => expect(updateSpy).toHaveBeenCalledWith(
      "motion-project",
      "frame-1",
      expect.objectContaining({
        clip_start_image_id: clipImageId(secondUrl),
        clip_start_image_url: secondUrl,
        t2i_selected_index: 0,
      }),
    ));

    const prompt = within(dialog).getByLabelText("Action Prompt");
    fireEvent.change(prompt, { target: { value: "A slow tracking move around the fictional hero" } });
    fireEvent.blur(prompt);
    await waitFor(() => expect(updateSpy).toHaveBeenCalledWith(
      "motion-project",
      "frame-1",
      { video_prompt: "A slow tracking move around the fictional hero" },
    ));

    fireEvent.click(within(dialog).getByRole("button", { name: "Close clip configuration" }));
    fireEvent.click(screen.getByRole("button", { name: "Configure clip for shot 1" }));
    expect(screen.getByLabelText("Action Prompt")).toHaveValue(
      "A slow tracking move around the fictional hero",
    );
  });

  it("does nothing when an upload picker is canceled", () => {
    seed([frame(1, false)]);
    const uploadSpy = vi.spyOn(api, "uploadT2IFrame");
    const view = renderCreator();
    fireEvent.click(screen.getByRole("button", { name: "Configure clip for shot 1" }));

    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [] } });
    expect(uploadSpy).not.toHaveBeenCalled();
  });

  it("uploads an image only to the selected shot and immediately selects it", async () => {
    const original = frame(4, false);
    seed([original]);
    const uploadedUrl = "uploads/frame-4-upload.png";
    const uploadSpy = vi.spyOn(api, "uploadT2IFrame").mockResolvedValue({
      ...original,
      t2i_image_urls: [uploadedUrl],
      t2i_selected_index: 0,
      clip_start_image_id: clipImageId(uploadedUrl),
      clip_start_image_url: uploadedUrl,
    });
    const view = renderCreator();
    fireEvent.click(screen.getByRole("button", { name: "Configure clip for shot 1" }));

    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["image"], "start-frame.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(uploadSpy).toHaveBeenCalledWith(
      "motion-project",
      "frame-4",
      file,
    ));
    expect(useProjectStore.getState().currentProject?.frames[0]).toEqual(
      expect.objectContaining({
        clip_start_image_id: clipImageId(uploadedUrl),
        clip_start_image_url: uploadedUrl,
      }),
    );
  });

  it("treats a model-echo polish result as a successful warning", async () => {
    const selectedFrame = frame(5);
    seed([selectedFrame]);
    const polishSpy = vi.spyOn(api, "polishVideoPrompt").mockResolvedValue({
      prompt_cn: "镜头五保持原有动作",
      prompt_en: selectedFrame.video_prompt!,
      warning: "model_echo",
    });
    const updateSpy = vi.spyOn(api, "updateFrameWorkbench");
    renderCreator();

    fireEvent.click(screen.getByRole("button", { name: "Configure clip for shot 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Smart Prompt Polish" }));

    await waitFor(() => expect(polishSpy).toHaveBeenCalledWith(
      selectedFrame.video_prompt,
      "",
      "motion-project",
      "",
      ["storyboard/shot-5.png"],
    ));
    await waitFor(() => expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      kind: "warning",
      title: expect.stringContaining("model made no notable changes"),
    }));
    expect(updateSpy).not.toHaveBeenCalled();
    expect(screen.queryByText("AI polish failed")).not.toBeInTheDocument();
  });

  it("submits the exact shot, selected image, frame type, prompt, model, and parameters", async () => {
    const selectedFrame = frame(7);
    seed([selectedFrame]);
    vi.spyOn(api, "getEnvConfig").mockResolvedValue({
      secrets_configured: { NEWAPI_SEEDANCE_2_FAST_API_KEY: true },
    } as never);
    const createdTask: VideoTask = {
      id: "task-shot-7",
      project_id: "motion-project",
      frame_id: selectedFrame.id,
      source_image_id: "variant-7",
      source_image_url: "storyboard/shot-7.png",
      frame_type: "follow",
      image_url: "video_inputs/task-shot-7.png",
      prompt: selectedFrame.video_prompt!,
      status: "pending",
      duration: 5,
      seed: 42,
      resolution: "720p",
      generate_audio: true,
      created_at: 1,
      model: DEFAULT_I2V_MODEL_ID,
    };
    const createSpy = vi.spyOn(api, "createVideoTask").mockResolvedValue([createdTask]);
    renderCreator();

    fireEvent.click(screen.getByRole("button", { name: "Configure clip for shot 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Generate Clip" }));

    await waitFor(() => expect(createSpy).toHaveBeenCalledWith(
      "motion-project",
      expect.objectContaining({
        frame_id: "frame-7",
        source_image_id: "variant-7",
        image_url: "storyboard/shot-7.png",
        frame_type: "follow",
        prompt: "Move through shot 7",
        model: DEFAULT_I2V_MODEL_ID,
        duration: 5,
        resolution: "720p",
        ratio: "16:9",
        seed: 42,
        generate_audio: true,
        batch_size: 1,
        compiled_request_checksum: "b".repeat(64),
      }),
    ));
    expect(api.previewVideoTask).toHaveBeenCalledWith(
      "motion-project",
      expect.objectContaining({ frame_id: "frame-7", prompt: "Move through shot 7" }),
    );
    expect(await screen.findByText("Submitted")).toBeInTheDocument();
    expect(useProjectStore.getState().currentProject?.video_tasks).toEqual([createdTask]);
  });

  it("submits directly in hybrid mode without reading the forbidden environment endpoint", async () => {
    window.__ENMOTION_RUNTIME_CONFIG__ = {
      hybridMode: true,
      serverMode: false,
    };
    seed([frame(8)]);
    const envSpy = vi.spyOn(api, "getEnvConfig");
    const createSpy = vi.spyOn(api, "createVideoTask").mockResolvedValue([]);
    renderCreator();

    fireEvent.click(screen.getByRole("button", { name: "Configure clip for shot 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Generate Clip" }));

    await waitFor(() => expect(createSpy).toHaveBeenCalledOnce());
    expect(envSpy).not.toHaveBeenCalled();
  });

  it("does not swallow Generate Clip while an edited prompt is saving on blur", async () => {
    window.__ENMOTION_RUNTIME_CONFIG__ = {
      hybridMode: true,
      serverMode: false,
    };
    const selectedFrame = frame(8);
    seed([selectedFrame]);
    let finishPromptSave: ((value: StoryboardFrame) => void) | undefined;
    const promptSave = new Promise<StoryboardFrame>((resolve) => {
      finishPromptSave = resolve;
    });
    const updateSpy = vi.spyOn(api, "updateFrameWorkbench").mockReturnValue(promptSave);
    const createSpy = vi.spyOn(api, "createVideoTask").mockResolvedValue([]);
    renderCreator();

    fireEvent.click(screen.getByRole("button", { name: "Configure clip for shot 1" }));
    const prompt = screen.getByLabelText("Action Prompt");
    fireEvent.change(prompt, { target: { value: "A newly edited motion prompt" } });
    fireEvent.blur(prompt);

    await waitFor(() => expect(updateSpy).toHaveBeenCalledOnce());
    const generate = screen.getByRole("button", { name: "Generate Clip" });
    expect(generate).toBeEnabled();
    fireEvent.click(generate);
    expect(createSpy).not.toHaveBeenCalled();

    finishPromptSave?.({ ...selectedFrame, video_prompt: "A newly edited motion prompt" });
    await waitFor(() => expect(createSpy).toHaveBeenCalledOnce());
    expect(updateSpy).toHaveBeenCalledOnce();
    expect(createSpy).toHaveBeenCalledWith(
      "motion-project",
      expect.objectContaining({ prompt: "A newly edited motion prompt" }),
    );
  });

  it("submits a shot-scoped text-to-video task without an image input", async () => {
    window.__ENMOTION_RUNTIME_CONFIG__ = {
      hybridMode: true,
      serverMode: false,
    };
    const selectedFrame = frame(9, false);
    seed([selectedFrame]);
    const updateSpy = vi.spyOn(api, "updateFrameWorkbench").mockImplementation(
      async (_projectId, _frameId, patch) => ({ ...selectedFrame, ...patch }),
    );
    const createdTask: VideoTask = {
      id: "task-shot-9-t2v",
      project_id: "motion-project",
      frame_id: selectedFrame.id,
      image_url: "",
      prompt: selectedFrame.video_prompt!,
      status: "pending",
      duration: 5,
      seed: 42,
      resolution: "720p",
      generate_audio: true,
      created_at: 1,
      model: DEFAULT_I2V_MODEL_ID,
      generation_mode: "t2v",
      workbench_tab: "direct_r2v",
    };
    const createSpy = vi.spyOn(api, "createVideoTask").mockResolvedValue([createdTask]);
    renderCreator();

    fireEvent.click(screen.getByRole("button", { name: "Configure clip for shot 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Prompt Driven (T2V)" }));

    await waitFor(() => expect(updateSpy).toHaveBeenCalledWith(
      "motion-project",
      "frame-9",
      { workbench_tab_mode: "direct_r2v" },
    ));
    await waitFor(() => expect(screen.getByRole("button", { name: "Generate Clip" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Generate Clip" }));

    await waitFor(() => expect(createSpy).toHaveBeenCalledOnce());
    const payload = createSpy.mock.calls[0][1];
    expect(payload).toMatchObject({
      frame_id: "frame-9",
      frame_type: "follow",
      prompt: "Move through shot 9",
      model: DEFAULT_I2V_MODEL_ID,
      generation_mode: "t2v",
      workbench_tab: "direct_r2v",
    });
    expect(payload).not.toHaveProperty("image_url");
    expect(payload).not.toHaveProperty("source_image_id");
  });

  it("uses localized Clip Start Frame and shot controls in Chinese", () => {
    seed([frame(1)]);
    renderCreator("zh");

    expect(screen.getByRole("heading", { name: "片段首帧" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "配置镜头 1 的片段" }));
    expect(screen.getByRole("dialog", { name: "片段配置" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成片段" })).toBeInTheDocument();
  });

  it("retries a failed clip from the same shot using the persisted server recipe", async () => {
    const selectedFrame = frame(3);
    const failedTask: VideoTask = {
      id: "failed-shot-3",
      project_id: "motion-project",
      frame_id: selectedFrame.id,
      source_image_id: "variant-3",
      source_image_url: "storyboard/shot-3.png",
      frame_type: "follow",
      image_url: "video_inputs/failed-shot-3.png",
      prompt: selectedFrame.video_prompt!,
      status: "failed",
      error: "provider failed",
      duration: 5,
      resolution: "720p",
      generate_audio: true,
      created_at: 1,
      model: DEFAULT_I2V_MODEL_ID,
    };
    seed([selectedFrame], [failedTask]);
    const retrySpy = vi.spyOn(api, "retryVideoTask").mockResolvedValue({
      ...failedTask,
      status: "pending",
      error: null,
    });
    renderCreator();

    fireEvent.click(screen.getByRole("button", { name: "Configure clip for shot 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Retry Task" }));

    await waitFor(() => expect(retrySpy).toHaveBeenCalledWith("motion-project", failedTask.id));
    expect(useProjectStore.getState().currentProject?.video_tasks?.[0]).toEqual(
      expect.objectContaining({ id: failedTask.id, status: "pending", frame_id: selectedFrame.id }),
    );
  });
});
