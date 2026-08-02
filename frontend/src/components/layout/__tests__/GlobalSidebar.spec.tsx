import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import GlobalSidebar from "../GlobalSidebar";
import { renderWithIntl } from "@/test/renderWithIntl";

describe("GlobalSidebar API activity navigation", () => {
  it("adds a bilingual Generation Records destination and routes to its dashboard", () => {
    const onTabChange = vi.fn();
    renderWithIntl(
      <GlobalSidebar activeTab="apiCalls" onTabChange={onTabChange} />,
      { locale: "en" },
    );

    const button = screen.getByRole("button", { name: "Generation Records" });
    expect(button).toHaveAttribute("aria-current", "page");
    fireEvent.click(button);
    expect(onTabChange).toHaveBeenCalledWith("apiCalls");
    expect(window.location.hash).toBe("#/api-calls");
  });

  it("uses the Chinese navigation label", () => {
    renderWithIntl(
      <GlobalSidebar activeTab="workspace" onTabChange={vi.fn()} />,
      { locale: "zh" },
    );
    expect(screen.getByRole("button", { name: "生成记录" })).toBeInTheDocument();
  });

  it("starts with navigation instead of rendering a decorative brand block", () => {
    renderWithIntl(
      <GlobalSidebar activeTab="workspace" onTabChange={vi.fn()} />,
      { locale: "zh" },
    );

    expect(screen.queryByText("将纷扰渲染成叙事")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "EnMotion 工作室 · 工作区" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "工作区" })).toBeInTheDocument();
  });
});
