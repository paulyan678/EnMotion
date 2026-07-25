import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import MediaInput from "@/components/modules/playground/MediaInput";
import { usePlaygroundStore } from "@/components/modules/playground/usePlaygroundStore";
import { playgroundApi } from "@/lib/api";
import { useToastStore } from "@/store/toastStore";
import { renderWithIntl } from "@/test/renderWithIntl";

vi.mock("@/lib/api", () => ({
  API_URL: "http://127.0.0.1:17177",
  playgroundApi: {
    deleteUpload: vi.fn(),
    getHistory: vi.fn(),
  },
}));

function deferred() {
  let resolve!: () => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<void>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function getRemoveButton(fileName: string): HTMLButtonElement {
  const fileTile = screen.getByText(fileName).parentElement;
  const button = fileTile?.querySelector("button");
  if (!(button instanceof HTMLButtonElement)) {
    throw new Error(`Remove button not found for ${fileName}`);
  }
  return button;
}

const uploadedPath = "playground/uploads/delete-me.png";

beforeEach(() => {
  usePlaygroundStore.setState({
    mode: "t2i",
    inputMedia: [uploadedPath],
  });
  useToastStore.getState().clear();
  vi.mocked(playgroundApi.deleteUpload).mockReset();
});

afterEach(() => {
  usePlaygroundStore.setState({
    mode: "t2i",
    inputMedia: [],
  });
  useToastStore.getState().clear();
  vi.restoreAllMocks();
});

describe("server-confirmed playground media deletion", () => {
  it("keeps an upload visible until the server confirms deletion, then removes it immediately", async () => {
    const serverDelete = deferred();
    vi.mocked(playgroundApi.deleteUpload).mockReturnValueOnce(serverDelete.promise);

    renderWithIntl(<MediaInput />, { locale: "en" });

    const removeButton = getRemoveButton("delete-me.png");
    fireEvent.click(removeButton);

    expect(playgroundApi.deleteUpload).toHaveBeenCalledWith(uploadedPath);
    expect(removeButton).toBeDisabled();
    expect(screen.getByText("delete-me.png")).toBeInTheDocument();
    expect(usePlaygroundStore.getState().inputMedia).toEqual([uploadedPath]);

    await act(async () => {
      serverDelete.resolve();
      await serverDelete.promise;
    });

    await waitFor(() => {
      expect(screen.queryByText("delete-me.png")).not.toBeInTheDocument();
      expect(usePlaygroundStore.getState().inputMedia).toEqual([]);
    });
  });

  it("preserves the visible upload when the server rejects deletion", async () => {
    const serverDelete = deferred();
    vi.mocked(playgroundApi.deleteUpload).mockReturnValueOnce(serverDelete.promise);
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    renderWithIntl(<MediaInput />, { locale: "en" });

    const removeButton = getRemoveButton("delete-me.png");
    fireEvent.click(removeButton);

    expect(removeButton).toBeDisabled();
    expect(screen.getByText("delete-me.png")).toBeInTheDocument();

    await act(async () => {
      serverDelete.reject(new Error("server cleanup failed"));
      await serverDelete.promise.catch(() => undefined);
    });

    await waitFor(() => {
      expect(removeButton).not.toBeDisabled();
      expect(screen.getByText("delete-me.png")).toBeInTheDocument();
      expect(usePlaygroundStore.getState().inputMedia).toEqual([uploadedPath]);
    });
    expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      kind: "error",
      title: "Failed to delete media",
    });
  });
});
