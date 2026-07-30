import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";
import { LightboxProvider } from "./LightboxProvider";
import PreviewImage from "./PreviewImage";

function renderPreview(ui: React.ReactElement) {
    return renderWithIntl(<LightboxProvider>{ui}</LightboxProvider>);
}

describe("PreviewImage responsive media", () => {
    it("keeps only the close action in the fullscreen preview toolbar", () => {
        renderPreview(
            <PreviewImage
                src="/files/assets/hero.png"
                alt="角色预览"
                clickToLightbox
                alwaysShowMagnify
            />,
        );

        fireEvent.click(screen.getByRole("button", { name: "放大查看" }));

        expect(screen.getByRole("dialog", { name: "角色预览" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "关闭" })).toHaveAttribute(
            "title",
            "关闭（退出键）",
        );
        expect(screen.queryByRole("button", { name: "复制地址" })).not.toBeInTheDocument();
        expect(screen.queryByRole("link", { name: "在新标签打开" })).not.toBeInTheDocument();
    });

    it("renders a typed srcset with stable geometry and keeps loaded state across re-signing", async () => {
        const common = {
            alt: "Hero",
            mediaKey: `${"a".repeat(64)}:${"b".repeat(64)}`,
            intrinsicWidth: 1024,
            intrinsicHeight: 768,
            sizes: "(max-width: 767px) 50vw, 20vw",
            loading: "eager" as const,
            fetchPriority: "high" as const,
            sizeBucket: "large" as const,
            noLightbox: true,
            responsiveSources: [
                { src: "derivatives/hero/w96.webp", width: 96, type: "image/webp" },
                { src: "derivatives/hero/w384.webp", width: 384, type: "image/webp" },
            ],
        };
        const view = renderPreview(
            <PreviewImage
                {...common}
                src="https://cdn.example/hero.png?signature=one"
            />,
        );

        const image = screen.getByRole("img", { name: "Hero" });
        const source = view.container.querySelector("source");
        expect(source).toHaveAttribute("type", "image/webp");
        expect(source).toHaveAttribute("sizes", common.sizes);
        expect(source?.getAttribute("srcset")).toContain(
            "/files/derivatives/hero/w384.webp 384w",
        );
        expect(image.getAttribute("src")).toContain(
            "/files/derivatives/hero/w384.webp",
        );
        expect(image).toHaveAttribute("width", "1024");
        expect(image).toHaveAttribute("height", "768");
        expect(image).toHaveAttribute("loading", "eager");
        expect(image).toHaveAttribute("fetchpriority", "high");

        fireEvent.load(image);
        expect(screen.queryByRole("status")).not.toBeInTheDocument();
        view.rerender(
            <LightboxProvider>
                <PreviewImage
                    {...common}
                    src="https://cdn.example/hero.png?signature=two"
                />
            </LightboxProvider>,
        );

        expect(screen.getByRole("img", { name: "Hero" })).toBe(image);
        expect(screen.queryByRole("status")).not.toBeInTheDocument();

        view.rerender(
            <LightboxProvider>
                <PreviewImage
                    {...common}
                    mediaKey={`${"a".repeat(64)}:${"c".repeat(64)}`}
                    src="https://cdn.example/hero-v2.png?signature=three"
                />
            </LightboxProvider>,
        );
        await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    });

    it("falls back to one cache-busted original when a derivative fails", async () => {
        const view = renderPreview(
            <PreviewImage
                src="/files/assets/hero.png"
                alt="回退主角"
                noLightbox
                sizeBucket="large"
                responsiveSources={[
                    {
                        src: "derivatives/hero/w384.webp",
                        width: 384,
                        type: "image/webp",
                    },
                ]}
            />,
        );
        const image = screen.getByRole("img", { name: "回退主角" });
        expect(image.getAttribute("src")).toContain(
            "/files/derivatives/hero/w384.webp",
        );
        fireEvent.error(image);

        await waitFor(() => {
            expect(view.container.querySelector("source")).not.toBeInTheDocument();
            expect(image.getAttribute("src")).toContain("/files/assets/hero.png");
            expect(image.getAttribute("src")).toContain("__r=1");
        });
    });
});
