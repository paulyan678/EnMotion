import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ShotCard, { type ShotNode } from "@/components/modules/storyboard-r2v/ShotCard";
import { renderWithIntl } from "@/test/renderWithIntl";

const shot: ShotNode = {
  id: "frame-1",
  prompt: "A quiet establishing shot",
  tabMode: "t2i_i2v",
};

function renderCard(options: { locale?: "en" | "zh"; deleting?: boolean } = {}) {
  const onDelete = vi.fn();
  renderWithIntl(
    <ShotCard
      shot={shot}
      index={0}
      totalShots={1}
      characters={[]}
      scenes={[]}
      props={[]}
      onUpdatePrompt={vi.fn()}
      onUpdateField={vi.fn()}
      onGenerateT2I={vi.fn()}
      onGenerateVideo={vi.fn()}
      onDelete={onDelete}
      isDeleting={options.deleting}
      onMoveUp={vi.fn()}
      onMoveDown={vi.fn()}
      onDuplicate={vi.fn()}
      onOpenDrawer={vi.fn()}
      expanded={false}
      onToggleExpanded={vi.fn()}
    />,
    { locale: options.locale ?? "en" },
  );
  return onDelete;
}

describe("ShotCard frame deletion", () => {
  it("exposes the frame delete action in English and invokes it once", () => {
    const onDelete = renderCard();

    fireEvent.click(screen.getByRole("button", { name: "Delete shot" }));

    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  it("uses the Chinese label and blocks clicks while server deletion is pending", () => {
    const onDelete = renderCard({ locale: "zh", deleting: true });
    const button = screen.getByRole("button", { name: "删除镜头" });

    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    fireEvent.click(button);
    expect(onDelete).not.toHaveBeenCalled();
  });
});
