import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ApiCallsPage, { elapsedMilliseconds, formatElapsed } from "./ApiCallsPage";
import { apiCallsApi, type ApiCallActivity } from "@/lib/api";
import { renderWithIntl } from "@/test/renderWithIntl";

vi.mock("@/lib/api", () => ({
  apiCallsApi: {
    list: vi.fn(),
    cancel: vi.fn(),
    retry: vi.fn(),
    dismiss: vi.fn(),
    download: vi.fn(),
  },
}));

const running: ApiCallActivity = {
  id: "running-1",
  task_id: "running-1",
  type: "storyboard_render",
  status: "running",
  category: "image",
  source: "workspace",
  progress: 38,
  detail: "A lantern-lit alley in the rain",
  attempts: 1,
  created_at: "2026-07-21T08:00:00.000Z",
  updated_at: "2026-07-21T08:00:05.000Z",
  started_at: "2026-07-21T08:00:02.000Z",
};

const queued: ApiCallActivity = {
  ...running,
  id: "queued-1",
  task_id: "queued-1",
  type: "video",
  status: "queued",
  category: "video",
  progress: 0,
  queue_position: 2,
  started_at: null,
};

const failed: ApiCallActivity = {
  ...running,
  id: "failed-1",
  task_id: "failed-1",
  type: "playground",
  status: "failed",
  source: "playground",
  error: "Provider request timed out",
  progress: 1,
  finished_at: "2026-07-21T08:00:12.000Z",
};

const completedWithMedia: ApiCallActivity = {
  ...running,
  id: "completed-media",
  task_id: "completed-media",
  type: "video",
  status: "completed",
  category: "video",
  progress: 100,
  progress_stage: "completed",
  progress_is_estimated: false,
  source: "playground",
  source_context: {
    type: "playground",
    route: "#/playground",
    playground_generation_id: "generation-1",
  },
  model_name: "Seedance 2.0 Fast",
  prompt: "A paper boat crossing a moonlit harbor",
  parameters: { resolution: "720p", duration: 5 },
  outputs: [
    {
      id: "image-output",
      media_type: "image",
      media_path: "playground/images/harbor.png",
      filename: "harbor.png",
      mime_type: "image/png",
      size_bytes: 2048,
    },
    {
      id: "video-output",
      media_type: "video",
      media_path: "playground/videos/harbor.mp4",
      thumbnail_path: "playground/video_thumbnails/harbor.jpg",
      filename: "harbor.mp4",
      mime_type: "video/mp4",
      size_bytes: 4096,
    },
  ],
  progress_steps: [
    {
      id: "queued",
      state: "completed",
      started_at: "2026-07-21T08:00:00.000Z",
      finished_at: "2026-07-21T08:00:01.000Z",
      message: "Queued",
    },
    {
      id: "completed",
      state: "completed",
      started_at: "2026-07-21T08:00:10.000Z",
      finished_at: "2026-07-21T08:00:12.000Z",
      message: "Completed",
    },
  ],
  finished_at: "2026-07-21T08:00:12.000Z",
};

