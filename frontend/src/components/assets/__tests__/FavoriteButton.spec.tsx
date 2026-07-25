import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";

import FavoriteButton from "../FavoriteButton";

describe("FavoriteButton", () => {
  it.each([
    { locale: "en" as const, add: "Add to Favorites", remove: "Remove from Favorites" },
    { locale: "zh" as const, add: "添加到收藏", remove: "从收藏中移除" },
  ])("exposes localized desired-state semantics in $locale", ({ locale, add, remove }) => {
    const onChange = vi.fn();
    const rendered = renderWithIntl(
      <FavoriteButton pressed={false} onChange={onChange} />,
      { locale },
    );

    const addButton = screen.getByRole("button", { name: add });
    expect(addButton).toHaveAttribute("aria-pressed", "false");
    expect(addButton).toHaveClass("min-h-11", "min-w-11", "bg-black/85");
    fireEvent.click(addButton);
    expect(onChange).toHaveBeenCalledWith(true);

    rendered.rerender(<FavoriteButton pressed onChange={onChange} />);
    const removeButton = screen.getByRole("button", { name: remove });
    expect(removeButton).toHaveAttribute("aria-pressed", "true");
    expect(removeButton).toHaveClass("bg-status-starred-bg");
  });

  it("deduplicates activation while a server write is pending", () => {
    const onChange = vi.fn();
    renderWithIntl(
      <FavoriteButton pressed={false} pending onChange={onChange} />,
      { locale: "en" },
    );

    const button = screen.getByRole("button", { name: "Add to Favorites" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    fireEvent.click(button);
    fireEvent.keyDown(button, { key: "Enter" });
    fireEvent.keyDown(button, { key: " " });
    expect(onChange).not.toHaveBeenCalled();
  });

  it.each([
    { locale: "en" as const, label: "Favorited" },
    { locale: "zh" as const, label: "已收藏" },
  ])("renders the localized pressed label in $locale", ({ locale, label }) => {
    renderWithIntl(
      <FavoriteButton pressed variant="labeled" onChange={vi.fn()} />,
      { locale },
    );

    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("supports Enter and Space without bubbling into an asset card", () => {
    const onChange = vi.fn();
    const onCardKeyDown = vi.fn();
    renderWithIntl(
      <div onKeyDown={onCardKeyDown}>
        <FavoriteButton pressed={false} onChange={onChange} />
      </div>,
      { locale: "en" },
    );

    const button = screen.getByRole("button", { name: "Add to Favorites" });
    fireEvent.keyDown(button, { key: "Enter" });
    fireEvent.keyDown(button, { key: " " });
    expect(onChange).toHaveBeenNthCalledWith(1, true);
    expect(onChange).toHaveBeenNthCalledWith(2, true);
    expect(onCardKeyDown).not.toHaveBeenCalled();
  });
});
