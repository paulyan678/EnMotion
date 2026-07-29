import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SeriesModelSettingsModal from "@/components/series/SeriesModelSettingsModal";
import { renderWithIntl } from "@/test/renderWithIntl";

const apiMocks = vi.hoisted(() => ({
  getSeriesModelSettings: vi.fn(),
  updateSeriesModelSettings: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMocks,
}));

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((onResolve) => {
    resolve = onResolve;
  });
  return { promise, resolve };
}

describe("SeriesModelSettingsModal", () => {
  beforeEach(() => {
    apiMocks.getSeriesModelSettings.mockReset().mockResolvedValue({});
    apiMocks.updateSeriesModelSettings.mockReset();
  });

  it("renders a stable Safari-safe loading state while model settings save", async () => {
    const pendingSave = deferred<Record<string, string>>();
    apiMocks.updateSeriesModelSettings.mockReturnValue(pendingSave.promise);
    const onClose = vi.fn();

    renderWithIntl(
      <SeriesModelSettingsModal
        isOpen
        onClose={onClose}
        seriesId="series-1"
      />,
      { locale: "en" },
    );

    fireEvent.click(
      await screen.findByRole("button", { name: /Qwen 3\.7 Max/i }),
    );
    const saveButton = await screen.findByRole("button", { name: "Save Settings" });
    fireEvent.click(saveButton);

    await waitFor(() => expect(saveButton).toHaveAttribute("aria-busy", "true"));
    expect(apiMocks.updateSeriesModelSettings).toHaveBeenCalledWith(
      "series-1",
      { chat_model: "qwen3.7-max" },
    );
    expect(saveButton.querySelector(".animate-spin")).not.toBeInTheDocument();
    expect(saveButton.querySelector('[data-loading-indicator="static"]')).toBeInTheDocument();

    await act(async () => pendingSave.resolve({ chat_model: "deepseek-v4-flash" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  });

  it("closes a no-op save without pinning inherited effective settings", async () => {
    const onClose = vi.fn();

    renderWithIntl(
      <SeriesModelSettingsModal
        isOpen
        onClose={onClose}
        seriesId="series-1"
      />,
      { locale: "en" },
    );

    expect(
      await screen.findByRole("dialog", { name: "Series Generation Settings" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    const saveButton = screen.getByRole("button", { name: "Save Settings" });
    await waitFor(() => expect(saveButton).toBeEnabled());
    fireEvent.click(saveButton);

    expect(apiMocks.updateSeriesModelSettings).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("sends null only for the explicit field reset to inheritance", async () => {
    apiMocks.getSeriesModelSettings.mockResolvedValue({
      chat_model: "qwen3.7-max",
      model_settings_overrides: ["chat_model"],
      inherited_model_settings: { chat_model: "deepseek-v4-flash" },
    });
    apiMocks.updateSeriesModelSettings.mockResolvedValue({});

    renderWithIntl(
      <SeriesModelSettingsModal
        isOpen
        onClose={vi.fn()}
        seriesId="series-1"
      />,
      { locale: "en" },
    );

    const inheritButtons = await screen.findAllByRole("button", {
      name: "Inherit",
    });
    fireEvent.click(inheritButtons[0]);
    expect(
      screen.getByRole("button", { name: /DeepSeek V4 Flash/i }),
    ).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Save Settings" }));

    await waitFor(() =>
      expect(apiMocks.updateSeriesModelSettings).toHaveBeenCalledWith(
        "series-1",
        { chat_model: null },
      ),
    );
  });

  it("cannot save defaults before the current Series provenance loads", async () => {
    const pendingLoad = deferred<Record<string, unknown>>();
    apiMocks.getSeriesModelSettings.mockReturnValue(pendingLoad.promise);
    const onClose = vi.fn();

    renderWithIntl(
      <SeriesModelSettingsModal
        isOpen
        onClose={onClose}
        seriesId="series-1"
      />,
      { locale: "en" },
    );

    const saveButton = screen.getByRole("button", { name: "Save Settings" });
    expect(saveButton).toBeDisabled();
    fireEvent.click(saveButton);
    expect(apiMocks.updateSeriesModelSettings).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => pendingLoad.resolve({
      chat_model: "deepseek-v4-flash",
      model_settings_overrides: [],
    }));
    await waitFor(() => expect(saveButton).toBeEnabled());
  });
});
