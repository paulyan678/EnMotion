import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EnMotionBranding from "@/components/layout/EnMotionBranding";
import { useSettingsStore } from "@/store/settingsStore";
import { renderWithIntl } from "@/test/renderWithIntl";

vi.mock("@/lib/useHydrated", () => ({
  useHydrated: () => true,
}));

describe("EnMotionBranding", () => {
  beforeEach(() => {
    useSettingsStore.setState({ theme: "atelier-dark" });
  });

  it("uses the official dark lockup on dark themes", () => {
    renderWithIntl(<EnMotionBranding showSlogan={false} />);

    expect(screen.getByRole("img", { name: "EnMotion 工作室" })).toHaveAttribute(
      "src",
      "enmotion-lockup-on-dark.svg",
    );
  });

  it("uses the official light-surface lockup on light themes", () => {
    useSettingsStore.setState({ theme: "brand-light" });
    renderWithIntl(<EnMotionBranding showSlogan={false} />);

    expect(screen.getByRole("img", { name: "EnMotion 工作室" })).toHaveAttribute(
      "src",
      "enmotion-lockup.svg",
    );
  });
});
