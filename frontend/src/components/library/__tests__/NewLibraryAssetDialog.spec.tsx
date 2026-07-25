import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { subscribeToAssetLibraryChanges } from "@/lib/assetLibrarySync";
import { renderWithIntl } from "@/test/renderWithIntl";

const uploadLibraryImage = vi.fn();
const createLibraryAsset = vi.fn();

vi.mock("@/lib/api", () => ({
  API_URL: "http://127.0.0.1:17177",
  api: {
    uploadLibraryImage: (...args: unknown[]) => uploadLibraryImage(...args),
    createLibraryAsset: (...args: unknown[]) => createLibraryAsset(...args),
  },
}));

vi.mock("@/store/toastStore", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import NewLibraryAssetDialog from "../NewLibraryAssetDialog";

describe("NewLibraryAssetDialog synchronization", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    uploadLibraryImage.mockResolvedValue({ image_url: "uploads/tester.png" });
    createLibraryAsset.mockResolvedValue({
      id: "char_tester",
      name: "tester",
      description: "tester description",
      reference_sheet: {
        selected_image_id: "img_tester",
        image_variants: [
          {
            id: "img_tester",
            url: "uploads/tester.png",
            created_at: 1_784_671_200,
          },
        ],
      },
    });
  });

  it("broadcasts a global invalidation with the persisted asset identity", async () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToAssetLibraryChanges(listener);
    const onClose = vi.fn();
    renderWithIntl(
      <NewLibraryAssetDialog onClose={onClose} />,
    );

    fireEvent.change(screen.getByLabelText("名称"), {
      target: { value: "tester" },
    });
    fireEvent.change(screen.getByLabelText("描述"), {
      target: { value: "tester description" },
    });

    const dialog = screen.getByRole("dialog");
    const fileInput = dialog.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput!, {
      target: { files: [new File(["image"], "tester.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(uploadLibraryImage).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(screen.getByDisplayValue("uploads/tester.png")).toBeInTheDocument();
    });

    fireEvent.submit(dialog.querySelector("form")!);

    await waitFor(() => {
      expect(createLibraryAsset).toHaveBeenCalledWith("character", {
        name: "tester",
        description: "tester description",
        image_url: "uploads/tester.png",
      });
    });
    expect(listener).toHaveBeenCalledWith({
      source: "global",
      assetType: "character",
      assetId: "char_tester",
      invalidateCollection: true,
    });
    expect(onClose).toHaveBeenCalledTimes(1);

    unsubscribe();
  });
});
