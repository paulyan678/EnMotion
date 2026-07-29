import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ModelSettingsModal from "@/components/common/ModelSettingsModal";
import { renderWithIntl } from "@/test/renderWithIntl";

const mocks = vi.hoisted(() => ({
  currentProject: {
    id: "project-1",
    model_settings: {},
    model_settings_overrides: [] as string[],
    inherited_model_settings: {},
  },
  updateProject: vi.fn(),
  updateModelSettings: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    updateModelSettings: mocks.updateModelSettings,
  },
}));

vi.mock("@/store/projectStore", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/store/projectStore")>();
  return {
    ...actual,
    useProjectStore: (selector: (state: unknown) => unknown) =>
      selector({
        currentProject: mocks.currentProject,
        updateProject: mocks.updateProject,
      }),
  };
});

describe("ModelSettingsModal override ownership", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.currentProject.model_settings = {};
    mocks.currentProject.model_settings_overrides = [];
    mocks.currentProject.inherited_model_settings = {};
    mocks.updateModelSettings.mockResolvedValue({
      id: "project-1",
      model_settings: {},
      model_settings_overrides: [],
    });
  });

  it("does not persist every effective value on an unchanged save", () => {
    const onClose = vi.fn();
    renderWithIntl(
      <ModelSettingsModal isOpen onClose={onClose} />,
      { locale: "en" },
    );

    fireEvent.click(screen.getByRole("button", { name: "Save Settings" }));

    expect(mocks.updateModelSettings).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("submits only the one field explicitly selected", async () => {
    renderWithIntl(
      <ModelSettingsModal isOpen onClose={vi.fn()} />,
      { locale: "en" },
    );

    fireEvent.click(screen.getByRole("button", { name: /Qwen 3\.7 Max/i }));
    fireEvent.click(screen.getByRole("button", { name: "Save Settings" }));

    await waitFor(() =>
      expect(mocks.updateModelSettings).toHaveBeenCalledWith(
        "project-1",
        { chat_model: "qwen3.7-max" },
      ),
    );
  });

  it("uses null to reset an explicit project field to inheritance", async () => {
    mocks.currentProject.model_settings = { chat_model: "qwen3.7-max" };
    mocks.currentProject.model_settings_overrides = ["chat_model"];
    mocks.currentProject.inherited_model_settings = {
      chat_model: "deepseek-v4-flash",
    };

    renderWithIntl(
      <ModelSettingsModal isOpen onClose={vi.fn()} />,
      { locale: "en" },
    );

    fireEvent.click(screen.getAllByRole("button", { name: "Inherit" })[0]);
    expect(
      screen.getByRole("button", { name: /DeepSeek V4 Flash/i }),
    ).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Save Settings" }));

    await waitFor(() =>
      expect(mocks.updateModelSettings).toHaveBeenCalledWith(
        "project-1",
        { chat_model: null },
      ),
    );
  });
});
