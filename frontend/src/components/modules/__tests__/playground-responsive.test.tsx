import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PlaygroundPage from "@/components/modules/playground/PlaygroundPage";
import PromptHistoryDrawer from "@/components/modules/playground/PromptHistoryDrawer";
import PromptTemplateModal from "@/components/modules/playground/PromptTemplateModal";
import { usePlaygroundStore } from "@/components/modules/playground/usePlaygroundStore";
import { playgroundApi } from "@/lib/api";
import { renderWithIntl } from "@/test/renderWithIntl";

vi.mock("@/lib/api", () => ({
  API_URL: "http://127.0.0.1:17177",
  playgroundApi: {
    generate: vi.fn(),
    getHistory: vi.fn().mockResolvedValue([]),
    getTemplates: vi.fn().mockResolvedValue([]),
    getGenerationStatus: vi.fn(),
    getGeneration: vi.fn(),
  },
}));

afterEach(() => {
  usePlaygroundStore.setState({
    history: [],
    activeGenerationIds: [],
    isGenerating: false,
    prompt: "",
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
  vi.clearAllMocks();
});

describe("playground responsive layout", () => {
  it("stacks the input and result panels below the desktop breakpoint", async () => {
    renderWithIntl(<PlaygroundPage />);

    const title = screen.getByRole("heading", { name: "创作台" });
    expect(title.parentElement?.previousElementSibling).toBeNull();
    expect(screen.queryByText("自由创作工作室")).not.toBeInTheDocument();
    expect(screen.queryByText("结果画廊")).not.toBeInTheDocument();
    expect(screen.getByTestId("playground-split-layout")).toHaveClass(
      "flex-col",
      "overflow-y-auto",
      "md:flex-row",
      "md:overflow-hidden",
    );
    expect(screen.getByTestId("playground-input-panel")).toHaveClass(
      "w-full",
      "md:w-[420px]",
      "md:overflow-y-auto",
    );
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
      expect(playgroundApi.getGenerationStatus).toHaveBeenCalledWith(processing.id);
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
