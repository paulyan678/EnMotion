import { screen } from "@testing-library/react";
import { BookOpen } from "lucide-react";
import { describe, expect, it, vi } from "vitest";

import PipelineSidebar from "../PipelineSidebar";
import { renderWithIntl } from "@/test/renderWithIntl";

describe("PipelineSidebar", () => {
  it("starts with pipeline content when navigation is provided by the top bar", () => {
    const { container } = renderWithIntl(
      <PipelineSidebar
        activeStep="script"
        onStepChange={vi.fn()}
        steps={[{ id: "script", label: "Script", icon: BookOpen }]}
      />,
      { locale: "en" },
    );

    expect(screen.queryByAltText("EnMotion")).not.toBeInTheDocument();
    expect(screen.queryByText("Render Noise into Narrative")).not.toBeInTheDocument();
    expect(container.querySelector("aside")?.firstElementChild?.tagName).toBe("NAV");
    expect(screen.getByRole("button", { name: /Script/i })).toBeInTheDocument();
  });

  it("keeps inactive workflow status text and icons at accessible token opacity", () => {
    renderWithIntl(
      <PipelineSidebar
        activeStep="script"
        onStepChange={vi.fn()}
        steps={[
          { id: "script", label: "Script", icon: BookOpen },
          { id: "art", label: "Art Direction", icon: BookOpen, status: "idle" },
          {
            id: "assembly",
            label: "Assembly",
            icon: BookOpen,
            status: "gated",
            statusLabel: "Waiting for storyboard",
          },
        ]}
      />,
      { locale: "en" },
    );

    const artButton = screen.getByRole("button", { name: /Art Direction/i });
    const assemblyButton = screen.getByRole("button", { name: /Assembly/i });

    expect(screen.getByText("STEP 02")).not.toHaveClass("opacity-70");
    expect(artButton.querySelector(".border-text-muted")).toBeInTheDocument();
    expect(assemblyButton).not.toHaveClass("opacity-60");
    expect(
      assemblyButton.querySelector('[aria-label="Complete upstream steps first"]'),
    ).toHaveClass("text-text-muted");
  });
});
