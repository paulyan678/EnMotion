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

const failedBilling: ApiCallActivity = {
  id: "billing:usage-failed",
  task_id: "usage-failed",
  type: "images.generations",
  status: "failed",
  category: "image",
  source: "workspace",
  progress: 0,
  attempts: 1,
  created_at: "2026-07-21T08:00:00.000Z",
  updated_at: "2026-07-21T08:00:05.000Z",
  finished_at: "2026-07-21T08:00:05.000Z",
  managed_read_only: true,
  activity_kind: "billing",
  billing_status: "failed",
};

const canceledBilling: ApiCallActivity = {
  ...failedBilling,
  id: "billing:usage-canceled",
  task_id: "usage-canceled",
  status: "canceled",
  billing_status: "cancelled",
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
    expect(screen.queryByText("Live updates")).not.toBeInTheDocument();
    expect(screen.queryByText("Track generation requests from the Playground, Workspace, and other tools in one place.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Refresh" })).not.toBeInTheDocument();
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

    fireEvent(document, new Event("visibilitychange"));
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
    expect(title).toHaveAttribute("data-global-page-title");
    expect(title).toHaveClass("text-[1.625rem]", "md:text-[2.125rem]");
    expect(screen.queryByText("接口活动 · 实时监控")).not.toBeInTheDocument();
    expect(screen.queryByText("集中查看创作台、工作区及其他功能发起的所有生成请求。")).not.toBeInTheDocument();
    expect(screen.queryByText("实时更新")).not.toBeInTheDocument();
    expect(await screen.findByText("这里还没有接口调用")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "刷新" })).not.toBeInTheDocument();
    const tabs = screen.getByRole("tablist", { name: "按状态筛选接口调用" });
    expect(tabs).toHaveClass("atelier-pill-tabs", "bg-surface-inset", "rounded-full");
    expect(screen.getByRole("tab", { name: "全部 0" })).toHaveClass(
      "atelier-pill-tab-active",
      "bg-surface",
    );
  });

  it("shows the generated asset name in local hybrid activity", async () => {
    vi.mocked(apiCallsApi.list).mockResolvedValue([{
      ...running,
      id: "hybrid:asset-task",
      task_id: "asset-task",
      type: "series_asset",
      detail: "守塔人",
      prompt: "全身角色设定图",
      model_name: "gpt-image-2",
    }]);
    renderWithIntl(<ApiCallsPage />, { locale: "zh" });

    expect(await screen.findByText("守塔人")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开系列资产生成的详情" }));
    const dialog = screen.getByRole("dialog", { name: "系列资产生成" });
    expect(within(dialog).getByText("生成项目")).toBeInTheDocument();
    expect(within(dialog).getByText("守塔人")).toBeInTheDocument();
    expect(within(dialog).getByText("全身角色设定图")).toBeInTheDocument();
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

  it("shows a failed billing outcome without presenting it as a generation failure", async () => {
    vi.mocked(apiCallsApi.list).mockResolvedValue([failedBilling]);
    renderWithIntl(<ApiCallsPage />, { locale: "en" });

    const card = await screen.findByRole("button", { name: "Open details for Image generation" });
    expect(within(card).getByText("Billing record")).toBeInTheDocument();
    expect(within(card).getByText("Billing outcome: Failed")).toBeInTheDocument();
    expect(within(card).getByText("Failed")).toBeInTheDocument();
    expect(within(card).queryByText("Succeeded")).not.toBeInTheDocument();
    expect(within(card).queryByText("The request failed. Check the input and try again.")).not.toBeInTheDocument();

    fireEvent.click(card);
    const dialog = screen.getByRole("dialog", { name: "Image generation" });
    expect(within(dialog).getByText("Billing outcome")).toBeInTheDocument();
    expect(within(dialog).getByText(
      "This account record tracks credit settlement, not generation progress or outputs.",
    )).toBeInTheDocument();
    expect(within(dialog).queryByText("Failure reason:")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Processing timeline")).not.toBeInTheDocument();
  });

  it("shows a canceled billing outcome in Chinese on both card and detail", async () => {
    vi.mocked(apiCallsApi.list).mockResolvedValue([canceledBilling]);
    renderWithIntl(<ApiCallsPage />, { locale: "zh" });

    const card = await screen.findByRole("button", { name: "打开图像生成的详情" });
    expect(within(card).getByText("账务记录")).toBeInTheDocument();
    expect(within(card).getByText("账务结果：已取消")).toBeInTheDocument();
    expect(within(card).getByText("已取消")).toBeInTheDocument();
    expect(within(card).queryByText("已成功")).not.toBeInTheDocument();

    fireEvent.click(card);
    const dialog = screen.getByRole("dialog", { name: "图像生成" });
    expect(within(dialog).getByText("账务结果")).toBeInTheDocument();
    expect(within(dialog).getByText(
      "此账户记录仅反映额度结算，不代表生成进度或生成结果。",
    )).toBeInTheDocument();
    expect(within(dialog).queryByText("处理时间线")).not.toBeInTheDocument();
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
    const overlay = screen.getByTestId("api-call-detail-overlay");
    expect(overlay.parentElement).toBe(document.body);
    expect(overlay).toHaveClass("z-[220]");
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
