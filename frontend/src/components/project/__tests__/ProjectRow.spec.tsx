import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";
import type { Project } from "@/store/projectStore";
import ProjectRow from "../ProjectRow";

const project: Project = {
  id: "episode-1",
  title: "列表集数",
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

describe("ProjectRow direct delete action", () => {
  beforeEach(() => {
    window.location.hash = "#/";
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens an episode in series context when the row is clicked", () => {
    renderWithIntl(
      <ProjectRow project={project} crumb="系列 · EP.01" onDelete={vi.fn()} />,
    );

    const row = screen.getByText(project.title).closest('[role="button"]');
    expect(row).not.toBeNull();
    fireEvent.click(row!);

    expect(window.location.hash).toBe("#/series/series-1/episode/episode-1");
  });

  it("confirms and invokes the server-backed delete action", async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    renderWithIntl(
      <ProjectRow project={project} crumb="系列 · EP.01" onDelete={onDelete} />,
    );

    expect(screen.queryByRole("button", { name: "更多操作" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: `删除${project.title}` }));
    expect(screen.getByRole("alertdialog", { name: `删除“${project.title}”？` })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(project.id));
    expect(window.location.hash).toBe("#/");
  });

  it("keeps the row when deletion is cancelled", () => {
    const onDelete = vi.fn();
    renderWithIntl(
      <ProjectRow project={project} crumb="系列 · EP.01" onDelete={onDelete} />,
    );

    fireEvent.click(screen.getByRole("button", { name: `删除${project.title}` }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(window.location.hash).toBe("#/");
  });
});
