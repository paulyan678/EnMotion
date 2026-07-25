import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";

const sharedEditor = vi.hoisted(() => vi.fn());

vi.mock("@/components/assets/SharedAssetEditor", () => ({
  default: (props: {
    open: boolean;
    assetRef: {
      ownerKind: string;
      ownerId: string;
      assetType: string;
      assetId: string;
    };
    onClose: () => void;
    onMutated: () => void;
    onConverted: () => void;
  }) => {
    sharedEditor(props);
    return props.open ? (
      <section data-testid="shared-asset-editor">
        <span>{JSON.stringify(props.assetRef)}</span>
        <button type="button" onClick={props.onClose}>关闭</button>
        <button type="button" onClick={props.onMutated}>变更</button>
        <button type="button" onClick={props.onConverted}>转换</button>
      </section>
    ) : null;
  },
}));

import LibraryAssetEditor from "../LibraryAssetEditor";

describe("LibraryAssetEditor compatibility launcher", () => {
  it("delegates the exact composite owner identity to the canonical editor", () => {
    renderWithIntl(
      <LibraryAssetEditor
        open
        sourceKind="series"
        sourceId="series-7"
        assetType="character"
        assetId="character-1"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
      { locale: "en" },
    );

    expect(screen.getByTestId("shared-asset-editor")).toHaveTextContent(
      JSON.stringify({
        ownerKind: "series",
        ownerId: "series-7",
        assetType: "character",
        assetId: "character-1",
      }),
    );
    expect(sharedEditor).toHaveBeenCalledWith(expect.objectContaining({
      assetRef: {
        ownerKind: "series",
        ownerId: "series-7",
        assetType: "character",
        assetId: "character-1",
      },
    }));
  });

  it("contains no independent editor orchestration and preserves callbacks", () => {
    const onClose = vi.fn();
    const onSaved = vi.fn();
    renderWithIntl(
      <LibraryAssetEditor
        open
        sourceKind="global"
        sourceId="global"
        assetType="prop"
        assetId="prop-1"
        onClose={onClose}
        onSaved={onSaved}
      />,
      { locale: "en" },
    );

    fireEvent.click(screen.getByRole("button", { name: "变更" }));
    fireEvent.click(screen.getByRole("button", { name: "转换" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));

    expect(onSaved).toHaveBeenCalledTimes(2);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
