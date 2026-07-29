import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { saveAuthenticatedMedia } from "@/lib/download";
import {
  useProjectStore,
  type Project,
  type StoryboardFrame,
  type VideoTask,
} from "@/store/projectStore";
import { renderWithIntl } from "@/test/renderWithIntl";

vi.mock("@/components/layout/ResizableSidePanel", () => ({
  default: ({ children }: PropsWithChildren) => <aside>{children}</aside>,
  EPISODE_EDITOR_PANEL_STORAGE_KEYS: { right: "test-right" },
}));

vi.mock("@/lib/download", () => ({
  saveAuthenticatedMedia: vi.fn().mockResolvedValue(undefined),
}));

import VideoAssembly from "../VideoAssembly";

const take: VideoTask = {
  id: "take-1",
  project_id: "assembly-project",
  frame_id: "frame-1",
  image_url: "storyboard/frame-1.png",
  prompt: "Move through the shot",
  status: "completed",
  video_url: "video/take-1.mp4",
  duration: 5,
  created_at: 1,
  model: "doubao-seedance-2-0-fast-260128",
};

const takeB: VideoTask = {
  ...take,
  id: "take-2",
  video_url: "video/take-2.mp4",
  created_at: 2,
};

function frame(overrides: Partial<StoryboardFrame> = {}): StoryboardFrame {
  return {
    id: "frame-1",
    scene_id: "scene-1",
    action_description: "Shot action",
    selected_video_id: take.id,
    audio_url: "audio/dialogue.mp3",
    ...overrides,
  };
}

function project(
  frameOverrides: Partial<StoryboardFrame> = {},
  projectOverrides: Partial<Project> = {},
): Project {
  return {
    id: "assembly-project",
    title: "Assembly Test",
    originalText: "A test sequence",
    characters: [],
    scenes: [],
    props: [],
    frames: [frame(frameOverrides)],
    video_tasks: [take],
    status: "draft",
    createdAt: "2026-07-30T00:00:00.000Z",
    updatedAt: "2026-07-30T00:00:00.000Z",
    ...projectOverrides,
  };
}

function seed(value: Project) {
  useProjectStore.setState({
    projects: [value],
    currentProject: value,
    runningOps: {},
  });
}

beforeEach(() => {
  vi.mocked(saveAuthenticatedMedia).mockClear();
  vi.stubGlobal("alert", vi.fn());
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  useProjectStore.setState({
    projects: [],
    currentProject: null,
    runningOps: {},
  });
});

