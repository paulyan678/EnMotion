import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderWithIntl } from "@/test/renderWithIntl";
import type { Character } from "@/store/projectStore";

vi.mock("@/lib/apiUrl", () => ({
  API_URL: "http://127.0.0.1:17177",
}));

import AssetCard from "../AssetCard";

describe("AssetCard", () => {
  it("renders a global character's selected reference-sheet image", () => {
    const tester = {
      id: "tester",
      name: "Tester",
      description: "Shared character",
      source: "global",
      reference_sheet: {
        selected_image_id: "tester-image",
        image_variants: [
          { id: "tester-image", url: "assets/tester.png", created_at: 1 },
        ],
      },
    } satisfies Character;

    renderWithIntl(<AssetCard asset={tester} type="characters" />);

    const image = screen.getByRole("img", { name: "Tester" });
    expect(image).toHaveAttribute(
      "src",
      "http://127.0.0.1:17177/files/assets/tester.png",
    );
    expect(image).toHaveAttribute("loading", "lazy");
    expect(image).toHaveAttribute("decoding", "async");

    fireEvent.error(image);
    expect(screen.getByRole("img", { name: "Tester" })).toHaveAttribute(
      "src",
      "http://127.0.0.1:17177/files/assets/tester.png?__r=1",
    );
  });
});
