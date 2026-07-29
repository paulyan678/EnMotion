// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import { resolveAssetTaskPollingTarget } from "@/components/assets/useAssetEditorController";

describe("asset editor task polling", () => {
  afterEach(() => {
    window.__ENMOTION_RUNTIME_CONFIG__ = undefined;
  });

  it("uses the local task endpoint for hybrid task_id markers", () => {
    window.__ENMOTION_RUNTIME_CONFIG__ = {
      hybridMode: true,
      serverMode: false,
    };

    expect(resolveAssetTaskPollingTarget({ task_id: "hybrid-task" })).toEqual({
      kind: "local",
      taskId: "hybrid-task",
    });
  });

  it("keeps durable job polling for true server mode", () => {
    window.__ENMOTION_RUNTIME_CONFIG__ = {
      hybridMode: false,
      serverMode: true,
    };

    expect(resolveAssetTaskPollingTarget({ task_id: "server-job" })).toEqual({
      kind: "durable",
      taskId: "server-job",
    });
  });

  it("keeps legacy desktop task markers on the local task endpoint", () => {
    window.__ENMOTION_RUNTIME_CONFIG__ = {
      hybridMode: false,
      serverMode: false,
    };

    expect(resolveAssetTaskPollingTarget({ _task_id: "desktop-task" })).toEqual({
      kind: "local",
      taskId: "desktop-task",
    });
  });
});
