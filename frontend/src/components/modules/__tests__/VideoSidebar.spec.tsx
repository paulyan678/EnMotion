import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VideoSidebar from "@/components/modules/VideoSidebar";
import { DEFAULT_I2V_MODEL_ID, VIDEO_I2V_MODELS } from "@/lib/modelCatalog";
import { useProjectStore, type Project, type VideoParams } from "@/store/projectStore";
import { renderWithIntl } from "@/test/renderWithIntl";

const params: VideoParams = {
  resolution: "720p",
  duration: 5,
  seed: undefined,
  generateAudio: true,
  batchSize: 1,
  model: DEFAULT_I2V_MODEL_ID,
  ratio: "16:9",
  watermark: false,
};

const project: Project = {
  id: "motion-project",
  title: "Motion",
  originalText: "A fictional scene",
  characters: [],
  scenes: [],
  props: [],
  frames: [],
  video_tasks: [],
  status: "draft",
  createdAt: "2026-07-23T00:00:00.000Z",
  updatedAt: "2026-07-23T00:00:00.000Z",
};

beforeEach(() => {
  useProjectStore.setState({ projects: [project], currentProject: project });
});

describe("compact Motion model selector", () => {
  it.each(["en", "zh"] as const)("shows only original model names in %s", (locale) => {
    renderWithIntl(
      <VideoSidebar
        tasks={[]}
        onRemix={vi.fn()}
        params={params}
        setParams={vi.fn()}
      />,
      { locale },
    );

    const selector = screen.getByRole("combobox", {
      name: locale === "zh" ? "模型" : "Model",
    });
    expect(selector).toBeInTheDocument();
    expect(within(selector).getAllByRole("option").map((option) => option.textContent)).toEqual(
      VIDEO_I2V_MODELS.map((model) => model.name),
    );
    for (const model of VIDEO_I2V_MODELS) {
      expect(screen.queryByText(model.description)).not.toBeInTheDocument();
    }
  });
});
