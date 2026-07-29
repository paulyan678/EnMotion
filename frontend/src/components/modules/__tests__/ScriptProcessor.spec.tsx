import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ScriptProcessor from "@/components/modules/ScriptProcessor";
import { api } from "@/lib/api";
import { useProjectStore, type Project } from "@/store/projectStore";
import { useToastStore } from "@/store/toastStore";
import { renderWithIntl } from "@/test/renderWithIntl";

vi.mock("@/components/modules/PreviousEpisodeSummary", () => ({
  default: () => null,
}));

vi.mock("@/components/layout/ResizableSidePanel", () => ({
  EPISODE_EDITOR_PANEL_STORAGE_KEYS: { right: "test-right" },
  default: ({ children }: { children: React.ReactNode }) => children,
}));

function project(text: string, id = "script-project"): Project {
  return {
    id,
    title: `Persistence test ${id}`,
    originalText: text,
    original_text: text,
    characters: [],
    scenes: [],
    props: [],
    frames: [],
    video_tasks: [],
    status: "draft",
    createdAt: "2026-07-30T00:00:00.000Z",
    updatedAt: "2026-07-30T00:00:00.000Z",
  } as Project;
}

beforeEach(() => {
    const currentProject = project("Server draft");
    useProjectStore.setState({
      projects: [currentProject],
      currentProject,
      scriptDrafts: {},
    });
  useToastStore.getState().clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  useProjectStore.setState({ projects: [], currentProject: null, scriptDrafts: {} });
  useToastStore.getState().clear();
});

