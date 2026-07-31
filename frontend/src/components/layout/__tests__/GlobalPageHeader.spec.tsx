import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import GlobalPageHeader from "@/components/layout/GlobalPageHeader";

describe("GlobalPageHeader", () => {
  it("owns the primary-page spacing without a separating border", () => {
    render(
      <GlobalPageHeader
        title="创作台"
        actions={<button type="button">操作</button>}
      />,
    );

    const title = screen.getByRole("heading", { name: "创作台" });
    const header = title.closest("header");

    expect(header).toHaveAttribute("data-global-page-header");
    expect(header).toHaveClass(
      "px-4",
      "pb-3",
      "pt-5",
      "md:px-7",
      "md:pt-6",
    );
    expect(header).not.toHaveClass("border-b");
    expect(screen.getByRole("button", { name: "操作" })).toBeInTheDocument();
  });
});
