import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { toast, useToastStore } from "@/store/toastStore";

describe("toastStore auto-close transitions", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        useToastStore.getState().clear();
    });

    afterEach(() => {
        useToastStore.getState().clear();
        vi.useRealTimers();
    });

    it("dismisses a progress toast five seconds after it becomes successful", () => {
        const id = toast.progress("Extracting entities");
        toast.update(id, {
            kind: "success",
            title: "Entity extraction done",
            autoCloseMs: 5000,
        });

        vi.advanceTimersByTime(4999);
        expect(useToastStore.getState().toasts).toHaveLength(1);

        vi.advanceTimersByTime(1);
        expect(useToastStore.getState().toasts).toHaveLength(0);
    });

    it("cancels an existing timer when a toast becomes an error", () => {
        const id = toast.success("Temporary success", { autoCloseMs: 1000 });
        toast.update(id, {
            kind: "error",
            title: "Entity extraction failed",
        });

        vi.advanceTimersByTime(10_000);
        expect(useToastStore.getState().toasts).toHaveLength(1);

        toast.dismiss(id);
        expect(useToastStore.getState().toasts).toHaveLength(0);
    });
});
