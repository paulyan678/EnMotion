import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import VideoQueue from "@/components/modules/VideoQueue";
import type { VideoTask } from "@/lib/api";
import { renderWithIntl } from "@/test/renderWithIntl";

const baseTask: VideoTask = {
  id: "video-failed-123456",
  project_id: "project-1",
  image_url: "uploads/input.png",
  prompt: "A fictional hero crossing a stormy bridge",
  status: "failed",
  duration: 5,
  resolution: "720p",
  generate_audio: false,
  created_at: 1,
  model: "doubao-seedance-2-0-fast-260128",
};

describe("VideoQueue terminal states", () => {
  it("shows failed tasks with localized copy, diagnostics, and persisted retry", async () => {
    let finishRetry!: () => void;
    const onRetry = vi.fn(
      () => new Promise<void>((resolve) => { finishRetry = resolve; }),
    );
    const failedTask: VideoTask = {
      ...baseTask,
      error: "raw worker error",
      error_code: "video_generation_timeout",
      error_diagnostic: "Provider polling did not finish within 3600 seconds",
    };

    renderWithIntl(
      <VideoQueue
        tasks={[failedTask]}
        onRemix={vi.fn()}
        onRetry={onRetry}
        onDelete={vi.fn()}
      />,
      { locale: "en" },
    );

    expect(screen.getByRole("button", { name: "Failed" })).toBeInTheDocument();
    expect(screen.getByText("Generation Failed")).toBeInTheDocument();
    expect(screen.getByText(/did not return a result before the time limit/)).toBeInTheDocument();
    expect(screen.queryByText("raw worker error")).not.toBeInTheDocument();
    const summary = screen.getByText("Technical details");
    const details = summary.closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    fireEvent.click(summary);
    expect(details.open).toBe(true);
    expect(within(details).getByText(/3600 seconds/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry Task" }));
    expect(onRetry).toHaveBeenCalledWith(failedTask);
    expect(await screen.findByText("Retrying…")).toBeInTheDocument();
    await act(async () => finishRetry());
    await waitFor(() => expect(screen.getByText("Retry Task")).toBeInTheDocument());
  });

  it("keeps canceled tasks separate from failures and active loading states", async () => {
    const queued: VideoTask = {
      ...baseTask,
      id: "video-queued",
      prompt: "queued prompt",
      status: "pending",
    };
    const canceled: VideoTask = {
      ...baseTask,
      id: "video-canceled",
      status: "canceled",
      error_code: "video_generation_canceled",
    };

    renderWithIntl(
      <VideoQueue
        tasks={[queued, canceled]}
        onRemix={vi.fn()}
        onRetry={vi.fn()}
        onDelete={vi.fn()}
      />,
      { locale: "en" },
    );

    expect(screen.getByText("queued prompt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Canceled" }));
    await waitFor(() => expect(screen.queryByText("queued prompt")).not.toBeInTheDocument());
    expect(screen.getByText("This task was canceled before generation started.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry Task" })).not.toBeInTheDocument();
  });

  it("keeps queued and processing filters distinct and shows the shot association", async () => {
    const queued: VideoTask = {
      ...baseTask,
      id: "video-queued",
      frame_id: "frame-2",
      frame_type: "push_in",
      prompt: "queued shot prompt",
      status: "pending",
    };
    const processing: VideoTask = {
      ...baseTask,
      id: "video-processing",
      frame_id: "frame-3",
      frame_type: "follow",
      prompt: "processing shot prompt",
      status: "processing",
    };

    renderWithIntl(
      <VideoQueue
        tasks={[queued, processing]}
        onRemix={vi.fn()}
        onRetry={vi.fn()}
        onDelete={vi.fn()}
        shotNumberByFrameId={{ "frame-2": 2, "frame-3": 3 }}
      />,
      { locale: "en" },
    );

    expect(screen.getByText("Shot 2")).toBeInTheDocument();
    expect(screen.getByText("Push in")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Queued" }));
    await waitFor(() => expect(screen.queryByText("processing shot prompt")).not.toBeInTheDocument());
    expect(screen.getByText("queued shot prompt")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Processing" }));
    await waitFor(() => expect(screen.queryByText("queued shot prompt")).not.toBeInTheDocument());
    expect(screen.getByText("processing shot prompt")).toBeInTheDocument();
    expect(screen.getByText("Shot 3")).toBeInTheDocument();
    expect(screen.getByText("Tracking")).toBeInTheDocument();
  });

  it("renders failure and retry controls in Chinese", () => {
    renderWithIntl(
      <VideoQueue
        tasks={[{
          ...baseTask,
          error_code: "video_generation_failed",
          error_diagnostic: "Provider worker exited with an internal error",
        }]}
        onRemix={vi.fn()}
        onRetry={vi.fn()}
        onDelete={vi.fn()}
      />,
      { locale: "zh" },
    );

    expect(screen.getByText("生成失败")).toBeInTheDocument();
    expect(screen.getByText(/请查看技术详情后重试/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试任务" })).toBeInTheDocument();
    const details = screen.getByText("技术详情").closest("details") as HTMLDetailsElement;
    fireEvent.click(within(details).getByText("技术详情"));
    expect(within(details).getByText("详细诊断信息已记录，请联系管理员查看。")).toBeInTheDocument();
    expect(screen.queryByText("Provider worker exited with an internal error")).not.toBeInTheDocument();
  });

  it("hides an unknown English task error from the Chinese failure surface", () => {
    renderWithIntl(
      <VideoQueue
        tasks={[{ ...baseTask, error: "legacy worker crashed unexpectedly" }]}
        onRemix={vi.fn()}
        onRetry={vi.fn()}
        onDelete={vi.fn()}
      />,
      { locale: "zh" },
    );

    expect(screen.getByText("未知错误，请重试")).toBeInTheDocument();
    expect(screen.queryByText("legacy worker crashed unexpectedly")).not.toBeInTheDocument();
  });
});