describe("ScriptProcessor draft persistence", () => {
  it("persists a typed draft on blur and rehydrates the saved text", async () => {
    const update = vi.spyOn(api, "updateScriptText").mockImplementation(
      async (_projectId, text) => ({
        ...project(text),
        original_text: text,
      }),
    );

    const view = renderWithIntl(<ScriptProcessor />, { locale: "en" });
    const editor = screen.getByPlaceholderText("Paste novel or script content here...");
    fireEvent.change(editor, { target: { value: "Saved after blur" } });

    // The optimistic store update must not suppress the backend write.
    expect((useProjectStore.getState().currentProject as any)?.original_text).toBe("Saved after blur");
    fireEvent.blur(editor);
    await waitFor(() => {
      expect(update).toHaveBeenCalledWith("script-project", "Saved after blur");
    });

    view.unmount();
    const reloaded = project("Saved after blur");
    useProjectStore.setState({ projects: [reloaded], currentProject: reloaded });
    renderWithIntl(<ScriptProcessor />, { locale: "en" });
    expect(screen.getByPlaceholderText("Paste novel or script content here...")).toHaveValue(
      "Saved after blur",
    );
  });

  it("keeps a failed draft dirty so a later blur retries it", async () => {
    const update = vi.spyOn(api, "updateScriptText")
      .mockRejectedValueOnce(new Error("disk unavailable"))
      .mockImplementationOnce(async (_projectId, text) => ({
        ...project(text),
        original_text: text,
      }));

    renderWithIntl(<ScriptProcessor />, { locale: "en" });
    const editor = screen.getByPlaceholderText("Paste novel or script content here...");
    fireEvent.change(editor, { target: { value: "Retry me" } });
    fireEvent.blur(editor);
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(useToastStore.getState().toasts.at(-1)?.kind).toBe("error"));

    fireEvent.blur(editor);
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
  });

  it("restores and retries a failed draft after unmounting", async () => {
    const update = vi.spyOn(api, "updateScriptText")
      .mockRejectedValueOnce(new Error("disk unavailable"))
      .mockImplementationOnce(async (_projectId, text) => ({
        ...project(text),
        original_text: text,
      }));

    const firstView = renderWithIntl(<ScriptProcessor />, { locale: "en" });
    const firstEditor = screen.getByPlaceholderText(
      "Paste novel or script content here...",
    );
    fireEvent.change(firstEditor, { target: { value: "Survive navigation" } });
    fireEvent.blur(firstEditor);
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(useProjectStore.getState().scriptDrafts["script-project"]?.text).toBe(
        "Survive navigation",
      );
    });

    firstView.unmount();
    renderWithIntl(<ScriptProcessor />, { locale: "en" });

    expect(screen.getByPlaceholderText("Paste novel or script content here...")).toHaveValue(
      "Survive navigation",
    );
    await waitFor(() => expect(update).toHaveBeenNthCalledWith(
      2,
      "script-project",
      "Survive navigation",
    ));
    await waitFor(() => {
      expect(useProjectStore.getState().scriptDrafts["script-project"]).toBeUndefined();
    });
  });

  it("drains the latest queued blur save after an in-flight write", async () => {
    let resolveFirst!: (value: Project) => void;
    const firstWrite = new Promise<Project>((resolve) => {
      resolveFirst = resolve;
    });
    const update = vi.spyOn(api, "updateScriptText")
      .mockReturnValueOnce(firstWrite)
      .mockImplementationOnce(async (_projectId, text) => ({
        ...project(text),
        original_text: text,
      }));
    renderWithIntl(<ScriptProcessor />, { locale: "en" });
    const editor = screen.getByPlaceholderText("Paste novel or script content here...");

    fireEvent.change(editor, { target: { value: "First queued value" } });
    fireEvent.blur(editor);
    expect(update).toHaveBeenCalledWith("script-project", "First queued value");

    fireEvent.change(editor, { target: { value: "Latest queued value" } });
    fireEvent.blur(editor);
    expect(update).toHaveBeenCalledTimes(1);

    resolveFirst({
      ...project("First queued value"),
      original_text: "First queued value",
    } as Project);
    await waitFor(() => expect(update).toHaveBeenNthCalledWith(
      2,
      "script-project",
      "Latest queued value",
    ));
  });

  it("automatically persists a newer queued draft after the in-flight write fails", async () => {
    let rejectFirst!: (error: Error) => void;
    const firstWrite = new Promise<Project>((_resolve, reject) => {
      rejectFirst = reject;
    });
    const update = vi.spyOn(api, "updateScriptText")
      .mockReturnValueOnce(firstWrite)
      .mockImplementationOnce(async (_projectId, text) => ({
        ...project(text),
        original_text: text,
      }));
    renderWithIntl(<ScriptProcessor />, { locale: "en" });
    const editor = screen.getByPlaceholderText("Paste novel or script content here...");

    fireEvent.change(editor, { target: { value: "First value" } });
    fireEvent.blur(editor);
    expect(update).toHaveBeenCalledWith("script-project", "First value");

    fireEvent.change(editor, { target: { value: "Newer value" } });
    fireEvent.blur(editor);
    expect(update).toHaveBeenCalledTimes(1);

    rejectFirst(new Error("first write failed"));

    await waitFor(() => expect(update).toHaveBeenNthCalledWith(
      2,
      "script-project",
      "Newer value",
    ));
    await waitFor(() => {
      expect(useProjectStore.getState().currentProject?.originalText).toBe("Newer value");
    });
  });

  it("does not let a delayed text-save snapshot overwrite newer project state", async () => {
    let resolveSave!: (value: Project) => void;
    const pendingSave = new Promise<Project>((resolve) => {
      resolveSave = resolve;
    });
    vi.spyOn(api, "updateScriptText").mockReturnValue(pendingSave);
    renderWithIntl(<ScriptProcessor />, { locale: "en" });
    const editor = screen.getByPlaceholderText("Paste novel or script content here...");

    fireEvent.change(editor, { target: { value: "Saved without replacing frames" } });
    fireEvent.blur(editor);
    await waitFor(() => expect(api.updateScriptText).toHaveBeenCalledOnce());

    const newerFrame = {
      id: "new-frame",
      scene_id: "scene-1",
      action_description: "Generated while text save was in flight",
    };
    useProjectStore.getState().updateProject("script-project", {
      frames: [newerFrame],
    } as Project);

    resolveSave({
      ...project("Saved without replacing frames"),
      frames: [],
      original_text: "Saved without replacing frames",
    } as Project);

    await waitFor(() => {
      expect(useProjectStore.getState().currentProject?.originalText).toBe(
        "Saved without replacing frames",
      );
    });
    expect(useProjectStore.getState().currentProject?.frames).toEqual([newerFrame]);
  });

  it("keeps an outgoing draft bound to project A during an immediate switch and blur", async () => {
    const projectA = project("Project A server text", "project-a");
    const projectB = project("Project B server text", "project-b");
    useProjectStore.setState({
      projects: [projectA, projectB],
      currentProject: projectA,
    });
    const update = vi.spyOn(api, "updateScriptText").mockImplementation(
      async (projectId, text) => ({
        ...project(text, projectId),
        original_text: text,
      }),
    );
    renderWithIntl(<ScriptProcessor />, { locale: "en" });
    const editor = screen.getByPlaceholderText("Paste novel or script content here...");
    fireEvent.change(editor, { target: { value: "Project A unsaved draft" } });

    useProjectStore.setState({ currentProject: projectB });
    fireEvent.blur(editor);

    await waitFor(() => expect(update).toHaveBeenCalledWith(
      "project-a",
      "Project A unsaved draft",
    ));
    expect(update).not.toHaveBeenCalledWith("project-b", "Project A unsaved draft");
    await waitFor(() => expect(editor).toHaveValue("Project B server text"));
  });
});
