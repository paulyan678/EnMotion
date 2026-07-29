import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PromptConfigModal from "@/components/project/PromptConfigModal";
import SeriesPromptConfigModal from "@/components/series/SeriesPromptConfigModal";
import { renderWithIntl } from "@/test/renderWithIntl";

const mocks = vi.hoisted(() => ({
  getPromptConfig: vi.fn(),
  updatePromptConfig: vi.fn(),
  getSeriesPromptConfig: vi.fn(),
  updateSeriesPromptConfig: vi.fn(),
  updateProject: vi.fn(),
  currentProject: { id: "project-1" },
}));

vi.mock("@/lib/api", () => ({
  api: {
    getPromptConfig: mocks.getPromptConfig,
    updatePromptConfig: mocks.updatePromptConfig,
    getSeriesPromptConfig: mocks.getSeriesPromptConfig,
    updateSeriesPromptConfig: mocks.updateSeriesPromptConfig,
  },
}));

vi.mock("@/store/projectStore", () => ({
  useProjectStore: (selector: (state: unknown) => unknown) =>
    selector({
      currentProject: mocks.currentProject,
      updateProject: mocks.updateProject,
    }),
}));

function promptConfigResponse(storyboardPolish = "") {
  return {
    prompt_config: {
      storyboard_polish: storyboardPolish,
      video_polish: "",
      polish_model: "",
    },
    defaults: {
      storyboard_polish: "default storyboard prompt",
      video_polish: "default video prompt",
    },
  };
}

const inheritedConfigResponse = promptConfigResponse();

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((onResolve) => {
    resolve = onResolve;
  });
  return { promise, resolve };
}

describe("prompt polish model inheritance", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.currentProject.id = "project-1";
    mocks.getPromptConfig.mockResolvedValue(inheritedConfigResponse);
    mocks.getSeriesPromptConfig.mockResolvedValue(inheritedConfigResponse);
    mocks.updatePromptConfig.mockResolvedValue({
      prompt_config: inheritedConfigResponse.prompt_config,
    });
    mocks.updateSeriesPromptConfig.mockResolvedValue({
      prompt_config: inheritedConfigResponse.prompt_config,
    });
  });

  it("keeps a project polish model inherited after opening and saving", async () => {
    renderWithIntl(
      <PromptConfigModal isOpen onClose={vi.fn()} />,
      { locale: "en" },
    );

    const modelSelect = await screen.findByRole("combobox", {
      name: "Polish model",
    });
    expect(modelSelect).toHaveValue("");
    expect(
      screen.getByRole("option", {
        name: "Inherit Series / current chat model",
      }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mocks.updatePromptConfig).toHaveBeenCalledWith(
        "project-1",
        expect.objectContaining({ polish_model: "" }),
      ),
    );
  });

  it("keeps a Series polish model inherited after opening and saving", async () => {
    renderWithIntl(
      <SeriesPromptConfigModal
        isOpen
        onClose={vi.fn()}
        seriesId="series-1"
      />,
      { locale: "en" },
    );

    const modelSelect = await screen.findByRole("combobox", {
      name: "Polish model",
    });
    expect(modelSelect).toHaveValue("");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mocks.updateSeriesPromptConfig).toHaveBeenCalledWith(
        "series-1",
        expect.objectContaining({ polish_model: "" }),
      ),
    );
  });

  it("ignores a stale project response and gates save until the current project loads", async () => {
    const projectOne = deferred<ReturnType<typeof promptConfigResponse>>();
    const projectTwo = deferred<ReturnType<typeof promptConfigResponse>>();
    mocks.getPromptConfig.mockImplementation((projectId: string) =>
      projectId === "project-1" ? projectOne.promise : projectTwo.promise,
    );

    const view = renderWithIntl(
      <PromptConfigModal isOpen onClose={vi.fn()} />,
      { locale: "en" },
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    mocks.currentProject.id = "project-2";
    view.rerender(<PromptConfigModal isOpen onClose={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await act(async () => projectTwo.resolve(promptConfigResponse("project two prompt")));
    const storyboardPrompt = await screen.findByRole("textbox", {
      name: "Storyboard prompt polish",
    });
    expect(storyboardPrompt).toHaveValue("project two prompt");

    await act(async () => projectOne.resolve(promptConfigResponse("stale project one prompt")));
    expect(storyboardPrompt).toHaveValue("project two prompt");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(mocks.updatePromptConfig).toHaveBeenCalledWith(
        "project-2",
        expect.objectContaining({ storyboard_polish: "project two prompt" }),
      ),
    );
  });

  it("ignores a stale Series prompt response after switching Series", async () => {
    const seriesOne = deferred<ReturnType<typeof promptConfigResponse>>();
    const seriesTwo = deferred<ReturnType<typeof promptConfigResponse>>();
    mocks.getSeriesPromptConfig.mockImplementation((seriesId: string) =>
      seriesId === "series-1" ? seriesOne.promise : seriesTwo.promise,
    );

    const view = renderWithIntl(
      <SeriesPromptConfigModal
        isOpen
        onClose={vi.fn()}
        seriesId="series-1"
      />,
      { locale: "en" },
    );
    view.rerender(
      <SeriesPromptConfigModal
        isOpen
        onClose={vi.fn()}
        seriesId="series-2"
      />,
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await act(async () => seriesTwo.resolve(promptConfigResponse("series two prompt")));
    const storyboardPrompt = await screen.findByRole("textbox", {
      name: "Storyboard prompt polish",
    });
    await act(async () => seriesOne.resolve(promptConfigResponse("stale series one prompt")));
    expect(storyboardPrompt).toHaveValue("series two prompt");
  });
});
