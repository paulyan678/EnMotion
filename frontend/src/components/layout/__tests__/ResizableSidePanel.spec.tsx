import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";
import ResizableSidePanel, {
    EPISODE_EDITOR_PANEL_STORAGE_KEYS,
} from "../ResizableSidePanel";

const sharedProps = {
    defaultWidth: 300,
    minWidth: 220,
    maxWidth: 520,
    minRemainingWidth: 360,
};

describe("ResizableSidePanel", () => {
    beforeEach(() => {
        window.sessionStorage.clear();
        Object.defineProperty(window, "innerWidth", { value: 1200, configurable: true });
    });

    it("resizes the left panel by dragging and restores its width in the same session", () => {
        const view = renderWithIntl(
            <div className="relative flex h-[600px] w-full">
                <ResizableSidePanel
                    {...sharedProps}
                    side="left"
                    storageKey={EPISODE_EDITOR_PANEL_STORAGE_KEYS.left}
                >
                    <div>左侧工作流</div>
                </ResizableSidePanel>
                <div>编辑画布</div>
            </div>,
        );

        const panel = view.container.querySelector('[data-side-panel="left"]') as HTMLElement;
        const separator = screen.getByRole("separator", { name: "调整左侧面板宽度" });
        expect(panel).toHaveStyle({ width: "300px" });

        fireEvent.pointerDown(separator, { button: 0, pointerId: 1, clientX: 300 });
        fireEvent.pointerMove(separator, { pointerId: 1, clientX: 390 });
        fireEvent.pointerUp(separator, { pointerId: 1, clientX: 390 });

        expect(panel).toHaveStyle({ width: "390px" });
        expect(JSON.parse(window.sessionStorage.getItem(EPISODE_EDITOR_PANEL_STORAGE_KEYS.left)!)).toEqual({
            width: 390,
            collapsed: false,
        });

        view.unmount();
        const restored = renderWithIntl(
            <div className="relative flex h-[600px] w-full">
                <ResizableSidePanel
                    {...sharedProps}
                    side="left"
                    storageKey={EPISODE_EDITOR_PANEL_STORAGE_KEYS.left}
                >
                    <div>左侧工作流</div>
                </ResizableSidePanel>
                <div>编辑画布</div>
            </div>,
        );
        expect(restored.container.querySelector('[data-side-panel="left"]')).toHaveStyle({ width: "390px" });
    });

    it("collapses and restores each panel independently without unmounting its content", () => {
        const view = renderWithIntl(
            <div className="relative flex h-[600px] w-full">
                <ResizableSidePanel
                    {...sharedProps}
                    side="left"
                    storageKey={EPISODE_EDITOR_PANEL_STORAGE_KEYS.left}
                >
                    <div>左侧工作流</div>
                </ResizableSidePanel>
                <div>编辑画布</div>
                <ResizableSidePanel
                    {...sharedProps}
                    side="right"
                    storageKey={EPISODE_EDITOR_PANEL_STORAGE_KEYS.right}
                >
                    <div>右侧详情</div>
                </ResizableSidePanel>
            </div>,
        );

        fireEvent.click(screen.getByRole("button", { name: "收起左侧面板" }));
        const leftPanel = view.container.querySelector('[data-side-panel="left"]') as HTMLElement;
        const rightPanel = view.container.querySelector('[data-side-panel="right"]') as HTMLElement;
        expect(leftPanel).toHaveStyle({ width: "0px" });
        expect(rightPanel).toHaveStyle({ width: "300px" });
        expect(screen.getByText("左侧工作流")).not.toBeVisible();
        expect(screen.getByText("编辑画布")).toBeVisible();

        fireEvent.click(screen.getByRole("button", { name: "收起右侧面板" }));
        expect(rightPanel).toHaveStyle({ width: "0px" });
        expect(screen.getByText("右侧详情")).not.toBeVisible();
        expect(screen.getByRole("button", { name: "展开左侧面板" })).toBeVisible();
        expect(screen.getByRole("button", { name: "展开右侧面板" })).toBeVisible();

        fireEvent.click(screen.getByRole("button", { name: "展开右侧面板" }));
        expect(rightPanel).toHaveStyle({ width: "300px" });
        expect(leftPanel).toHaveStyle({ width: "0px" });
        expect(screen.getByText("右侧详情")).toBeVisible();

        fireEvent.click(screen.getByRole("button", { name: "展开左侧面板" }));
        expect(leftPanel).toHaveStyle({ width: "300px" });
        expect(screen.getByText("左侧工作流")).toBeVisible();
    });

    it("uses physical arrow directions for accessible left and right resizing", () => {
        const view = renderWithIntl(
            <div className="relative flex h-[600px] w-full">
                <ResizableSidePanel
                    {...sharedProps}
                    side="left"
                    storageKey={EPISODE_EDITOR_PANEL_STORAGE_KEYS.left}
                >
                    <div>左侧工作流</div>
                </ResizableSidePanel>
                <div>编辑画布</div>
                <ResizableSidePanel
                    {...sharedProps}
                    side="right"
                    storageKey={EPISODE_EDITOR_PANEL_STORAGE_KEYS.right}
                >
                    <div>右侧详情</div>
                </ResizableSidePanel>
            </div>,
        );

        fireEvent.keyDown(screen.getByRole("separator", { name: "调整左侧面板宽度" }), { key: "ArrowRight" });
        fireEvent.keyDown(screen.getByRole("separator", { name: "调整右侧面板宽度" }), { key: "ArrowLeft" });

        expect(view.container.querySelector('[data-side-panel="left"]')).toHaveStyle({ width: "316px" });
        expect(view.container.querySelector('[data-side-panel="right"]')).toHaveStyle({ width: "316px" });
    });

    it("clamps custom widths so the main editing area keeps a usable responsive minimum", () => {
        Object.defineProperty(window, "innerWidth", { value: 700, configurable: true });
        const view = renderWithIntl(
            <div className="relative flex h-[600px] w-full">
                <ResizableSidePanel
                    {...sharedProps}
                    side="left"
                    storageKey={EPISODE_EDITOR_PANEL_STORAGE_KEYS.left}
                >
                    <div>左侧工作流</div>
                </ResizableSidePanel>
                <div>编辑画布</div>
            </div>,
        );
        const panel = view.container.querySelector('[data-side-panel="left"]') as HTMLElement;
        const separator = screen.getByRole("separator", { name: "调整左侧面板宽度" });

        fireEvent.pointerDown(separator, { button: 0, pointerId: 1, clientX: 300 });
        fireEvent.pointerMove(separator, { pointerId: 1, clientX: 900 });
        fireEvent.pointerUp(separator, { pointerId: 1, clientX: 900 });

        expect(panel).toHaveStyle({ width: "340px" });
        expect(separator).toHaveAttribute("aria-valuemax", "340");
    });

    it("defaults to a collapsed overlay without a drag rail on narrow mobile screens", () => {
        Object.defineProperty(window, "innerWidth", { value: 390, configurable: true });
        const view = renderWithIntl(
            <div className="relative flex h-[600px] w-full">
                <ResizableSidePanel
                    {...sharedProps}
                    side="left"
                    storageKey={EPISODE_EDITOR_PANEL_STORAGE_KEYS.left}
                >
                    <div>移动工作流</div>
                </ResizableSidePanel>
                <div>移动编辑画布</div>
            </div>,
        );

        const panel = view.container.querySelector('[data-side-panel="left"]') as HTMLElement;
        expect(panel).toHaveAttribute("data-compact", "true");
        expect(panel).toHaveStyle({ width: "0px" });
        expect(screen.getByText("移动编辑画布")).toBeVisible();
        expect(screen.queryByRole("separator")).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "展开左侧面板" }));
        expect(panel).toHaveStyle({ width: "300px" });
        expect(screen.getByText("移动工作流")).toBeVisible();
        expect(screen.queryByRole("separator")).not.toBeInTheDocument();
    });
});
