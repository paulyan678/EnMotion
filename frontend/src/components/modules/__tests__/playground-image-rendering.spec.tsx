import { fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiUrl", () => ({
  API_URL: "https://studio.example",
}));

import DetailPanel from "@/components/modules/playground/DetailPanel";
import GalleryView from "@/components/modules/playground/GalleryView";
import ResultCard from "@/components/modules/playground/ResultCard";
import {
  usePlaygroundStore,
  type PlaygroundGeneration,
} from "@/components/modules/playground/usePlaygroundStore";
import { renderWithIntl } from "@/test/renderWithIntl";

function generation(mediaPath: string): PlaygroundGeneration {
  return {
    id: "generation-image",
    mode: "t2i",
    model_id: "gpt-image-2",
    prompt: "A persistent Playground image",
    input_media: [],
    parameters: {},
    batch_size: 1,
    outputs: [
      {
        id: "output-image",
        media_path: mediaPath,
        media_type: "image",
        saved_to_library: false,
      },
    ],
    status: "completed",
    created_at: "2026-07-22T00:00:00.000Z",
  };
}

function videoGeneration(): PlaygroundGeneration {
  return {
    ...generation("playground/videos/result.mp4"),
    id: "generation-video",
    mode: "t2v",
    outputs: [
      {
        id: "output-video",
        media_path: "playground/videos/result.mp4",
        media_type: "video",
        saved_to_library: false,
      },
    ],
  };
}

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    bottom: 240,
    height: 240,
    left: 0,
    right: 320,
    top: 0,
    width: 320,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
  usePlaygroundStore.setState({
    history: [],
    activeGenerationIds: [],
    featuredByGen: {},
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Playground image rendering", () => {
  it.each([
    [
      "relative server media",
      "playground/images/result.png",
      "https://studio.example/files/playground/images/result.png",
    ],
    [
      "legacy output prefix",
      "output/playground/images/result.png",
      "https://studio.example/files/playground/images/result.png",
    ],
    [
      "restored authenticated URL",
      "https://old.example/files/playground/images/result.png?expired=1",
      "https://studio.example/files/playground/images/result.png",
    ],
    [
      "temporary external provider URL",
      "https://provider.example/temporary/result.png?signature=live",
      "https://provider.example/temporary/result.png?signature=live",
    ],
  ])("uses the shared resolver for %s", (_label, mediaPath, expected) => {
    const item = generation(mediaPath);
    renderWithIntl(<ResultCard generation={item} />, { locale: "en" });

    expect(screen.getByRole("status", { name: "Loading image…" })).toBeInTheDocument();
    expect(screen.getByAltText(item.prompt)).toHaveAttribute("src", expected);

    fireEvent.load(screen.getByAltText(item.prompt));
    expect(screen.queryByRole("status", { name: "Loading image…" })).not.toBeInTheDocument();
  });

  it("shows a localized retry after authenticated media genuinely fails", () => {
    const item = generation("playground/images/missing.png");
    renderWithIntl(<ResultCard generation={item} />, { locale: "zh" });

    const image = screen.getByAltText(item.prompt);
    fireEvent.error(image);
    expect(image).toHaveAttribute(
      "src",
      "https://studio.example/files/playground/images/missing.png?__r=1",
    );

    fireEvent.error(image);
    expect(screen.getByText("图片加载失败")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    const retried = screen.getByAltText(item.prompt);
    expect(retried).toHaveAttribute(
      "src",
      "https://studio.example/files/playground/images/missing.png?__r=2",
    );
    expect(screen.getByRole("status", { name: "正在加载图片…" })).toBeInTheDocument();
  });

  it("uses the same persisted image in gallery and detail views", () => {
    const item = generation("outputs/playground/images/restored.png");
    usePlaygroundStore.setState({ history: [item] });

    const gallery = renderWithIntl(
      <GalleryView generations={[item]} onOpenDetail={vi.fn()} />,
      { locale: "en" },
    );
    expect(screen.getByAltText(item.prompt)).toHaveAttribute(
      "src",
      "https://studio.example/files/playground/images/restored.png",
    );
    gallery.unmount();

    renderWithIntl(
      <DetailPanel
        generation={item}
        allGenerations={[item]}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
      { locale: "en" },
    );
    expect(screen.getByAltText(item.prompt)).toHaveAttribute(
      "src",
      "https://studio.example/files/playground/images/restored.png",
    );
  });

  it("lets video controls handle clicks without opening detail", () => {
    const item = videoGeneration();
    const onOpenDetail = vi.fn();
    const { container } = renderWithIntl(
      <GalleryView generations={[item]} onOpenDetail={onOpenDetail} />,
      { locale: "en" },
    );

    const videoControls = container.querySelector("video[controls]");
    expect(videoControls).toBeInstanceOf(HTMLVideoElement);
    fireEvent.click(videoControls!);
    fireEvent.keyDown(videoControls!, { key: "Enter" });
    expect(onOpenDetail).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", {
      name: `Click to view details: ${item.prompt}`,
    }));
    expect(onOpenDetail).toHaveBeenCalledWith(item);
  });

  it("retries a failed gallery item without also opening detail", () => {
    const item: PlaygroundGeneration = {
      ...generation(""),
      status: "failed",
      outputs: [],
      error: "Generation failed",
    };
    const onOpenDetail = vi.fn();
    const onRetry = vi.fn();
    renderWithIntl(
      <GalleryView
        generations={[item]}
        onOpenDetail={onOpenDetail}
        onRetry={onRetry}
      />,
      { locale: "en" },
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledWith(item);
    expect(onOpenDetail).not.toHaveBeenCalled();
  });
});
