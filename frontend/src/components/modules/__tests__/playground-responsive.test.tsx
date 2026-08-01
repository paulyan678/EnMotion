import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PlaygroundPage from "@/components/modules/playground/PlaygroundPage";
import PromptHistoryDrawer from "@/components/modules/playground/PromptHistoryDrawer";
import PromptTemplateModal from "@/components/modules/playground/PromptTemplateModal";
import { usePlaygroundStore } from "@/components/modules/playground/usePlaygroundStore";
import { playgroundApi } from "@/lib/api";
import { renderWithIntl } from "@/test/renderWithIntl";
import { useToastStore } from "@/store/toastStore";

vi.mock("@/lib/api", () => ({
  API_URL: "http://127.0.0.1:17177",
  playgroundApi: {
    generate: vi.fn(),
    getHistory: vi.fn().mockResolvedValue([]),
    getTemplates: vi.fn().mockResolvedValue([]),
    getGenerationStatus: vi.fn(),
    getGeneration: vi.fn(),
    uploadMedia: vi.fn(),
    deleteUpload: vi.fn(),
  },
}));

afterEach(() => {
  usePlaygroundStore.setState({
    history: [],
    activeGenerationIds: [],
    isGenerating: false,
    prompt: "",
    negativePrompt: "",
    mode: "t2i",
    modelId: "gpt-image-2",
    inputMedia: [],
    parameters: {},
    batchSize: 1,
    queue: [],
    showHistoryDrawer: false,
    showTemplateModal: false,
    templates: [],
  });
  useToastStore.getState().clear();
  vi.clearAllMocks();
});

