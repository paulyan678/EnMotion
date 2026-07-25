// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import {
  confirmDesktopUiReady,
  type EnMotionUpdaterBridge,
  type UpdaterState,
} from "@/lib/updater";

vi.mock("@/lib/api", () => ({
  api: {
    healthCheck: vi.fn(),
  },
}));

function updaterBridge(confirmUiReady: () => Promise<void>): EnMotionUpdaterBridge {
  const idleState: UpdaterState = {
    status: "idle",
    currentVersion: "1.0.0",
  };
  return {
    getState: vi.fn(async () => idleState),
    checkForUpdates: vi.fn(async () => idleState),
    startUpdate: vi.fn(async () => idleState),
    installAndRestart: vi.fn(async () => idleState),
    confirmUiReady,
    subscribe: vi.fn(async () => () => undefined),
  };
}

describe("desktop update health confirmation", () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, "enmotionUpdater");
    vi.mocked(api.healthCheck).mockReset();
  });

  it("requires application health before committing native update health", async () => {
    const nativeConfirmation = vi.fn(async () => undefined);
    window.enmotionUpdater = updaterBridge(nativeConfirmation);
    vi.mocked(api.healthCheck).mockResolvedValue({
      ok: false,
      time: Date.now(),
      log_file: "",
      log_dir: "",
      studio_projects: 0,
    });

    await expect(confirmDesktopUiReady()).rejects.toThrow("本地服务未通过健康检查");
    expect(nativeConfirmation).not.toHaveBeenCalled();
  });

  it("retries after a transient native confirmation failure", async () => {
    const nativeConfirmation = vi.fn()
      .mockRejectedValueOnce(new Error("sidecar still starting"))
      .mockResolvedValueOnce(undefined);
    window.enmotionUpdater = updaterBridge(nativeConfirmation);
    vi.mocked(api.healthCheck).mockResolvedValue({
      ok: true,
      time: Date.now(),
      log_file: "",
      log_dir: "",
      studio_projects: 0,
    });

    await expect(confirmDesktopUiReady()).rejects.toThrow("sidecar still starting");
    await expect(confirmDesktopUiReady()).resolves.toBeUndefined();
    expect(api.healthCheck).toHaveBeenCalledTimes(2);
    expect(nativeConfirmation).toHaveBeenCalledTimes(2);
  });
});
