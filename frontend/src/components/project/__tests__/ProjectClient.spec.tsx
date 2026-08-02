import { act, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { notifyAssetLibraryChanged } from "@/lib/assetLibrarySync";
import {
  resetProjectWorkspaceState,
  useProjectStore,
  type Project,
} from "@/store/projectStore";
import { renderWithIntl } from "@/test/renderWithIntl";

const getProject = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getProject: (...args: unknown[]) => getProject(...args),
    getSeries: vi.fn(),
  },
}));

vi.mock("next/dynamic", () => ({
  default: () => () => <div data-testid="creative-canvas" />,
}));

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({ serverMode: false, user: null }),
}));

vi.mock("@/components/layout/TopBarNavigationContext", () => ({
  useTopBarNavigation: () => ({ registerNavigation: vi.fn() }),
}));

vi.mock("@/components/layout/ResizableSidePanel", () => ({
  default: ({ children }: { children: ReactNode }) => <aside>{children}</aside>,
  EPISODE_EDITOR_PANEL_STORAGE_KEYS: { left: "test-left", right: "test-right" },
}));

vi.mock("@/components/layout/PipelineSidebar", () => ({
  default: ({ headerActions }: { headerActions?: ReactNode }) => (
    <div data-testid="pipeline-sidebar">{headerActions}</div>
  ),
}));
vi.mock("@/components/layout/EpisodeMiniList", () => ({ default: () => null }));

vi.mock("@/components/modules/ScriptProcessor", () => ({ default: () => null }));
vi.mock("@/components/modules/Cast", () => ({ default: () => null }));
vi.mock("@/components/modules/VideoGenerator", () => ({ default: () => null }));
vi.mock("@/components/modules/VideoAssembly", () => ({ default: () => null }));
vi.mock("@/components/modules/ConsistencyVault", () => ({ default: () => null }));
vi.mock("@/components/modules/ArtDirection", () => ({ default: () => null }));
vi.mock("@/components/modules/StoryboardComposer", () => ({ default: () => null }));
vi.mock("@/components/modules/StoryboardR2V", () => ({ default: () => null }));

vi.mock("@/components/common/ModelSettingsModal", () => ({ default: () => null }));
vi.mock("@/components/project/PromptConfigModal", () => ({ default: () => null }));
vi.mock("@/components/modules/EntityConfirmModal", () => ({ default: () => null }));

import ProjectClient from "../ProjectClient";

const baseProject: Project = {
  id: "episode-1",
  title: "Episode",
  originalText: "",
  characters: [],
  scenes: [],
  props: [],
  frames: [],
  status: "draft",
  createdAt: "2026-07-22T00:00:00.000Z",
  updatedAt: "2026-07-22T00:00:00.000Z",
  workflow_mode: "r2v",
};

const syncedProject: Project = {
  ...baseProject,
  characters: [
    {
      id: "char_tester",
      name: "tester",
      description: "tester description",
      source: "global",
      reference_sheet: {
        selected_image_id: "img_tester",
        image_variants: [
          {
            id: "img_tester",
            url: "uploads/tester.png",
            created_at: 1_784_671_200,
          },
        ],
      },
    },
  ],
};

describe("ProjectClient asset synchronization", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetProjectWorkspaceState();
    useProjectStore.setState({
      projects: [baseProject],
      currentProject: baseProject,
    });
    getProject
      .mockResolvedValueOnce(baseProject)
      .mockResolvedValueOnce(syncedProject);
  });

  afterEach(() => {
    resetProjectWorkspaceState();
  });

  it("refetches the active canonical project after a global asset change", async () => {
    renderWithIntl(<ProjectClient id={baseProject.id} />);

    await waitFor(() => expect(getProject).toHaveBeenCalledTimes(1));
    act(() => {
      notifyAssetLibraryChanged({
        source: "global",
        assetType: "character",
        assetId: "char_tester",
      });
    });

    await waitFor(() => expect(getProject).toHaveBeenCalledTimes(2));
    await waitFor(() => {
      expect(useProjectStore.getState().currentProject?.characters).toEqual(
        syncedProject.characters,
      );
    });
    expect(getProject).toHaveBeenLastCalledWith(baseProject.id);
  });

  it("removes duplicate project prompt and model defaults", async () => {
    renderWithIntl(<ProjectClient id={baseProject.id} />, { locale: "en" });

    await waitFor(() => expect(getProject).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "API Key & OSS Configuration" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prompt configuration" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Model settings" })).not.toBeInTheDocument();
  });
});
