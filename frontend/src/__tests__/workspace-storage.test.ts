// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearWorkspaceStorage,
  readWorkspaceItem,
  setWorkspaceStorageScope,
  writeWorkspaceItem,
} from "@/lib/workspaceStorage";
import {
  resetPlaygroundWorkspaceState,
  usePlaygroundStore,
} from "@/components/modules/playground/usePlaygroundStore";

describe("workspace browser-cache isolation", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_SERVER_MODE", "true");
    window.localStorage.clear();
    setWorkspaceStorageScope(null);
  });

  it("never reads or writes workspace data before authentication", () => {
    window.localStorage.setItem("project-storage", "legacy desktop project");
    expect(readWorkspaceItem("project-storage")).toBeNull();
    writeWorkspaceItem("project-storage", "anonymous data");
    expect(window.localStorage.getItem("project-storage")).toBe("legacy desktop project");
  });

  it("keeps two users' cache keys completely separate", () => {
    setWorkspaceStorageScope("workspace-a");
    writeWorkspaceItem("project-storage", "Alice project");

    setWorkspaceStorageScope("workspace-b");
    expect(readWorkspaceItem("project-storage")).toBeNull();
    writeWorkspaceItem("project-storage", "Bob project");

    setWorkspaceStorageScope("workspace-a");
    expect(readWorkspaceItem("project-storage")).toBe("Alice project");
    setWorkspaceStorageScope("workspace-b");
    expect(readWorkspaceItem("project-storage")).toBe("Bob project");
  });

  it("clears one user's cache without deleting another user's cache", () => {
    setWorkspaceStorageScope("workspace-a");
    writeWorkspaceItem("enmotion:playground:featured", "Alice generation");
    setWorkspaceStorageScope("workspace-b");
    writeWorkspaceItem("enmotion:playground:featured", "Bob generation");

    clearWorkspaceStorage("workspace-a");

    expect(window.localStorage.getItem("enmotion:workspace:workspace-a:enmotion:playground:featured")).toBeNull();
    expect(window.localStorage.getItem("enmotion:workspace:workspace-b:enmotion:playground:featured"))
      .toBe("Bob generation");
  });

  it("drops in-memory generation history when the account changes", () => {
    setWorkspaceStorageScope("workspace-a");
    usePlaygroundStore.setState({
      prompt: "Alice's unreleased animation",
      history: [{ id: "alice-generation" } as never],
      activeGenerationIds: ["alice-generation"],
      isGenerating: true,
    });

    setWorkspaceStorageScope("workspace-b");
    resetPlaygroundWorkspaceState();

    const state = usePlaygroundStore.getState();
    expect(state.prompt).toBe("");
    expect(state.history).toEqual([]);
    expect(state.activeGenerationIds).toEqual([]);
    expect(state.isGenerating).toBe(false);
  });

  it("restores active generation tracking from server history", () => {
    usePlaygroundStore.getState().setHistory([
      { id: "processing-generation", status: "processing" } as never,
      { id: "completed-generation", status: "completed" } as never,
    ]);

    const state = usePlaygroundStore.getState();
    expect(state.activeGenerationIds).toEqual(["processing-generation"]);
    expect(state.isGenerating).toBe(true);
  });

  it("retains raw keys in desktop mode for backward compatibility", () => {
    vi.stubEnv("NEXT_PUBLIC_SERVER_MODE", "false");
    writeWorkspaceItem("project-storage", "desktop project");
    expect(window.localStorage.getItem("project-storage")).toBe("desktop project");
    expect(readWorkspaceItem("project-storage")).toBe("desktop project");
  });
});
