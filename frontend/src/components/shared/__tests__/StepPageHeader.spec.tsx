import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import StepPageHeader from "@/components/shared/StepPageHeader";

describe("StepPageHeader", () => {
  it("renders only the step title and functional controls", () => {
    const onPrimaryAction = vi.fn();
    const { container } = render(
      <StepPageHeader
        title="故事板"
        trailing={(
          <button type="button" onClick={onPrimaryAction}>
            生成
          </button>
        )}
      />,
    );

    expect(screen.getByRole("heading", { level: 1, name: "故事板" })).toBeInTheDocument();
    expect(container.querySelector("header")).toHaveClass("py-3");
    expect(container.querySelector("header > div")).toHaveClass("flex-wrap");
    expect(container.querySelector("p")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    expect(onPrimaryAction).toHaveBeenCalledTimes(1);
  });

  it("does not reserve an actions area when a step has no controls", () => {
    const { container } = render(<StepPageHeader title="动效生成" />);

    expect(screen.getByRole("heading", { name: "动效生成" })).toBeInTheDocument();
    expect(container.querySelector("header")?.textContent).toBe("动效生成");
    expect(container.querySelector("button")).not.toBeInTheDocument();
  });
});
