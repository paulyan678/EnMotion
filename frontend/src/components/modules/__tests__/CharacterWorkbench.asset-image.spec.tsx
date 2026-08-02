import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithIntl } from "@/test/renderWithIntl";
import { useProjectStore } from "@/store/projectStore";

vi.mock("@/components/common/VariantSelector", () => ({
  VariantSelector: ({ asset, currentImageUrl, onSelect, onDelete, onFavorite }: any) => (
    <>
      <button
        type="button"
        data-testid="variant-selector"
        data-current-image={currentImageUrl || ""}
        data-selected-id={asset?.selected_id || ""}
        data-variant-count={String(asset?.variants?.length || 0)}
      />
      <button type="button" data-testid="select-variant" onClick={() => onSelect?.("variant-from-test")} />
      <button type="button" data-testid="delete-variant" onClick={() => onDelete?.("variant-from-test")} />
      <button type="button" data-testid="favorite-variant" onClick={() => onFavorite?.("variant-from-test", true)} />
    </>
  ),
}));

vi.mock("@/components/common/VideoVariantSelector", () => ({
  VideoVariantSelector: () => null,
}));

import CharacterWorkbench from "../CharacterWorkbench";

describe("CharacterWorkbench master image", () => {
  beforeEach(() => {
    useProjectStore.setState({ currentProject: null });
    vi.stubGlobal("confirm", vi.fn(() => true));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("normalizes a global character reference sheet for the master panel", () => {
    const onGenerate = vi.fn();
    renderWithIntl(
      <CharacterWorkbench
        asset={{
          id: "tester",
          name: "Tester",
          description: "Shared character",
          source: "global",
          reference_sheet: {
            selected_image_id: "tester-image",
            image_variants: [
              { id: "tester-image", url: "assets/tester.png", created_at: 1 },
            ],
          },
          three_views: {
            selected_image_id: "tester-three-view",
            image_variants: [
              { id: "tester-three-view", url: "assets/tester-three.png", created_at: 2 },
            ],
          },
          head_shot: {
            selected_image_id: "tester-headshot",
            image_variants: [
              { id: "tester-headshot", url: "assets/tester-head.png", created_at: 3 },
            ],
          },
        }}
        onClose={vi.fn()}
        onGenerate={onGenerate}
        generatingTypes={[]}
        onSaveMetadata={vi.fn()}
      />,
      { locale: "en" },
    );

    const master = screen.getAllByTestId("variant-selector")[0];
    expect(master).toHaveAttribute("data-current-image", "assets/tester.png");
    expect(master).toHaveAttribute("data-selected-id", "tester-image");
    expect(master).toHaveAttribute("data-variant-count", "1");
    fireEvent.click(screen.getByRole("button", { name: /2\. Three-view/ }));
    expect(screen.getByTestId("variant-selector")).toHaveAttribute(
      "data-current-image",
      "assets/tester-three.png",
    );
    fireEvent.click(screen.getByRole("button", { name: /3\. Headshot/ }));
    expect(screen.getByTestId("variant-selector")).toHaveAttribute(
      "data-current-image",
      "assets/tester-head.png",
    );

    fireEvent.click(screen.getByRole("button", { name: /1\. Full body/ }));
    fireEvent.click(screen.getByRole("button", { name: "Generate output" }));
    expect(onGenerate).toHaveBeenCalledWith(
      "reference_sheet",
      expect.any(String),
      false,
      "",
      1,
      {
        aspectRatio: "9:16",
        modelName: "gpt-image-2",
        templateId: undefined,
      },
    );
  });

  it("uses injected owner-aware variant mutations without a current project", async () => {
    const onSelectVariant = vi.fn();
    const onDeleteVariant = vi.fn();
    const onFavoriteVariant = vi.fn();

    renderWithIntl(
      <CharacterWorkbench
        asset={{
          id: "global-character",
          name: "Global character",
          description: "Not attached to an episode",
          source: "global",
          reference_sheet: {
            selected_image_id: "master-image",
            image_variants: [{ id: "master-image", url: "assets/master.png", created_at: 1 }],
          },
          three_views: {
            selected_image_id: "three-view-image",
            image_variants: [{ id: "three-view-image", url: "assets/three-view.png", created_at: 2 }],
          },
        }}
        onClose={vi.fn()}
        onGenerate={vi.fn()}
        generatingTypes={[]}
        onSelectVariant={onSelectVariant}
        onDeleteVariant={onDeleteVariant}
        onFavoriteVariant={onFavoriteVariant}
        onSaveMetadata={vi.fn()}
      />,
      { locale: "en" },
    );

    // Use the three-view panel so every callback receives the same explicit
    // image kind. There is deliberately no current project in the store.
    fireEvent.click(screen.getByRole("button", { name: /2\. Three-view/ }));
    fireEvent.click(screen.getByTestId("select-variant"));
    fireEvent.click(screen.getByTestId("delete-variant"));
    fireEvent.click(screen.getByTestId("favorite-variant"));

    await waitFor(() => {
      expect(onSelectVariant).toHaveBeenCalledWith("three_view", "variant-from-test");
      expect(onDeleteVariant).toHaveBeenCalledWith("three_view", "variant-from-test");
      expect(onFavoriteVariant).toHaveBeenCalledWith("three_view", "variant-from-test", true);
    });
    expect(useProjectStore.getState().currentProject).toBeNull();
  });

  it("owns focus and Escape, renames the character, and preserves legacy metadata", async () => {
    const onClose = vi.fn();
    const onSaveMetadata = vi.fn();

    renderWithIntl(
      <CharacterWorkbench
        asset={{
          id: "global-character",
          name: "Global character",
          description: "Shared character",
          visual_weight: 12,
        }}
        onClose={onClose}
        onGenerate={vi.fn()}
        generatingTypes={[]}
        onSaveMetadata={onSaveMetadata}
      />,
      { locale: "en" },
    );

    const dialog = screen.getByRole("dialog", { name: "Edit Asset" });
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: "Generate output" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Apply the current art direction")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Renamed character" } });
    expect(screen.queryByLabelText("Visual weight")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => {
      expect(onSaveMetadata).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Renamed character", visualWeight: 5 }),
      );
    });

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