describe("API Calls dashboard", () => {
  beforeEach(() => {
    vi.mocked(apiCallsApi.list).mockResolvedValue([running, queued, failed]);
    vi.mocked(apiCallsApi.cancel).mockResolvedValue({
      ...queued,
      status: "canceled",
      error: "Canceled by user",
      queue_position: null,
      finished_at: "2026-07-21T08:00:09.000Z",
    });
    vi.mocked(apiCallsApi.dismiss).mockResolvedValue(undefined);
    vi.mocked(apiCallsApi.retry).mockResolvedValue({
      ...failed,
      status: "queued",
      error: null,
      queue_position: 3,
      started_at: null,
      finished_at: null,
    });
    vi.mocked(apiCallsApi.download).mockResolvedValue({
      blob: new Blob(["persisted output"], { type: "image/png" }),
      filename: "harbor.png",
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows aggregate activity, queue position, failures, and live request metadata", async () => {
    renderWithIntl(<ApiCallsPage />, { locale: "en" });

    const title = screen.getByRole("heading", { name: "API Calls" });
    expect(title.previousElementSibling).toBeNull();
    expect(screen.queryByText("API Activity · Live Monitor")).not.toBeInTheDocument();
    expect(await screen.findByText("Storyboard image generation")).toBeInTheDocument();
    expect(screen.getByText("Video generation")).toBeInTheDocument();
    expect(screen.getByText("Queue position 2")).toBeInTheDocument();
    expect(screen.getByText("The request failed. Check the input and try again.")).toBeInTheDocument();
    expect(screen.queryByText(/Provider request timed out/)).not.toBeInTheDocument();
    expect(screen.getAllByText("A lantern-lit alley in the rain")).toHaveLength(3);
    expect(screen.getAllByRole("tab")).toHaveLength(6);
    expect(screen.getAllByRole("tablist")).toHaveLength(1);
    expect(apiCallsApi.list).toHaveBeenCalledWith();
  });

  it("marks a newly created queued request as canceled without offering retry", async () => {
    renderWithIntl(<ApiCallsPage />, { locale: "en" });

    await screen.findByText("Video generation");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(apiCallsApi.cancel).toHaveBeenCalledWith("queued-1"));

    const card = screen.getByText("Video generation").closest("article");
    expect(card).not.toBeNull();
    expect(await within(card as HTMLElement).findByText("Canceled")).toBeInTheDocument();
    expect(within(card as HTMLElement).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("restores a canceled retry to failed so it can be retried again", async () => {
    const originalFailure: ApiCallActivity = {
      ...failed,
      id: "retryable-failure",
      task_id: "retryable-failure",
      error: "Provider request timed out",
    };
    let serverJob = originalFailure;

    vi.mocked(apiCallsApi.list).mockImplementation(async () => [serverJob]);
    vi.mocked(apiCallsApi.retry).mockImplementation(async () => {
      serverJob = {
        ...serverJob,
        status: "queued",
        error: null,
        progress: 0,
        queue_position: 1,
        started_at: null,
        finished_at: null,
      };
      return serverJob;
    });
    vi.mocked(apiCallsApi.cancel).mockImplementation(async () => {
      serverJob = {
        ...originalFailure,
        status: "failed",
        queue_position: null,
      };
      return serverJob;
    });

    renderWithIntl(<ApiCallsPage />, { locale: "en" });

    await screen.findByText("Playground generation");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(apiCallsApi.retry).toHaveBeenCalledWith("retryable-failure"));

    let card = screen.getByText("Playground generation").closest("article");
    expect(card).not.toBeNull();
    expect(await within(card as HTMLElement).findByText("Queued")).toBeInTheDocument();
    expect(within(card as HTMLElement).queryByText(/Provider request timed out/)).not.toBeInTheDocument();

    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(apiCallsApi.cancel).toHaveBeenCalledWith("retryable-failure"));

    card = screen.getByText("Playground generation").closest("article");
    expect(card).not.toBeNull();
    expect(await within(card as HTMLElement).findByText("Failed")).toBeInTheDocument();
    expect(within(card as HTMLElement).getByText("The request failed. Check the input and try again.")).toBeInTheDocument();
    expect(within(card as HTMLElement).queryByText(/Provider request timed out/)).not.toBeInTheDocument();

    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(apiCallsApi.retry).toHaveBeenCalledTimes(2));
    expect(apiCallsApi.retry).toHaveBeenNthCalledWith(2, "retryable-failure");
  });

  it("does not let an older list response overwrite a restored failure", async () => {
    const originalFailure: ApiCallActivity = {
      ...failed,
      id: "poll-race-failure",
      task_id: "poll-race-failure",
    };
    const queuedRetry: ApiCallActivity = {
      ...originalFailure,
      status: "queued",
      error: null,
      progress: 0,
      queue_position: 1,
      started_at: null,
      finished_at: null,
    };
    let resolveStaleList!: (jobs: ApiCallActivity[]) => void;
    const staleList = new Promise<ApiCallActivity[]>((resolve) => {
      resolveStaleList = resolve;
    });

    vi.mocked(apiCallsApi.list)
      .mockResolvedValueOnce([originalFailure])
      .mockReturnValueOnce(staleList);
    vi.mocked(apiCallsApi.retry).mockResolvedValue(queuedRetry);
    vi.mocked(apiCallsApi.cancel).mockResolvedValue(originalFailure);

    renderWithIntl(<ApiCallsPage />, { locale: "en" });

    await screen.findByText("Playground generation");
    const card = screen.getByText("Playground generation").closest("article");
    expect(card).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await within(card as HTMLElement).findByText("Queued")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(apiCallsApi.list).toHaveBeenCalledTimes(2));
    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Cancel" }));
    expect(await within(card as HTMLElement).findByText("Failed")).toBeInTheDocument();

    await act(async () => {
      resolveStaleList([queuedRetry]);
      await staleList;
    });

    expect(within(card as HTMLElement).getByText("Failed")).toBeInTheDocument();
    expect(within(card as HTMLElement).getByText("The request failed. Check the input and try again.")).toBeInTheDocument();
    expect(within(card as HTMLElement).queryByText(/Provider request timed out/)).not.toBeInTheDocument();
    expect(within(card as HTMLElement).getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(within(card as HTMLElement).queryByText("Queued")).not.toBeInTheDocument();
  });

  it("renders a complete Chinese interface when Chinese is selected", async () => {
    vi.mocked(apiCallsApi.list).mockResolvedValue([]);
    renderWithIntl(<ApiCallsPage />, { locale: "zh" });

    const title = await screen.findByRole("heading", { name: "接口调用" });
    expect(title.previousElementSibling).toBeNull();
    expect(screen.queryByText("接口活动 · 实时监控")).not.toBeInTheDocument();
    expect(await screen.findByText("这里还没有接口调用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新" })).toBeInTheDocument();
  });

  it("localizes provider privacy failures and hides diagnostics until expanded", async () => {
    vi.mocked(apiCallsApi.list).mockResolvedValue([{
      ...failed,
      type: "motion_reference",
      error: "Safe server fallback message",
      error_code: "input_image_privacy",
      error_diagnostic: (
        "HTTP status: 400\n" +
        "Provider code: InputImageSensitiveContentDetected.PrivacyInformation"
      ),
    }]);
    renderWithIntl(<ApiCallsPage />, { locale: "en" });

    expect(await screen.findByText(/may appear to show a real person/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open details for Motion reference generation" }));
    const summary = screen.getByText("Technical details");
    const details = summary.closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    fireEvent.click(summary);
    expect(details.open).toBe(true);
    expect(within(details).getByText(
      "Detailed diagnostics were recorded. Contact an administrator to review them.",
    )).toBeInTheDocument();
    expect(within(details).queryByText(/InputImageSensitiveContentDetected/)).not.toBeInTheDocument();
  });

  it("shows the provider privacy failure in Chinese", async () => {
    vi.mocked(apiCallsApi.list).mockResolvedValue([{
      ...failed,
      type: "motion_reference",
      error_code: "input_image_privacy",
      error_diagnostic: "HTTP status: 400",
    }]);
    renderWithIntl(<ApiCallsPage />, { locale: "zh" });

    expect(await screen.findByText(/可能看起来像真实人物/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开动态参考生成的详情" }));
    expect(screen.getByText("技术详情")).toBeInTheDocument();
  });

  it("renders persisted image and video media, posters, and individual downloads", async () => {
    vi.mocked(apiCallsApi.list).mockResolvedValue([completedWithMedia]);
    const createObjectURL = vi.fn(() => "blob:download");
    const revokeObjectURL = vi.fn();
    const NativeURL = URL;
    class DownloadURL extends NativeURL {}
    Object.assign(DownloadURL, { createObjectURL, revokeObjectURL });
    vi.stubGlobal("URL", DownloadURL);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    renderWithIntl(<ApiCallsPage />, { locale: "en" });

    expect(await screen.findByText("Seedance 2.0 Fast")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open details for Video generation" }));
    const dialog = screen.getByRole("dialog", { name: "Video generation" });
    expect(within(dialog).getByText("harbor.png")).toBeInTheDocument();
    expect(within(dialog).getByText("harbor.mp4")).toBeInTheDocument();
    const video = dialog.querySelector("video");
    expect(video).toHaveAttribute("src", expect.stringContaining("playground/videos/harbor.mp4"));
    expect(video).toHaveAttribute("poster", expect.stringContaining("playground/video_thumbnails/harbor.jpg"));

    fireEvent.click(within(dialog).getByRole("button", { name: "Download harbor.png" }));
    await waitFor(() => expect(apiCallsApi.download).toHaveBeenCalledWith("completed-media", "image-output"));
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();

    click.mockRestore();
  });

  it("shows real workflow progress, provider progress, and an indeterminate state without real data", async () => {
    vi.mocked(apiCallsApi.list).mockResolvedValue([
      {
        ...running,
        progress: 58,
        progress_stage: "provider_processing",
        progress_is_estimated: true,
        provider_progress: 50,
        progress_steps: [{
          id: "provider_processing",
          state: "active",
          started_at: "2026-07-21T08:00:02.000Z",
          finished_at: null,
          message: "Rendering",
        }],
      },
      {
        ...running,
        id: "indeterminate",
        task_id: "indeterminate",
        progress: 0,
        progress_stage: null,
      },
    ]);

    renderWithIntl(<ApiCallsPage />, { locale: "en" });

    const progress = await screen.findByRole("progressbar", { name: "Request progress" });
    expect(progress).toHaveAttribute("aria-valuenow", "58");
    expect(screen.getByText(/Estimated 58% complete/)).toBeInTheDocument();
    expect(screen.getByText(/Provider 50%/)).toBeInTheDocument();
    expect(screen.getByText("Waiting for a meaningful progress update")).toBeInTheDocument();
  });

  it("opens request details from the keyboard, shows the timeline, navigates to source, and closes with Escape", async () => {
    vi.mocked(apiCallsApi.list).mockResolvedValue([completedWithMedia]);
    window.location.hash = "#/api-calls";
    renderWithIntl(<ApiCallsPage />, { locale: "en" });

    const card = await screen.findByRole("button", { name: "Open details for Video generation" });
    fireEvent.keyDown(card, { key: "Enter" });
    const dialog = screen.getByRole("dialog", { name: "Video generation" });
    expect(within(dialog).getByText("Processing timeline")).toBeInTheDocument();
    expect(within(dialog).getByText("A paper boat crossing a moonlit harbor")).toBeInTheDocument();
    expect(within(dialog).getByText("Seedance 2.0 Fast")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Open in Playground" }));
    expect(window.location.hash).toBe("#/playground");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not open request details when a nested action handles the keyboard event", async () => {
    vi.mocked(apiCallsApi.list).mockResolvedValue([completedWithMedia]);
    renderWithIntl(<ApiCallsPage />, { locale: "en" });

    const download = await screen.findByRole("button", { name: "Download harbor.png" });
    fireEvent.keyDown(download, { key: "Enter" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps completed duration fixed while a running duration uses the live clock", () => {
    const now = Date.parse("2026-07-21T08:01:07.000Z");
    expect(formatElapsed(elapsedMilliseconds(running, now))).toBe("00:01:05");
    expect(formatElapsed(elapsedMilliseconds(failed, now))).toBe("00:00:10");
  });
});
