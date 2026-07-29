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
    uploadMedia: vi.fn(),
    getHistory: vi.fn(),
  },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
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
    queue: [],
  });
  useToastStore.getState().clear();
  vi.mocked(playgroundApi.deleteUpload).mockReset();
});

afterEach(() => {
  usePlaygroundStore.setState({
    mode: "t2i",
    inputMedia: [],
    queue: [],
  });
  useToastStore.getState().clear();
  vi.restoreAllMocks();
});

describe("server-confirmed playground media deletion", () => {
  it("keeps an upload visible until the server confirms deletion, then removes it immediately", async () => {
    const serverDelete = deferred<{ ok: boolean; deleted: boolean }>();
    vi.mocked(playgroundApi.deleteUpload).mockReturnValueOnce(serverDelete.promise);

    renderWithIntl(<MediaInput />, { locale: "en" });

    const removeButton = getRemoveButton("delete-me.png");
    fireEvent.click(removeButton);

    expect(playgroundApi.deleteUpload).toHaveBeenCalledWith(uploadedPath);
    expect(removeButton).toBeDisabled();
    expect(screen.getByText("delete-me.png")).toBeInTheDocument();
    expect(usePlaygroundStore.getState().inputMedia).toEqual([uploadedPath]);

    await act(async () => {
      serverDelete.resolve({ ok: true, deleted: true });
      await serverDelete.promise;
    });

    await waitFor(() => {
      expect(screen.queryByText("delete-me.png")).not.toBeInTheDocument();
      expect(usePlaygroundStore.getState().inputMedia).toEqual([]);
    });
  });

  it("preserves the visible upload when the server rejects deletion", async () => {
    const serverDelete = deferred<{ ok: boolean; deleted: boolean }>();
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

  it("removes a queued reference from compose without deleting its source upload", async () => {
    usePlaygroundStore.setState({
      queue: [{
        id: "queued-request",
        mode: "i2i",
        modelId: "gpt-image-2",
        prompt: "Queued edit",
        inputMedia: [uploadedPath],
        parameters: {},
        batchSize: 1,
        status: "pending",
        enqueuedAt: 1,
      }],
    });
    renderWithIntl(<MediaInput />, { locale: "en" });

    fireEvent.click(getRemoveButton("delete-me.png"));

    await waitFor(() => {
      expect(usePlaygroundStore.getState().inputMedia).toEqual([]);
    });
    expect(playgroundApi.deleteUpload).not.toHaveBeenCalled();
    expect(usePlaygroundStore.getState().queue[0].inputMedia).toEqual([uploadedPath]);
  });

  it("replaces a queued first frame without deleting the queued upload", async () => {
    const replacement = "playground/uploads/replacement.png";
    usePlaygroundStore.setState({
      mode: "i2v",
      queue: [{
        id: "queued-video",
        mode: "i2v",
        modelId: "doubao-seedance-2-0-fast-260128",
        prompt: "Queued video",
        inputMedia: [uploadedPath],
        parameters: {},
        batchSize: 1,
        status: "pending",
        enqueuedAt: 1,
      }],
    });
    vi.mocked(playgroundApi.uploadMedia).mockResolvedValueOnce({ path: replacement });
    const { container } = renderWithIntl(<MediaInput />, { locale: "en" });

    const fileInput = container.querySelector('input[type="file"]');
    expect(fileInput).toBeInstanceOf(HTMLInputElement);
    fireEvent.change(fileInput!, {
      target: { files: [new File(["image"], "replacement.png", { type: "image/png" })] },
    });

    await waitFor(() => {
      expect(usePlaygroundStore.getState().inputMedia).toEqual([replacement]);
    });
    expect(playgroundApi.deleteUpload).not.toHaveBeenCalledWith(uploadedPath);
    expect(usePlaygroundStore.getState().queue[0].inputMedia).toEqual([uploadedPath]);
  });
});
