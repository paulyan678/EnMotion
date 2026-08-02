import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type CompiledGenerationRequest } from "@/lib/api";
import { renderWithIntl } from "@/test/renderWithIntl";

import TextGenerationRequestDialog from "./TextGenerationRequestDialog";

function compiled(prompt: string, checksumCharacter: string): CompiledGenerationRequest {
  return {
    compiler_version: "1.0",
    compiled_request_id: `genreq_${checksumCharacter.repeat(24)}`,
    checksum: checksumCharacter.repeat(64),
    category: "text",
    mode: "entity_extraction",
    source: "workspace",
    user_prompt: prompt,
    prompt_parts: [],
    target: { project_id: "project-1" },
    provider_requests: [{
      phase: "extract_entities",
      model: "qwen3.7-max",
      prompt,
      parameters: {},
      input_media: [],
    }],
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TextGenerationRequestDialog", () => {
  it("blocks execution until the current draft has been visibly reviewed", async () => {
    vi.spyOn(api, "getTextGenerationConfig").mockResolvedValue({
      operation: "entity_extraction",
      model: "qwen3.7-max",
      instructions: "Initial instructions",
      source_text: "Initial source",
      output_contract: "Return valid JSON",
    });
    let resolveSecondPreview!: (value: CompiledGenerationRequest) => void;
    const secondPreview = new Promise<CompiledGenerationRequest>((resolve) => {
      resolveSecondPreview = resolve;
    });
    const preview = vi.spyOn(api, "previewTextGeneration")
      .mockResolvedValueOnce(compiled("Initial exact prompt", "a"))
      .mockReturnValueOnce(secondPreview);
    const execute = vi.spyOn(api, "executeTextGeneration").mockResolvedValue({
      characters: [],
      scenes: [],
      props: [],
    });
    const onCompleted = vi.fn();
    const onClose = vi.fn();

    renderWithIntl(
      <TextGenerationRequestDialog
        open
        scriptId="project-1"
        operation="entity_extraction"
        onClose={onClose}
        onCompleted={onCompleted}
      />,
      { locale: "en" },
    );

    const run = await screen.findByRole("button", { name: "Run request" });
    await waitFor(() => expect(run).toBeEnabled());
    expect(screen.getByDisplayValue("Initial exact prompt")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "Edited instructions" },
    });
    expect(run).toBeDisabled();
    expect(screen.queryByDisplayValue("Initial exact prompt")).not.toBeInTheDocument();

    resolveSecondPreview(compiled("Edited exact prompt", "b"));
    await waitFor(() => expect(run).toBeEnabled());
    fireEvent.click(run);

    await waitFor(() => expect(execute).toHaveBeenCalledWith("project-1", {
      operation: "entity_extraction",
      model: "qwen3.7-max",
      instructions: "Edited instructions",
      source_text: "Initial source",
      compiled_request_checksum: "b".repeat(64),
    }));
    expect(onCompleted).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(preview).toHaveBeenCalledTimes(2);
  });
});
