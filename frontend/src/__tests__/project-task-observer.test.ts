// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import {
  observeProjectTasks,
  resetProjectTaskObserversForTests,
} from "@/lib/projectTaskObserver";
import type { Project } from "@/store/projectStore";

function setVisibility(value: DocumentVisibilityState): void {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("project task observer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility("visible");
    resetProjectTaskObserversForTests();
  });

  afterEach(() => {
    resetProjectTaskObserversForTests();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("shares one request between subscribers and pauses while hidden", async () => {
    const project = { id: "project-1", video_tasks: [] } as unknown as Project;
    const getProject = vi.spyOn(api, "getProject").mockResolvedValue(project);
    const first = vi.fn();
    const second = vi.fn();

    const stopFirst = observeProjectTasks("project-1", { onProject: first });
    const stopSecond = observeProjectTasks("project-1", { onProject: second });
    await vi.advanceTimersByTimeAsync(0);

    expect(getProject).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledWith(project);
    expect(second).toHaveBeenCalledWith(project);

    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(30_000);
    expect(getProject).toHaveBeenCalledTimes(1);

    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(getProject).toHaveBeenCalledTimes(2);

    stopFirst();
    stopSecond();
  });
});
