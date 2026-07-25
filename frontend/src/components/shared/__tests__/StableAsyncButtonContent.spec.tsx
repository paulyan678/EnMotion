import { render, screen } from "@testing-library/react";
import { Check } from "lucide-react";
import { describe, expect, it } from "vitest";

import StableAsyncButtonContent from "@/components/shared/StableAsyncButtonContent";

describe("StableAsyncButtonContent", () => {
  it("keeps idle and loading labels in a stable shared grid cell", () => {
    const { container, rerender } = render(
      <StableAsyncButtonContent
        loading={false}
        idleLabel="Save settings"
        loadingLabel="Saving…"
        idleIcon={<Check />}
      />,
    );

    expect(screen.getByText("Save settings").parentElement).toHaveAttribute("aria-hidden", "false");
    expect(screen.getByText("Saving…").parentElement).toHaveAttribute("aria-hidden", "true");

    rerender(
      <StableAsyncButtonContent
        loading
        idleLabel="Save settings"
        loadingLabel="Saving…"
        idleIcon={<Check />}
      />,
    );

    expect(screen.getByText("Save settings").parentElement).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Saving…");
    expect(container.querySelector('[data-loading-indicator="static"]')).toBeInTheDocument();
  });

  it("does not use a transform-based spinner that can corrupt Safari text layers", () => {
    const { container } = render(
      <StableAsyncButtonContent
        loading
        idleLabel="Sign in"
        loadingLabel="Signing in…"
      />,
    );

    expect(container.querySelector(".animate-spin")).not.toBeInTheDocument();
    expect(container.querySelector('[data-loading-indicator="static"]')).toBeInTheDocument();
  });
});