describe("VideoAssembly dubbing and export", () => {
  it("does not imply dubbing is available when the selected frame has no audio", () => {
    seed(project({ audio_url: null }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });

    const frameCard = screen.getByRole("button", { name: "Frame 1" });
    fireEvent.keyDown(frameCard, { key: "Enter" });

    expect(screen.queryByRole("button", { name: "Preview dub" })).not.toBeInTheDocument();
    expect(screen.queryByText("Dialogue audio ready")).not.toBeInTheDocument();
    expect(frameCard).toHaveAttribute("aria-pressed", "true");
  });

  it("never autoplays a merged video hidden behind another phase", () => {
    seed(project({}, { merged_video_url: "video/merged.mp4" }));
    const view = renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Export" }));

    const mergedVideo = view.container.querySelector(
      'video[src*="video/merged.mp4"]',
    );
    expect(mergedVideo).not.toHaveAttribute("autoplay");

    fireEvent.click(screen.getByRole("button", { name: "Takes" }));
    expect(mergedVideo?.closest(".hidden")).not.toBeNull();
  });

  it("runs preview, apply, and revert as one-flight actions with visible media status", async () => {
    const previewed = project({
      preview_video_url: "video/preview-take-1.mp4",
      preview_video_task_id: take.id,
    });
    let resolvePreview!: (value: Project) => void;
    const previewPromise = new Promise<Project>((resolve) => {
      resolvePreview = resolve;
    });
    const previewSpy = vi.spyOn(api, "previewDub").mockReturnValue(previewPromise);
    const applySpy = vi.spyOn(api, "applyDub").mockResolvedValue(project({
      dubbed_video_url: "video/dubbed-take-1.mp4",
      dubbed_video_task_id: take.id,
      preview_video_url: null,
    }));
    const revertSpy = vi.spyOn(api, "revertDub").mockResolvedValue(project({
      dubbed_video_url: null,
      dubbed_video_task_id: null,
      preview_video_url: null,
    }));
    seed(project());
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByText("Shot action"));

    const previewButton = screen.getByRole("button", { name: "Preview dub" });
    fireEvent.click(previewButton);
    fireEvent.click(previewButton);
    expect(previewSpy).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Apply dub" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Revert" })).toBeDisabled();

    resolvePreview(previewed);
    expect(await screen.findByText("Dubbing preview ready")).toBeInTheDocument();
    expect(screen.getByTestId("take-media-take-1")).toHaveAttribute(
      "src",
      expect.stringContaining("/files/video/preview-take-1.mp4"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Apply dub" }));
    await waitFor(() => expect(applySpy).toHaveBeenCalledWith("assembly-project", "frame-1"));
    expect(await screen.findByText("Dub applied")).toBeInTheDocument();
    expect(screen.getByTestId("take-media-take-1")).toHaveAttribute(
      "src",
      expect.stringContaining("/files/video/dubbed-take-1.mp4"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Revert" }));
    await waitFor(() => expect(revertSpy).toHaveBeenCalledWith("assembly-project", "frame-1"));
    expect(await screen.findByText("Dialogue audio ready")).toBeInTheDocument();
    expect(screen.getByTestId("take-media-take-1")).toHaveAttribute(
      "src",
      expect.stringContaining("/files/video/take-1.mp4"),
    );
  });

  it("allows an applied dub to be reverted after dialogue audio is removed", async () => {
    const revertSpy = vi.spyOn(api, "revertDub").mockResolvedValue(project({
      audio_url: null,
      dubbed_video_url: null,
      dubbed_video_task_id: null,
    }));
    seed(project({
      audio_url: null,
      dubbed_video_url: "video/dubbed-take-1.mp4",
      dubbed_video_task_id: take.id,
    }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Frame 1" }));

    expect(screen.getByRole("button", { name: "Preview dub" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Apply dub" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Revert" }));

    await waitFor(() => {
      expect(revertSpy).toHaveBeenCalledWith("assembly-project", "frame-1");
    });
  });

  it("keeps applied-take and preview-take provenance separate before Apply", async () => {
    const selectedB = project({
      selected_video_id: takeB.id,
      dubbed_video_url: "video/dubbed-take-1.mp4",
      dubbed_video_task_id: take.id,
    }, {
      video_tasks: [take, takeB],
    });
    const previewedB = project({
      selected_video_id: takeB.id,
      dubbed_video_url: "video/dubbed-take-1.mp4",
      dubbed_video_task_id: take.id,
      preview_video_url: "video/preview-take-2.mp4",
      preview_video_task_id: takeB.id,
    }, {
      video_tasks: [take, takeB],
    });
    vi.spyOn(api, "selectVideo").mockResolvedValue(selectedB);
    const previewSpy = vi.spyOn(api, "previewDub").mockResolvedValue(previewedB);
    seed(project({
      dubbed_video_url: "video/dubbed-take-1.mp4",
      dubbed_video_task_id: take.id,
    }, {
      video_tasks: [take, takeB],
    }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Frame 1" }));

    fireEvent.click(screen.getByRole("button", { name: /select this variant/i }));
    await waitFor(() => expect(api.selectVideo).toHaveBeenCalledWith(
      "assembly-project",
      "frame-1",
      takeB.id,
    ));
    fireEvent.click(screen.getByRole("button", { name: "Preview dub" }));
    await waitFor(() => expect(previewSpy).toHaveBeenCalledWith(
      "assembly-project",
      "frame-1",
      takeB.id,
      0,
    ));

    expect(await screen.findByText("Dubbing preview ready")).toBeInTheDocument();
    expect(screen.getByTestId("take-media-take-2")).toHaveAttribute(
      "src",
      expect.stringContaining("/files/video/preview-take-2.mp4"),
    );
    expect(screen.getByTestId("take-media-take-1")).toHaveAttribute(
      "src",
      expect.stringContaining("/files/video/dubbed-take-1.mp4"),
    );
    expect(screen.getByRole("button", { name: "Apply dub" })).toBeEnabled();
  });

  it("shows dubbing failures inline", async () => {
    vi.spyOn(api, "previewDub").mockRejectedValue({
      response: { data: { detail: "Dialogue audio could not be mixed" } },
    });
    seed(project());
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByText("Shot action"));

    fireEvent.click(screen.getByRole("button", { name: "Preview dub" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Dialogue audio could not be mixed");
  });

  it("exports compatible options and downloads merged, exported, and sidecar media through authentication", async () => {
    vi.spyOn(api, "exportProject").mockResolvedValue({ url: "export/final.webm" });
    seed(project({}, { merged_video_url: "video/merged.mp4" }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Export" }));

    fireEvent.click(screen.getByRole("button", { name: "Download merged MP4" }));
    await waitFor(() => expect(saveAuthenticatedMedia).toHaveBeenCalledWith(
      expect.stringContaining("/files/video/merged.mp4"),
      "Assembly-Test_merged.mp4",
    ));

    fireEvent.change(screen.getByLabelText("Subtitles"), { target: { value: "embedded" } });
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "webm" } });
    expect(screen.getByLabelText("Subtitles")).toHaveValue("none");
    expect(screen.queryByRole("option", { name: "Embedded" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Resolution"), { target: { value: "720p" } });
    fireEvent.change(screen.getByLabelText("Subtitles"), { target: { value: "sidecar" } });
    fireEvent.click(screen.getByRole("button", { name: "Export final video" }));

    await waitFor(() => expect(api.exportProject).toHaveBeenCalledWith(
      "assembly-project",
      { resolution: "720p", format: "webm", subtitles: "sidecar" },
    ));
    expect(await screen.findByText("export/final.webm")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Download exported video" }));
    await waitFor(() => expect(saveAuthenticatedMedia).toHaveBeenCalledWith(
      expect.stringContaining("/files/export/final.webm"),
      "Assembly-Test_720p.webm",
    ));
    fireEvent.click(screen.getByRole("button", { name: "Download subtitle file" }));
    await waitFor(() => expect(saveAuthenticatedMedia).toHaveBeenCalledWith(
      expect.stringContaining("/files/export/final.srt"),
      "Assembly-Test_720p.srt",
    ));
  });

  it("clears a completed export artifact when the active project changes", async () => {
    const firstProject = project({}, {
      id: "project-a",
      title: "Project A",
      merged_video_url: "video/project-a-merged.mp4",
    });
    const secondProject = project({}, {
      id: "project-b",
      title: "Project B",
      merged_video_url: "video/project-b-merged.mp4",
    });
    vi.spyOn(api, "exportProject").mockResolvedValue({ url: "export/project-a.mp4" });
    seed(firstProject);
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    fireEvent.click(screen.getByRole("button", { name: "Export final video" }));

    expect(await screen.findByText("export/project-a.mp4")).toBeInTheDocument();

    act(() => {
      useProjectStore.setState({
        projects: [firstProject, secondProject],
        currentProject: secondProject,
      });
    });

    expect(screen.queryByText("export/project-a.mp4")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download exported video" })).not.toBeInTheDocument();
  });

  it("ignores an export response that resolves after switching projects", async () => {
    const firstProject = project({}, {
      id: "project-a",
      title: "Project A",
      merged_video_url: "video/project-a-merged.mp4",
    });
    const secondProject = project({}, {
      id: "project-b",
      title: "Project B",
      merged_video_url: "video/project-b-merged.mp4",
    });
    let resolveFirstExport!: (value: { url: string }) => void;
    const firstExport = new Promise<{ url: string }>((resolve) => {
      resolveFirstExport = resolve;
    });
    const exportSpy = vi.spyOn(api, "exportProject")
      .mockImplementationOnce(() => firstExport)
      .mockResolvedValueOnce({ url: "export/project-b.mp4" });
    seed(firstProject);
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    fireEvent.click(screen.getByRole("button", { name: "Export final video" }));
    await waitFor(() => expect(exportSpy).toHaveBeenCalledWith(
      "project-a",
      { resolution: "1080p", format: "mp4", subtitles: "none" },
    ));

    act(() => {
      useProjectStore.setState({
        projects: [firstProject, secondProject],
        currentProject: secondProject,
      });
    });
    await act(async () => {
      resolveFirstExport({ url: "export/stale-project-a.mp4" });
      await firstExport;
    });

    expect(screen.queryByText("export/stale-project-a.mp4")).not.toBeInTheDocument();
    const exportButton = screen.getByRole("button", { name: "Export final video" });
    expect(exportButton).toBeEnabled();

    fireEvent.click(exportButton);
    await waitFor(() => expect(exportSpy).toHaveBeenLastCalledWith(
      "project-b",
      { resolution: "1080p", format: "mp4", subtitles: "none" },
    ));
    expect(await screen.findByText("export/project-b.mp4")).toBeInTheDocument();
  });

  it("invalidates an export after re-merging and prevents merge/export races", async () => {
    let resolveExport!: (value: { url: string }) => void;
    const pendingExport = new Promise<{ url: string }>((resolve) => {
      resolveExport = resolve;
    });
    const exportSpy = vi.spyOn(api, "exportProject")
      .mockReturnValueOnce(pendingExport)
      .mockResolvedValueOnce({ url: "export/from-new-merge.mp4" });
    let resolveMerge!: (value: Project) => void;
    const pendingMerge = new Promise<Project>((resolve) => {
      resolveMerge = resolve;
    });
    const mergeSpy = vi.spyOn(api, "mergeVideos").mockReturnValue(pendingMerge);
    seed(project({}, { merged_video_url: "video/merged-v1.mp4" }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Export" }));

    const exportButton = screen.getByRole("button", { name: "Export final video" });
    const mergeButton = screen.getByRole("button", { name: "Merge & Proceed" });
    fireEvent.click(exportButton);
    await waitFor(() => expect(exportSpy).toHaveBeenCalledOnce());
    expect(mergeButton).toBeDisabled();
    fireEvent.click(mergeButton);
    expect(mergeSpy).not.toHaveBeenCalled();

    await act(async () => {
      resolveExport({ url: "export/from-v1.mp4" });
      await pendingExport;
    });
    expect(await screen.findByText("export/from-v1.mp4")).toBeInTheDocument();

    fireEvent.click(mergeButton);
    await waitFor(() => expect(mergeSpy).toHaveBeenCalledOnce());
    expect(exportButton).toBeDisabled();
    fireEvent.click(exportButton);
    expect(exportSpy).toHaveBeenCalledOnce();

    await act(async () => {
      resolveMerge(project({}, { merged_video_url: "video/merged-v2.mp4" }));
      await pendingMerge;
    });
    expect(screen.queryByText("export/from-v1.mp4")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Download exported video" }),
    ).not.toBeInTheDocument();

    fireEvent.click(exportButton);
    await waitFor(() => expect(exportSpy).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("export/from-new-merge.mp4")).toBeInTheDocument();
  });

  it("locks phase navigation and assembly actions during an export", async () => {
    let resolveExport!: (value: { url: string }) => void;
    const pendingExport = new Promise<{ url: string }>((resolve) => {
      resolveExport = resolve;
    });
    const exportSpy = vi.spyOn(api, "exportProject").mockReturnValue(pendingExport);
    const mergeSpy = vi.spyOn(api, "mergeVideos");
    seed(project({}, { merged_video_url: "video/merged.mp4" }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Export" }));

    fireEvent.click(screen.getByRole("button", { name: "Export final video" }));
    await waitFor(() => expect(exportSpy).toHaveBeenCalledOnce());

    const takesTab = screen.getByRole("button", { name: "Takes" });
    expect(takesTab).toBeDisabled();
    fireEvent.click(takesTab);
    expect(screen.getByRole("heading", { name: "Export Final Video" })).toBeInTheDocument();

    const exportButton = screen.getByRole("button", { name: "Exporting..." });
    const mergeButton = screen.getByRole("button", { name: "Merge & Proceed" });
    expect(exportButton).toBeDisabled();
    expect(mergeButton).toBeDisabled();
    fireEvent.click(exportButton);
    fireEvent.click(mergeButton);
    expect(exportSpy).toHaveBeenCalledOnce();
    expect(mergeSpy).not.toHaveBeenCalled();

    await act(async () => {
      resolveExport({ url: "export/after-navigation.mp4" });
      await pendingExport;
    });
    expect(await screen.findByText("export/after-navigation.mp4")).toBeInTheDocument();
  });

  it("disables mix controls that cannot affect the exported audio", async () => {
    vi.spyOn(api, "listBgmPresets").mockResolvedValue([
      {
        id: "calm_warm",
        label: "Calm",
        mood: "warm",
        url: "presets/bgm/calm_warm.mp3",
        available: false,
      },
    ]);
    seed(project());
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Mix" }));

    expect(await screen.findByText(/No licensed background tracks are installed/)).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Dialogue" })).toBeDisabled();
    expect(screen.getByRole("slider", { name: "BGM" })).toBeDisabled();
    expect(screen.getByRole("slider", { name: "SFX" })).toBeDisabled();
    expect(screen.getByText(/SFX mixing is not available/)).toBeInTheDocument();
  });

  it("uploads and selects a custom BGM track from the Mix phase", async () => {
    vi.spyOn(api, "listBgmPresets").mockResolvedValue([]);
    const uploaded = project({}, {
      bgm_url: "audio/custom_bgm/assembly-project/custom-score.mp3",
      merged_video_url: null,
      mix_settings: { dialogue: 100, bgm: 35, sfx: 60 },
    });
    const uploadSpy = vi.spyOn(api, "uploadCustomBgm").mockResolvedValue(uploaded);
    seed(project({}, { merged_video_url: "video/merged.mp4" }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Mix" }));
    const input = screen.getByLabelText("Upload custom BGM", { selector: "input" });
    const file = new File(["ID3-music"], "score.mp3", { type: "audio/mpeg" });

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(uploadSpy).toHaveBeenCalledWith(
      "assembly-project",
      file,
    ));
    expect(await screen.findByText("Custom track")).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Dialogue" })).toBeEnabled();
    expect(useProjectStore.getState().currentProject?.bgm_url).toBe(
      "audio/custom_bgm/assembly-project/custom-score.mp3",
    );
    expect(useProjectStore.getState().currentProject?.merged_video_url).toBeNull();
  });

  it("never treats an unresolved preset path as a usable custom upload", async () => {
    vi.spyOn(api, "listBgmPresets").mockRejectedValue(new Error("catalog unavailable"));
    seed(project({}, {
      bgm_url: "presets/bgm/calm_warm.mp3",
      mix_settings: { dialogue: 100, bgm: 35, sfx: 60 },
    }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Mix" }));

    expect(await screen.findByText(/No licensed background tracks are installed/)).toBeInTheDocument();
    expect(screen.queryByText("calm_warm.mp3")).not.toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Dialogue" })).toBeDisabled();
    expect(screen.getByRole("slider", { name: "BGM" })).toBeDisabled();
  });

  it("shows custom BGM upload progress and surfaces a failed upload", async () => {
    vi.spyOn(api, "listBgmPresets").mockResolvedValue([]);
    let rejectUpload!: (error: unknown) => void;
    const pendingUpload = new Promise<Project>((_resolve, reject) => {
      rejectUpload = reject;
    });
    const uploadSpy = vi.spyOn(api, "uploadCustomBgm").mockReturnValue(pendingUpload);
    seed(project());
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Mix" }));
    const input = screen.getByLabelText("Upload custom BGM", { selector: "input" });
    const file = new File(["ID3-music"], "score.mp3", { type: "audio/mpeg" });

    fireEvent.change(input, { target: { files: [file] } });
    expect(await screen.findByRole("button", { name: /Uploading BGM/ })).toBeDisabled();
    fireEvent.change(input, { target: { files: [file] } });
    expect(uploadSpy).toHaveBeenCalledOnce();

    await act(async () => {
      rejectUpload({
        response: { data: { detail: "The audio file could not be stored" } },
      });
      await pendingUpload.catch(() => undefined);
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The audio file could not be stored",
    );
    expect(screen.getByRole("button", { name: /Upload custom BGM/ })).toBeEnabled();
    expect(useProjectStore.getState().currentProject?.bgm_url).toBeUndefined();
  });

  it("serializes rapid supported mix-level writes and persists the latest value last", async () => {
    vi.spyOn(api, "listBgmPresets").mockResolvedValue([]);
    let resolveFirstWrite!: (value: Project) => void;
    const firstWrite = new Promise<Project>((resolve) => {
      resolveFirstWrite = resolve;
    });
    const updateSpy = vi.spyOn(api, "updateAudioMix")
      .mockImplementationOnce(() => firstWrite)
      .mockResolvedValueOnce(project({}, {
        bgm_url: "custom/bgm.mp3",
        mix_settings: { dialogue: 70, bgm: 35, sfx: 60 },
      }));
    seed(project({}, {
      bgm_url: "custom/bgm.mp3",
      mix_settings: { dialogue: 100, bgm: 35, sfx: 60 },
    }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Mix" }));

    const dialogueSlider = screen.getByRole("slider", { name: "Dialogue" });
    await waitFor(() => expect(dialogueSlider).toBeEnabled());
    fireEvent.change(dialogueSlider, { target: { value: "40" } });
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: /No BGM/i })).toBeDisabled();
    expect(updateSpy).toHaveBeenLastCalledWith(
      "assembly-project",
      { dialogue_volume: 40 },
    );

    fireEvent.change(dialogueSlider, { target: { value: "70" } });
    expect(updateSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirstWrite(project({}, {
        bgm_url: "custom/bgm.mp3",
        mix_settings: { dialogue: 40, bgm: 35, sfx: 60 },
      }));
      await firstWrite;
    });
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(2));
    expect(updateSpy).toHaveBeenLastCalledWith(
      "assembly-project",
      { dialogue_volume: 70 },
    );
  });

  it("surfaces a supported mix-level persistence failure", async () => {
    vi.spyOn(api, "listBgmPresets").mockResolvedValue([]);
    const updateSpy = vi.spyOn(api, "updateAudioMix")
      .mockRejectedValueOnce({
        response: { data: { detail: "Audio mix storage is unavailable" } },
      })
      .mockResolvedValueOnce(project({}, {
        bgm_url: "custom/bgm.mp3",
        mix_settings: { dialogue: 100, bgm: 25, sfx: 60 },
      }));
    seed(project({}, {
      bgm_url: "custom/bgm.mp3",
      mix_settings: { dialogue: 100, bgm: 35, sfx: 60 },
    }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Mix" }));

    const dialogueSlider = screen.getByRole("slider", { name: "Dialogue" });
    await waitFor(() => expect(dialogueSlider).toBeEnabled());
    fireEvent.change(dialogueSlider, { target: { value: "55" } });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Audio mix storage is unavailable",
    );
    await waitFor(() => expect(dialogueSlider).toHaveValue("100"));

    const bgmSlider = screen.getByRole("slider", { name: "BGM" });
    fireEvent.change(bgmSlider, { target: { value: "25" } });
    await waitFor(() => expect(updateSpy).toHaveBeenLastCalledWith(
      "assembly-project",
      { bgm_volume: 25 },
    ));
    await waitFor(() => {
      expect(dialogueSlider).toHaveValue("100");
      expect(bgmSlider).toHaveValue("25");
    });
  });

  it("rolls back a failed mix write even after leaving the Mix phase", async () => {
    vi.spyOn(api, "listBgmPresets").mockResolvedValue([]);
    let rejectWrite!: (error: unknown) => void;
    const pendingWrite = new Promise<Project>((_resolve, reject) => {
      rejectWrite = reject;
    });
    vi.spyOn(api, "updateAudioMix").mockReturnValue(pendingWrite);
    seed(project({}, {
      bgm_url: "custom/bgm.mp3",
      merged_video_url: "video/merged.mp4",
      mix_settings: { dialogue: 100, bgm: 35, sfx: 60 },
    }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Mix" }));

    const dialogueSlider = screen.getByRole("slider", { name: "Dialogue" });
    await waitFor(() => expect(dialogueSlider).toBeEnabled());
    fireEvent.change(dialogueSlider, { target: { value: "55" } });
    await waitFor(() => expect(api.updateAudioMix).toHaveBeenCalledOnce());
    expect(useProjectStore.getState().currentProject?.mix_settings?.dialogue).toBe(55);
    expect(useProjectStore.getState().currentProject?.merged_video_url).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Takes" }));
    await act(async () => {
      rejectWrite(new Error("mix write failed"));
      await pendingWrite.catch(() => undefined);
    });
    fireEvent.click(screen.getByRole("button", { name: "Mix" }));

    await waitFor(() => {
      expect(screen.getByRole("slider", { name: "Dialogue" })).toHaveValue("100");
    });
    expect(useProjectStore.getState().currentProject?.merged_video_url).toBe(
      "video/merged.mp4",
    );
  });

  it("disables level writes while a BGM selection is being saved", async () => {
    vi.spyOn(api, "listBgmPresets").mockResolvedValue([]);
    let resolveBgm!: (value: Project) => void;
    const pendingBgm = new Promise<Project>((resolve) => {
      resolveBgm = resolve;
    });
    const updateSpy = vi.spyOn(api, "updateAudioMix").mockReturnValue(pendingBgm);
    seed(project({}, {
      bgm_url: "custom/bgm.mp3",
      mix_settings: { dialogue: 100, bgm: 35, sfx: 60 },
    }));
    renderWithIntl(<VideoAssembly />, { locale: "en" });
    fireEvent.click(screen.getByRole("button", { name: "Mix" }));

    const dialogueSlider = screen.getByRole("slider", { name: "Dialogue" });
    await waitFor(() => expect(dialogueSlider).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /No BGM/i }));
    await waitFor(() => expect(updateSpy).toHaveBeenCalledWith(
      "assembly-project",
      { bgm_url: null },
    ));
    expect(dialogueSlider).toBeDisabled();
    fireEvent.change(dialogueSlider, { target: { value: "20" } });
    expect(updateSpy).toHaveBeenCalledOnce();

    await act(async () => {
      resolveBgm(project({}, {
        bgm_url: null,
        mix_settings: { dialogue: 100, bgm: 35, sfx: 60 },
      }));
      await pendingBgm;
    });
  });
});