describe("playground responsive layout", () => {
  it("stacks the input and result panels below the desktop breakpoint", async () => {
    renderWithIntl(<PlaygroundPage />);

    const title = screen.getByRole("heading", { name: "创作台" });
    expect(title.parentElement?.previousElementSibling).toBeNull();
    expect(screen.queryByText("自由创作工作室")).not.toBeInTheDocument();
    expect(screen.queryByText("结果画廊")).not.toBeInTheDocument();
    expect(screen.queryByText("自由生成 · 不绑定项目")).not.toBeInTheDocument();
    expect(screen.queryByText(/^\d+\s+结果$/)).not.toBeInTheDocument();
    expect(screen.getByTestId("playground-split-layout")).toHaveClass(
      "flex-col",
      "overflow-y-auto",
      "md:flex-row",
      "md:overflow-hidden",
    );
    expect(screen.getByTestId("playground-input-panel")).toHaveClass(
      "w-full",
      "md:w-[420px]",
      "md:h-full",
      "md:overflow-hidden",
    );
    const composerScroll = screen.getByTestId("playground-composer-scroll");
    const generateBar = screen.getByTestId("playground-generate-bar");
    expect(composerScroll).toHaveClass("md:flex-1", "md:overflow-y-auto");
    expect(generateBar).toHaveClass("shrink-0");
    expect(generateBar).not.toHaveClass("sticky", "bottom-0");
    expect(composerScroll.nextElementSibling).toBe(generateBar);
    expect(generateBar.parentElement).toBe(screen.getByTestId("playground-input-panel"));
    expect(generateBar.contains(screen.getByRole("button", { name: "生成" }))).toBe(true);
    expect(screen.getByTestId("playground-results-panel")).toHaveClass(
      "min-h-[360px]",
      "w-full",
      "md:min-h-0",
    );

    await waitFor(() => {
      expect(playgroundApi.getHistory).toHaveBeenCalledOnce();
      expect(playgroundApi.getTemplates).toHaveBeenCalledOnce();
    });
  });

  it("resumes polling generations restored from server history", async () => {
    const processing = {
      id: "processing-generation",
      mode: "t2i",
      model_id: "gpt-image-2",
      prompt: "A luminous mountain city",
      input_media: [],
      parameters: {},
      batch_size: 1,
      outputs: [],
      status: "processing",
      created_at: "2026-07-20T15:00:00+00:00",
    };
    const completed = {
      ...processing,
      status: "completed",
      outputs: [{
        id: "output-1",
        media_path: "playground/images/result.png",
        media_type: "image",
        saved_to_library: false,
      }],
    };
    vi.mocked(playgroundApi.getHistory).mockResolvedValueOnce([processing]);
    vi.mocked(playgroundApi.getGenerationStatus).mockResolvedValue({
      id: processing.id,
      status: "completed",
      outputs: completed.outputs,
    });
    vi.mocked(playgroundApi.getGeneration).mockResolvedValue(completed);

    renderWithIntl(<PlaygroundPage />);

    await waitFor(() => {
      expect(usePlaygroundStore.getState().activeGenerationIds).toEqual([processing.id]);
    });
    await waitFor(() => {
      expect(playgroundApi.getGenerationStatus).toHaveBeenCalledWith(
        processing.id,
        { signal: expect.any(AbortSignal) },
      );
      expect(usePlaygroundStore.getState().history[0]?.status).toBe("completed");
    }, { timeout: 3500 });
  });

  it("materialises effective video defaults before dispatch", async () => {
    const modelId = "doubao-seedance-2-0-fast-260128";
    const prompt = "A camera orbit around a glowing city";
    const inputMedia = ["playground/images/city.png"];
    const effectiveParameters = {
      resolution: "720p",
      aspect_ratio: "16:9",
      duration: 5,
      generate_audio: true,
      watermark: false,
    };

    usePlaygroundStore.setState({
      mode: "i2v",
      modelId,
      prompt,
      inputMedia,
      parameters: effectiveParameters,
      queue: [],
    });
    vi.mocked(playgroundApi.generate).mockResolvedValueOnce({
      id: "failed-generation",
      mode: "i2v",
      model_id: modelId,
      prompt,
      input_media: inputMedia,
      parameters: effectiveParameters,
      batch_size: 1,
      outputs: [],
      status: "failed",
      error: "Test terminal response",
      created_at: "2026-07-21T04:00:00+00:00",
    });

    renderWithIntl(<PlaygroundPage />);

    // ParameterBar's mode/model effect has already run. Clearing the record
    // now verifies that handleGenerate itself materialises the shown defaults.
    act(() => {
      usePlaygroundStore.setState({ parameters: {} });
    });
    fireEvent.click(screen.getByRole("button", { name: "生成" }));

    await waitFor(() => {
      expect(playgroundApi.generate).toHaveBeenCalledWith(expect.objectContaining({
        mode: "i2v",
        model_id: modelId,
        prompt,
        input_media: inputMedia,
        parameters: effectiveParameters,
      }));
    });
  });

  it("visibly switches T2I reference uploads to I2I before composing the request", async () => {
    const prompt = "Turn this harbor photo into a watercolor";
    const uploadedPath = "playground/uploads/harbor.png";
    usePlaygroundStore.setState({
      mode: "t2i",
      modelId: "gpt-image-2",
      prompt,
      inputMedia: [],
      parameters: {},
      queue: [],
    });
    vi.mocked(playgroundApi.uploadMedia).mockResolvedValueOnce({ path: uploadedPath });
    vi.mocked(playgroundApi.generate).mockResolvedValueOnce({
      id: "image-edit-generation",
      mode: "i2i",
      model_id: "gpt-image-2",
      prompt,
      input_media: [uploadedPath],
      parameters: { size: "1536x1024", quality: "auto" },
      batch_size: 1,
      outputs: [],
      status: "failed",
      error: "Test terminal response",
      created_at: "2026-07-29T04:00:00Z",
    });

    const { container } = renderWithIntl(<PlaygroundPage />);
    const fileInput = container.querySelector('input[type="file"]');
    expect(fileInput).toBeInstanceOf(HTMLInputElement);
    fireEvent.change(fileInput!, {
      target: { files: [new File(["image"], "harbor.png", { type: "image/png" })] },
    });

    await waitFor(() => {
      expect(usePlaygroundStore.getState()).toMatchObject({
        mode: "i2i",
        inputMedia: [uploadedPath],
      });
    });
    fireEvent.click(screen.getByRole("button", { name: "生成" }));

    await waitFor(() => {
      expect(playgroundApi.generate).toHaveBeenCalledWith(expect.objectContaining({
        mode: "i2i",
        model_id: "gpt-image-2",
        input_media: [uploadedPath],
      }));
    });
  });

  it.each([
    ["i2i" as const, "gpt-image-2", "图生图需要至少添加一张参考图片。"],
    [
      "i2v" as const,
      "doubao-seedance-2-0-fast-260128",
      "图生视频需要且只能添加一张首帧图片。",
    ],
  ])("blocks %s generation until its required image is present", (mode, modelId, reason) => {
    usePlaygroundStore.setState({
      mode,
      modelId,
      prompt: "Animate a lighthouse in a storm",
      inputMedia: [],
      queue: [],
    });

    renderWithIntl(<PlaygroundPage />);

    const generate = screen.getByRole("button", { name: "生成" });
    expect(generate).toBeDisabled();
    expect(generate).toHaveAccessibleDescription(reason);
    expect(screen.getByText(reason)).toBeInTheDocument();
    fireEvent.click(generate);
    expect(playgroundApi.generate).not.toHaveBeenCalled();
  });

  it("hides and clears an unsupported negative prompt before dispatch", async () => {
    const prompt = "A paper-cut forest at sunrise";
    usePlaygroundStore.setState({
      mode: "t2i",
      modelId: "gpt-image-2",
      prompt,
      negativePrompt: "blurry, low contrast",
      inputMedia: [],
      queue: [],
    });
    vi.mocked(playgroundApi.generate).mockResolvedValueOnce({
      id: "image-generation",
      mode: "t2i",
      model_id: "gpt-image-2",
      prompt,
      input_media: [],
      parameters: { size: "1536x1024", quality: "auto" },
      batch_size: 1,
      outputs: [],
      status: "failed",
      error: "Test terminal response",
      created_at: "2026-07-29T04:00:00Z",
    });

    renderWithIntl(<PlaygroundPage />);

    expect(screen.queryByText("负面提示词")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(usePlaygroundStore.getState().negativePrompt).toBe("");
    });
    fireEvent.click(screen.getByRole("button", { name: "生成" }));

    await waitFor(() => {
      expect(playgroundApi.generate).toHaveBeenCalledWith(
        expect.objectContaining({
          mode: "t2i",
          model_id: "gpt-image-2",
          negative_prompt: undefined,
        }),
      );
    });
  });

  it("shows a localized error when a queued request cannot be dispatched", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    usePlaygroundStore.setState({
      mode: "t2i",
      modelId: "gpt-image-2",
      prompt: "A clockwork harbor",
      inputMedia: [],
      queue: [],
    });
    vi.mocked(playgroundApi.generate).mockRejectedValueOnce(new Error("offline"));

    renderWithIntl(<PlaygroundPage />);
    fireEvent.click(screen.getByRole("button", { name: "生成" }));

    await waitFor(() => {
      expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
        kind: "error",
        title: "生成任务启动失败，请重试。",
      });
    });
    expect(usePlaygroundStore.getState().queue).toEqual([]);
  });

  it("keeps the history drawer within narrow viewports", () => {
    usePlaygroundStore.setState({ showHistoryDrawer: true });
    renderWithIntl(<PromptHistoryDrawer />);

    expect(screen.getByTestId("playground-history-drawer")).toHaveClass(
      "w-full",
      "max-w-[420px]",
    );
    expect(screen.getByRole("button", { name: "关闭" })).toBeInTheDocument();
  });

  it("keeps the template drawer within narrow viewports", () => {
    usePlaygroundStore.setState({ showTemplateModal: true });
    renderWithIntl(<PromptTemplateModal />);

    expect(screen.getByTestId("playground-template-drawer")).toHaveClass(
      "w-full",
      "max-w-[420px]",
    );
    expect(screen.getByRole("button", { name: "关闭" })).toBeInTheDocument();
  });
});
