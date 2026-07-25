import { describe, expect, it } from "vitest";

import { reconcileT2IWorkbenchAfterDelete } from "@/components/modules/storyboard-r2v/t2iDeleteReconcile";

describe("T2I deletion race reconciliation", () => {
    it("does not let a delayed workbench patch resurrect the deleted URL", () => {
        const reconciled = reconcileT2IWorkbenchAfterDelete(
            {
                t2i_image_urls: ["kept.png"],
                t2i_selected_index: 0,
            },
            {
                t2i_image_urls: ["deleted.png", "kept.png", "new.png"],
                t2i_selected_index: 2,
                workbench_generate_count: 4,
            },
            "deleted.png",
            ["deleted.png", "kept.png"],
        );

        expect(reconciled).toEqual({
            t2i_image_urls: ["kept.png", "new.png"],
            t2i_selected_index: 1,
            workbench_generate_count: 4,
        });
    });

    it("translates a selection-only patch by URL after an earlier image is deleted", () => {
        const reconciled = reconcileT2IWorkbenchAfterDelete(
            {
                t2i_image_urls: ["second.png", "third.png"],
                t2i_selected_index: 0,
            },
            { t2i_selected_index: 2 },
            "first.png",
            ["first.png", "second.png", "third.png"],
        );

        expect(reconciled).toEqual({ t2i_selected_index: 1 });
    });

    it("uses the server selection when the queued selection was deleted", () => {
        const reconciled = reconcileT2IWorkbenchAfterDelete(
            {
                t2i_image_urls: ["kept.png"],
                t2i_selected_index: 0,
            },
            {
                t2i_image_urls: ["deleted.png", "kept.png"],
                t2i_selected_index: 0,
            },
            "deleted.png",
            ["deleted.png", "kept.png"],
        );

        expect(reconciled).toEqual({
            t2i_image_urls: ["kept.png"],
            t2i_selected_index: 0,
        });
    });
});
