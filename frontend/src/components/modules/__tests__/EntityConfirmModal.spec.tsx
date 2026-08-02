import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import EntityConfirmModal from "@/components/modules/EntityConfirmModal";
import { renderWithIntl } from "@/test/renderWithIntl";

const preview = {
  characters: [{ name: "Hero" }],
  scenes: [{ name: "Station" }],
  props: [{ name: "Ticket" }],
};

describe("EntityConfirmModal", () => {
  it("cannot be dismissed by Escape or the backdrop while apply is in flight", () => {
    const onDiscard = vi.fn();
    renderWithIntl(
      <EntityConfirmModal
        isOpen
        preview={preview}
        currentCounts={{ characters: 0, scenes: 0, props: 0 }}
        onConfirm={vi.fn()}
        onDiscard={onDiscard}
        applying
      />,
      { locale: "en" },
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-busy", "true");
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(dialog.parentElement as HTMLElement);

    expect(onDiscard).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Apply extracted assets" })).toBeDisabled();
  });

  it("still dismisses from Escape when no apply is running", () => {
    const onDiscard = vi.fn();
    renderWithIntl(
      <EntityConfirmModal
        isOpen
        preview={preview}
        currentCounts={{ characters: 0, scenes: 0, props: 0 }}
        onConfirm={vi.fn()}
        onDiscard={onDiscard}
      />,
      { locale: "en" },
    );

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onDiscard).toHaveBeenCalledOnce();
  });
});
