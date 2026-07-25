import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";

vi.mock("@/components/common/VariantSelector", () => ({
  VariantSelector: ({ onSelect, onDelete, onFavorite, disabled }: any) => (
    <div>
      <button type="button" disabled={disabled} onClick={() => onSelect("variant-2")}>选择变体</button>
      <button type="button" disabled={disabled} onClick={() => onDelete("variant-2")}>删除变体</button>
      <button type="button" disabled={disabled} onClick={() => onFavorite("variant-2", true)}>收藏变体</button>
    </div>
  ),
}));

vi.mock("@/components/common/VideoVariantSelector", () => ({
  VideoVariantSelector: () => null,
}));

import ScenePropWorkbench from "../ScenePropWorkbench";

const scene = {
  id: "scene-1",
  name: "Old scene",
  description: "Old description",
  image_prompt: "Old prompt",
  video_prompt: "Old video prompt",
  time_of_day: "Day",
  lighting_mood: "Soft",
  visual_weight: 2,
  image_asset: {
    selected_id: "variant-1",
    variants: [{ id: "variant-1", url: "assets/one.png", created_at: 1 }],
  },
};

describe("ScenePropWorkbench", () => {
  it("reuses owner-aware save, generation, and variant callbacks", async () => {
    const onSaveMetadata = vi.fn().mockResolvedValue(undefined);
    const onGenerate = vi.fn().mockResolvedValue(undefined);
    const onSelectVariant = vi.fn().mockResolvedValue(undefined);
    const onDeleteVariant = vi.fn().mockResolvedValue(undefined);
    const onFavoriteVariant = vi.fn().mockResolvedValue(undefined);

    renderWithIntl(
      <ScenePropWorkbench
        asset={scene}
        assetType="scene"
        onClose={vi.fn()}
        onGenerate={onGenerate}
        isGenerating={false}
        onSaveMetadata={onSaveMetadata}
        onSelectVariant={onSelectVariant}
        onDeleteVariant={onDeleteVariant}
        onFavoriteVariant={onFavoriteVariant}
        canChangeAssetType
        supportsMotion={false}
      />,
      { locale: "en" },
    );

    fireEvent.change(screen.getByLabelText("Generation prompt"), { target: { value: "Edited prompt" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate output" }));
    fireEvent.click(screen.getByRole("button", { name: "选择变体" }));
    fireEvent.click(screen.getByRole("button", { name: "删除变体" }));
    fireEvent.click(screen.getByRole("button", { name: "收藏变体" }));
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Edited scene" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(onGenerate).toHaveBeenCalledWith(
      "Edited prompt",
      true,
      expect.any(String),
      1,
      {
        aspectRatio: "16:9",
        modelName: "gpt-image-2",
      },
    );
    expect(onSelectVariant).toHaveBeenCalledWith("variant-2");
    expect(onDeleteVariant).toHaveBeenCalledWith("variant-2");
    expect(onFavoriteVariant).toHaveBeenCalledWith("variant-2", true);
    await waitFor(() =>
      expect(onSaveMetadata).toHaveBeenCalledWith(
        expect.objectContaining({
          assetType: "scene",
          attributes: expect.objectContaining({ name: "Edited scene", time_of_day: "Day" }),
          prompt: "Edited prompt",
          videoPrompt: "Old video prompt",
        }),
      ),
    );
  });

  it("traps focus, owns Escape, and restores prior focus on close", () => {
    const onClose = vi.fn();
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();

    const { unmount } = renderWithIntl(
      <ScenePropWorkbench
        asset={scene}
        assetType="scene"
        onClose={onClose}
        onGenerate={vi.fn()}
        isGenerating={false}
        supportsMotion={false}
        onSaveMetadata={vi.fn()}
      />,
      { locale: "en" },
    );

    const dialog = screen.getByRole("dialog", { name: "Edit Asset" });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])"));
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(first).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });

  it("locks image mutations until an edited asset type is saved", async () => {
    const onSaveMetadata = vi.fn().mockResolvedValue(undefined);
    const onGenerate = vi.fn();

    renderWithIntl(
      <ScenePropWorkbench
        asset={scene}
        assetType="scene"
        onClose={vi.fn()}
        onGenerate={onGenerate}
        isGenerating={false}
        onSaveMetadata={onSaveMetadata}
        canChangeAssetType
        supportsMotion={false}
      />,
      { locale: "en" },
    );

    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "prop" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    expect(screen.getByRole("button", { name: "Generate output" })).toBeDisabled();
    expect(screen.getByText("Save the type change before generating images.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(onSaveMetadata).toHaveBeenCalledWith(expect.objectContaining({ assetType: "prop" })),
    );
    expect(onGenerate).not.toHaveBeenCalled();
  });

  it("renames props, including tool and object assets, through the shared Details save", async () => {
    const onSaveMetadata = vi.fn().mockResolvedValue(undefined);

    renderWithIntl(
      <ScenePropWorkbench
        asset={{
          ...scene,
          id: "prop-1",
          name: "Old tool",
          description: "A handheld tool",
        }}
        assetType="prop"
        onClose={vi.fn()}
        onGenerate={vi.fn()}
        isGenerating={false}
        onSaveMetadata={onSaveMetadata}
        supportsMotion={false}
      />,
      { locale: "en" },
    );

    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Renamed tool" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(onSaveMetadata).toHaveBeenCalledWith(
        expect.objectContaining({
          assetType: "prop",
          attributes: expect.objectContaining({
            name: "Renamed tool",
            description: "A handheld tool",
          }),
        }),
      ),
    );
  });
});
