import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";
import type { CompiledGenerationRequest } from "@/lib/api";

import GenerationRequestReview from "./GenerationRequestReview";

function snapshot(prompt: string, checksumCharacter: string): CompiledGenerationRequest {
  return {
    compiler_version: "1.0",
    compiled_request_id: `genreq_${checksumCharacter.repeat(24)}`,
    checksum: checksumCharacter.repeat(64),
    category: "video",
    mode: "i2v",
    source: "workspace",
    user_prompt: prompt,
    prompt_parts: [{ kind: "user", label: "User prompt", text: prompt, editable: true }],
    target: { surface: "storyboard" },
    provider_requests: [{
      phase: "storyboard_video",
      model: "doubao-seedance-2-0-fast-260128",
      prompt,
      parameters: { duration: 5, resolution: "720p" },
      input_media: ["frames/first.png"],
    }],
  };
}

describe("GenerationRequestReview", () => {
  it("shows the server-compiled request and invalidates it after a draft change", async () => {
    const first = snapshot("First exact prompt", "a");
    const second = snapshot("Second exact prompt", "b");
    const loadFirst = vi.fn().mockResolvedValue(first);
    const loadSecond = vi.fn().mockResolvedValue(second);
    const view = renderWithIntl(
      <GenerationRequestReview fingerprint="first" loadPreview={loadFirst} />,
      { locale: "en" },
    );
    expect(screen.getByTestId("generation-request-review")).toHaveClass("shrink-0");

    fireEvent.click(screen.getByRole("button", { name: /Review provider request/ }));
    await waitFor(() => expect(screen.getByDisplayValue("First exact prompt")).toBeInTheDocument());
    expect(screen.getByText("doubao-seedance-2-0-fast-260128")).toBeInTheDocument();
    expect(screen.getByText("frames/first.png")).toBeInTheDocument();

    view.rerender(
      <GenerationRequestReview fingerprint="second" loadPreview={loadSecond} />,
    );
    expect(screen.queryByDisplayValue("First exact prompt")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Compile current request" }));
    await waitFor(() => expect(screen.getByDisplayValue("Second exact prompt")).toBeInTheDocument());
    expect(loadSecond).toHaveBeenCalledTimes(1);
  });
});
