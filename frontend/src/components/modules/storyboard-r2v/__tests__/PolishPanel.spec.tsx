import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PolishPanel from "@/components/modules/storyboard-r2v/PolishPanel";
import { api } from "@/lib/api";
import { renderWithIntl } from "@/test/renderWithIntl";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PolishPanel generation-time model selection", () => {
  it("sends the model chosen for the current polish request", async () => {
    const polishSpy = vi.spyOn(api, "polishVideoPrompt").mockResolvedValue({
      prompt_cn: "机器人转身并挥手。",
      prompt_en: "The robot turns and waves.",
    });

    renderWithIntl(
      <PolishPanel
        prompt="Robot waves"
        scriptId="project-1"
        imageUrls={["asset://robot.png"]}
        onApply={vi.fn()}
      />,
      { locale: "en" },
    );

    fireEvent.change(screen.getByLabelText("Polish model"), {
      target: { value: "deepseek-v4-pro" },
    });
    fireEvent.click(screen.getByRole("button", { name: "AI Polish" }));

    await waitFor(() => expect(polishSpy).toHaveBeenCalledWith(
      "Robot waves",
      "",
      "project-1",
      "",
      ["asset://robot.png"],
      "deepseek-v4-pro",
    ));
    expect(await screen.findByText("The robot turns and waves.")).toBeInTheDocument();
  });
});
