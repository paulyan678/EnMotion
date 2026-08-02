import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";
import { useProjectStore, type Project } from "@/store/projectStore";

const apiMocks = vi.hoisted(() => ({
  updateProjectMetadata: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    updateProjectMetadata: apiMocks.updateProjectMetadata,
  },
}));

import ProjectCard from "../ProjectCard";

const project: Project = {
  id: "episode-gallery-1",
  title: "画廊集数",
  originalText: "",
  characters: [],
  scenes: [],
  props: [],
  frames: [],
  status: "draft",
  createdAt: "2026-07-21T00:00:00.000Z",
  updatedAt: "2026-07-21T00:00:00.000Z",
  series_id: "series-1",
  episode_number: 1,
};

describe("ProjectCard direct delete action", () => {
  beforeEach(() => {
    window.location.hash = "#/";
    apiMocks.updateProjectMetadata.mockReset();
    useProjectStore.setState({ projects: [project] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("confirms and deletes without navigating the gallery card", async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    renderWithIntl(<ProjectCard project={project} onDelete={onDelete} />);

    expect(screen.queryByRole("button", { name: "更多操作" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: `删除${project.title}` }));
    expect(screen.getByRole("alertdialog", { name: `删除“${project.title}”？` })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(project.id));
    expect(window.location.hash).toBe("#/");
  });

  it("edits episode information without opening the card", async () => {
    apiMocks.updateProjectMetadata.mockResolvedValue({
      ...project,
      title: "更新后的集数",
      description: "更新后的说明",
      script_summary: "更新后的概要",
    });
    renderWithIntl(<ProjectCard project={project} onDelete={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "编辑名称与描述" }));
    const dialog = screen.getByRole("dialog", { name: "编辑集数信息" });
    fireEvent.change(screen.getByRole("textbox", { name: "名称" }), {
      target: { value: "更新后的集数" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "描述" }), {
      target: { value: "更新后的说明" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "剧本概要" }), {
      target: { value: "更新后的概要" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(apiMocks.updateProjectMetadata).toHaveBeenCalledWith(project.id, {
      title: "更新后的集数",
      description: "更新后的说明",
      script_summary: "更新后的概要",
    }));
    expect(dialog).not.toBeInTheDocument();
    expect(window.location.hash).toBe("#/");
  });
});
