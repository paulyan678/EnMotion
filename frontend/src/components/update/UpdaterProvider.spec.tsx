import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithIntl } from "@/test/renderWithIntl";
import { UpdaterProvider } from "@/components/update/UpdaterProvider";
import UpdatePill from "@/components/update/UpdatePill";
import UpdateSettingsCard from "@/components/update/UpdateSettingsCard";
import type { EnMotionUpdaterBridge, UpdaterState } from "@/lib/updater";

describe("desktop updater UI", () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, "enmotionUpdater");
  });

  it("stays hidden in a normal browser with no trusted desktop bridge", async () => {
    renderWithIntl(
      <UpdaterProvider>
        <UpdatePill />
        <UpdateSettingsCard />
      </UpdaterProvider>,
      { locale: "en" },
    );

    await act(async () => Promise.resolve());
    expect(screen.queryByText("EnMotion updates")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /update to/i })).not.toBeInTheDocument();
  });

  it("downloads in the background and waits for an explicit restart", async () => {
    let listener: ((state: UpdaterState) => void) | undefined;
    const startUpdate = vi.fn(async (): Promise<UpdaterState> => ({
      status: "downloading",
      currentVersion: "1.0.0",
      availableVersion: "1.1.0",
      progress: { downloadedBytes: 20, totalBytes: 100 },
    }));
    const installAndRestart = vi.fn(async (): Promise<UpdaterState> => ({
      status: "installing",
      currentVersion: "1.0.0",
      availableVersion: "1.1.0",
    }));
    const bridge: EnMotionUpdaterBridge = {
      getState: vi.fn(async (): Promise<UpdaterState> => ({
        status: "available",
        currentVersion: "1.0.0",
        availableVersion: "1.1.0",
      })),
      checkForUpdates: vi.fn(),
      startUpdate,
      installAndRestart,
      confirmUiReady: vi.fn(),
      subscribe: vi.fn(async (next) => {
        listener = next;
        return () => undefined;
      }),
    };
    window.enmotionUpdater = bridge;

    renderWithIntl(
      <UpdaterProvider>
        <UpdatePill />
        <UpdateSettingsCard />
      </UpdaterProvider>,
      { locale: "en" },
    );

    fireEvent.click(await screen.findByRole("button", { name: "Update to 1.1.0" }));
    await waitFor(() => expect(startUpdate).toHaveBeenCalledOnce());
    await waitFor(() => {
      const progressbars = screen.getAllByRole("progressbar", { name: "Update download progress" });
      expect(progressbars).toHaveLength(2);
      for (const progressbar of progressbars) {
        expect(progressbar).toHaveAttribute("aria-valuenow", "20");
      }
    });

    act(() => {
      listener?.({
        status: "ready",
        currentVersion: "1.0.0",
        availableVersion: "1.1.0",
      });
    });

    const restartButtons = await screen.findAllByRole("button", { name: "Restart to update" });
    fireEvent.click(restartButtons[0]);
    await waitFor(() => expect(installAndRestart).toHaveBeenCalledOnce());
  });

  it("keeps top-bar retry hidden while retaining diagnostics in settings", async () => {
    let listener: ((state: UpdaterState) => void) | undefined;
    window.enmotionUpdater = {
      getState: vi.fn(async (): Promise<UpdaterState> => ({
        status: "idle",
        currentVersion: "1.0.0",
      })),
      checkForUpdates: vi.fn(),
      startUpdate: vi.fn(),
      installAndRestart: vi.fn(),
      confirmUiReady: vi.fn(),
      subscribe: vi.fn(async (next) => {
        listener = next;
        return () => undefined;
      }),
    };

    renderWithIntl(
      <UpdaterProvider>
        <UpdatePill />
        <UpdateSettingsCard />
      </UpdaterProvider>,
      { locale: "en" },
    );

    expect(
      await screen.findByRole("button", { name: "Check for updates" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry update" })).not.toBeInTheDocument();

    act(() => {
      listener?.({
        status: "error",
        currentVersion: "1.0.0",
        error: "network unavailable",
      });
    });

    expect(await screen.findByRole("button", { name: "Retry update" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Retry update" })).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Confirm that you are signed in and online, then retry.",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("version 1.0.0");
  });
});
