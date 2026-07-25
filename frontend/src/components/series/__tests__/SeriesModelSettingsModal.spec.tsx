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

    const saveButton = await screen.findByRole("button", { name: "Save Settings" });
    fireEvent.click(saveButton);

    await waitFor(() => expect(saveButton).toHaveAttribute("aria-busy", "true"));
    expect(saveButton.querySelector(".animate-spin")).not.toBeInTheDocument();
    expect(saveButton.querySelector('[data-loading-indicator="static"]')).toBeInTheDocument();

    await act(async () => pendingSave.resolve({ chat_model: "deepseek-v4-flash" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  });
});
