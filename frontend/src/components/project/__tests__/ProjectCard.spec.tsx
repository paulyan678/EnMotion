import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";
import type { Project } from "@/store/projectStore";
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
});
