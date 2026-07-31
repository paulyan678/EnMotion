import { afterEach, describe, expect, it, vi } from "vitest";

import { playgroundApi } from "@/lib/api";
import { waitForPlaygroundGeneration } from "../pollGeneration";

vi.mock("@/lib/api", () => ({
  playgroundApi: {
    getGenerationStatus: vi.fn(),
    getGeneration: vi.fn(),
  },
}));

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("waitForPlaygroundGeneration", () => {
  it("polls sequentially and fetches the full record only at completion", async () => {
    vi.useFakeTimers();
    vi.mocked(playgroundApi.getGenerationStatus)
      .mockResolvedValueOnce({ id: "generation-1", status: "processing", outputs: [] })
      .mockResolvedValueOnce({ id: "generation-1", status: "completed", outputs: [] });
    vi.mocked(playgroundApi.getGeneration).mockResolvedValue({
      id: "generation-1",
      mode: "t2i",
      model_id: "gpt-image-2",
      prompt: "A moonlit harbor",
      input_media: [],
      parameters: {},
      batch_size: 1,
      outputs: [],
      status: "completed",
      created_at: "2026-07-31T00:00:00Z",
    });
    const controller = new AbortController();

    const pending = waitForPlaygroundGeneration("generation-1", {
      signal: controller.signal,
      intervalMs: 1_000,
    });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(playgroundApi.getGenerationStatus).toHaveBeenCalledTimes(1);
    expect(playgroundApi.getGeneration).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1_000);
    await expect(pending).resolves.toMatchObject({ status: "completed" });
    expect(playgroundApi.getGenerationStatus).toHaveBeenCalledTimes(2);
    expect(playgroundApi.getGeneration).toHaveBeenCalledOnce();
  });

  it("aborts without issuing another status request", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const pending = waitForPlaygroundGeneration("generation-1", {
      signal: controller.signal,
      intervalMs: 1_000,
    });

    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(5_000);
    expect(playgroundApi.getGenerationStatus).not.toHaveBeenCalled();
  });
});
