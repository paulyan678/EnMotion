import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import TaskQueuePanel from "@/components/modules/storyboard-r2v/shot-panel/TaskQueuePanel";
import type { VideoTask } from "@/lib/api";
import { renderWithIntl } from "@/test/renderWithIntl";

const failedTask: VideoTask = {
  id: "storyboard-video-failed",
  project_id: "project-1",
  frame_id: "frame-1",
  image_url: "uploads/frame.png",
  prompt: "Animate this fictional storyboard frame",
  status: "failed",
  error: "raw provider error",
  error_code: "video_generation_timeout",
  error_diagnostic: "Polling timeout after 3600 seconds",
  duration: 5,
  resolution: "720p",
  generate_audio: false,
  created_at: Math.floor(Date.now() / 1000),
  model: "doubao-seedance-2-0-fast-260128",
};

describe("Storyboard task queue terminal failures", () => {
  it("filters failed tasks, exposes diagnostics, and retries the original task", async () => {
    const onRetry = vi.fn().mockResolvedValue(undefined);
    renderWithIntl(
      <TaskQueuePanel
        open
        onClose={vi.fn()}
        tasks={[failedTask]}
        shotLabelByFrameId={{ "frame-1": "Shot 1" }}
        onJumpToShot={vi.fn()}
        onRetry={onRetry}
      />,
      { locale: "en" },
    );

    fireEvent.click(screen.getByRole("tab", { name: /Failed/ }));
    expect(screen.getByText(/did not return a result before the time limit/)).toBeInTheDocument();
    expect(screen.queryByText("raw provider error")).not.toBeInTheDocument();
    const details = screen.getByText("Technical details").closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    fireEvent.click(within(details).getByText("Technical details"));
    expect(details.open).toBe(true);
    expect(within(details).getByText(/3600 seconds/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry task" }));
    await waitFor(() => expect(onRetry).toHaveBeenCalledWith(failedTask));
  });

  it("hides an unknown English task error from the Chinese failure surface", () => {
    renderWithIntl(
      <TaskQueuePanel
        open
        onClose={vi.fn()}
        tasks={[{
          ...failedTask,
          error_code: null,
          error: "legacy provider failed unexpectedly",
          error_diagnostic: "Internal provider stack trace in English",
        }]}
        shotLabelByFrameId={{ "frame-1": "镜头 1" }}
        onJumpToShot={vi.fn()}
        onRetry={vi.fn()}
      />,
      { locale: "zh" },
    );

    fireEvent.click(screen.getByRole("tab", { name: /失败/ }));
    expect(screen.getByText(/未知错误/)).toBeInTheDocument();
    expect(screen.queryByText("legacy provider failed unexpectedly")).not.toBeInTheDocument();
    const details = screen.getByText("技术详情").closest("details") as HTMLDetailsElement;
    fireEvent.click(within(details).getByText("技术详情"));
    expect(within(details).getByText("详细诊断信息已记录，请联系管理员查看。")).toBeInTheDocument();
    expect(screen.queryByText("Internal provider stack trace in English")).not.toBeInTheDocument();
  });
});
