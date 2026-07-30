import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";
import AssetEditorShell from "./AssetEditorShell";

describe("AssetEditorShell", () => {
  it("portals the full editor to document.body so application sidebars cannot cover it", () => {
    const view = renderWithIntl(
      <div data-testid="transformed-parent" style={{ transform: "translateX(300px)" }}>
        <AssetEditorShell
          title="守塔人"
          typeLabel="角色"
          isDirty={false}
          isSaving={false}
          onSave={vi.fn()}
          onRequestClose={vi.fn()}
          rail={<div>变体列表</div>}
          preview={<div>资产预览</div>}
          inspector={<div>编辑面板</div>}
        />
      </div>,
      { locale: "zh" },
    );

    const dialog = screen.getByRole("dialog");
    expect(document.body).toContainElement(dialog);
    expect(view.getByTestId("transformed-parent")).not.toContainElement(dialog);
    expect(screen.getByText("资产预览")).toBeInTheDocument();
    expect(screen.getByText("编辑面板")).toBeInTheDocument();
  });
});
