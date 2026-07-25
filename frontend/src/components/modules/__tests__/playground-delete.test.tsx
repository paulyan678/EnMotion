import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DetailPanel from "@/components/modules/playground/DetailPanel";
import {
  usePlaygroundStore,
  type PlaygroundGeneration,
} from "@/components/modules/playground/usePlaygroundStore";
import { playgroundApi } from "@/lib/api";
import { renderWithIntl } from "@/test/renderWithIntl";

vi.mock("@/lib/api", () => ({
  API_URL: "http://127.0.0.1:17177",
  playgroundApi: {
    deleteGeneration: vi.fn(),
  },
}));

const generation: PlaygroundGeneration = {
  id: "generation-to-delete",
  mode: "t2i",
  model_id: "gpt-image-2",
  prompt: "A sunlit animation studio",
  input_media: [],
  parameters: {},
  batch_size: 1,
  outputs: [
    {
      id: "output-to-delete",
      media_path: "playground/images/result.png",
      media_type: "image",
      saved_to_library: false,
    },
  ],
  status: "completed",
  created_at: "2026-07-21T00:00:00.000Z",
};

beforeEach(() => {
  usePlaygroundStore.setState({
    history: [generation],
    activeGenerationIds: [],
    isGenerating: false,
    featuredByGen: { [generation.id]: generation.outputs[0].id },
  });
});

afterEach(() => {
  usePlaygroundStore.setState({
    history: [],
    activeGenerationIds: [],
    isGenerating: false,
    featuredByGen: {},
  });
  vi.clearAllMocks();
});

describe("Playground detail deletion", () => {
  it("removes the confirmed generation immediately without a page refresh", async () => {
    let confirmServerDelete: (() => void) | undefined;
    vi.mocked(playgroundApi.deleteGeneration).mockReturnValueOnce(
      new Promise<void>((resolve) => {
        confirmServerDelete = resolve;
      }),
    );
    const onClose = vi.fn();

    renderWithIntl(
      <DetailPanel
        generation={generation}
        allGenerations={[generation]}
        onClose={onClose}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "GPT Image 2" })).toBeInTheDocument();
    expect(screen.queryByText("gpt-image-2")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    expect(playgroundApi.deleteGeneration).toHaveBeenCalledWith(generation.id);
    expect(usePlaygroundStore.getState().history).toEqual([generation]);
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => {
      confirmServerDelete?.();
    });

    await waitFor(() => {
      expect(usePlaygroundStore.getState().history).toEqual([]);
      expect(usePlaygroundStore.getState().featuredByGen).toEqual({});
      expect(onClose).toHaveBeenCalledOnce();
    });
  });
});
