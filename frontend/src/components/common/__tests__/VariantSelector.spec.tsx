import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";

vi.mock("@/lib/apiUrl", () => ({
  API_URL: "http://127.0.0.1:17177",
}));

import { VariantSelector } from "../VariantSelector";

describe("VariantSelector", () => {
  it("shows the first available variant when the selected id is absent or stale", () => {
    renderWithIntl(
      <VariantSelector
        asset={{
          selected_id: "missing",
          variants: [
            { id: "first", url: "uploads/tester.png", created_at: 1 },
          ],
        }}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        onGenerate={vi.fn()}
        isGenerating={false}
      />,
    );

    expect(screen.getByRole("img", { name: "已选择的变体" })).toHaveAttribute(
      "src",
      "http://127.0.0.1:17177/files/uploads/tester.png",
    );
  });
});
