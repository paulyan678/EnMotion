import { StrictMode } from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useToastStore } from "@/store/toastStore";
import { useProjectStore } from "@/store/projectStore";
import { renderWithIntl } from "@/test/renderWithIntl";

const apiMocks = vi.hoisted(() => ({
  createSeriesV2: vi.fn(),
  getProjects: vi.fn(),
  getSeriesEpisodes: vi.fn(),
  listSeries: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMocks,
}));

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({
    serverMode: false,
    status: "disabled",
    user: null,
  }),
}));

import Home, { CreateSeriesDialog } from "@/app/page";

describe("CreateSeriesDialog request recovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useToastStore.getState().clear();
    useProjectStore.setState({ projects: [], seriesList: [] });
    apiMocks.getProjects.mockResolvedValue([]);
    apiMocks.getSeriesEpisodes.mockResolvedValue([]);
    apiMocks.listSeries.mockResolvedValue([]);
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.location.hash = "";
  });

  it("restores the Create button and gives localized duplicate-safe guidance after failure", async () => {
    let rejectRequest!: (error: Error) => void;
    apiMocks.createSeriesV2.mockReturnValue(new Promise((_resolve, reject) => {
      rejectRequest = reject;
    }));
    const onClose = vi.fn();

    renderWithIntl(<CreateSeriesDialog isOpen onClose={onClose} />);
    fireEvent.change(screen.getByPlaceholderText("例如：我的漫剧系列"), {
      target: { value: "潮汐邮局" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建系列" }));

    expect(screen.getByRole("button", { name: "创建中..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "创建中..." })).toHaveAttribute("aria-busy", "true");

    rejectRequest(new Error("timeout"));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "创建系列" })).toBeEnabled();
    });
    expect(screen.getByRole("button", { name: "创建系列" })).toHaveAttribute("aria-busy", "false");
    expect(onClose).not.toHaveBeenCalled();
    expect(console.error).toHaveBeenCalledWith("Failed to create series:", expect.any(Error));
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        kind: "error",
        title: "无法确认系列是否已创建",
        body: "请先同步工作区确认，再决定是否重试，以免重复创建。",
      }),
    ]);
  });

  it("uses definitive failure copy when the server rejected creation", async () => {
    apiMocks.createSeriesV2.mockRejectedValue({
      response: { status: 422 },
    });

    renderWithIntl(<CreateSeriesDialog isOpen onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("例如：我的漫剧系列"), {
      target: { value: "无效系列" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建系列" }));

    await waitFor(() => {
      expect(useToastStore.getState().toasts).toEqual([
        expect.objectContaining({
          kind: "error",
          title: "系列未创建",
          body: "请检查系列信息和账号状态，然后重试。",
        }),
      ]);
    });
  });

  it("keeps 5xx failures duplicate-safe because creation may have committed", async () => {
    apiMocks.createSeriesV2.mockRejectedValue({
      response: { status: 502 },
    });

    renderWithIntl(<CreateSeriesDialog isOpen onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("例如：我的漫剧系列"), {
      target: { value: "网关超时系列" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建系列" }));

    await waitFor(() => {
      expect(useToastStore.getState().toasts).toEqual([
        expect.objectContaining({
          kind: "error",
          title: "无法确认系列是否已创建",
          body: "请先同步工作区确认，再决定是否重试，以免重复创建。",
        }),
      ]);
    });
  });

  it("prevents dismissal while creation is active and navigates only after success", async () => {
    let resolveRequest!: (value: { id: string }) => void;
    apiMocks.createSeriesV2.mockReturnValue(new Promise((resolve) => {
      resolveRequest = resolve;
    }));
    const onClose = vi.fn();

    renderWithIntl(<CreateSeriesDialog isOpen onClose={onClose} />);
    fireEvent.change(screen.getByPlaceholderText("例如：我的漫剧系列"), {
      target: { value: "潮汐邮局" },
    });
    fireEvent.change(screen.getByPlaceholderText("简要描述这个系列..."), {
      target: { value: "提交时的描述" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建系列" }));

    expect(screen.getByRole("button", { name: "关闭" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(screen.getByPlaceholderText("例如：我的漫剧系列")).toBeDisabled();
    expect(screen.getByPlaceholderText("简要描述这个系列...")).toBeDisabled();
    expect(screen.getByRole("button", { name: /我有剧本/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /我直接创作/ })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();

    resolveRequest({ id: "created-series" });

    await waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(window.location.hash).toBe("#/series/created-series");
    });
  });

  it("completes normally when mounted under React Strict Mode", async () => {
    let resolveRequest!: (value: { id: string }) => void;
    apiMocks.createSeriesV2.mockReturnValue(new Promise((resolve) => {
      resolveRequest = resolve;
    }));
    const onClose = vi.fn();

    renderWithIntl(
      <StrictMode>
        <CreateSeriesDialog isOpen onClose={onClose} />
      </StrictMode>
    );
    fireEvent.change(screen.getByPlaceholderText("例如：我的漫剧系列"), {
      target: { value: "严格模式系列" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建系列" }));

    resolveRequest({ id: "strict-series" });

    await waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(window.location.hash).toBe("#/series/strict-series");
    });
  });

  it("suppresses a late response after the dialog unmounts", async () => {
    let resolveRequest!: (value: { id: string }) => void;
    apiMocks.createSeriesV2.mockReturnValue(new Promise((resolve) => {
      resolveRequest = resolve;
    }));
    const onClose = vi.fn();
    const rendered = renderWithIntl(<CreateSeriesDialog isOpen onClose={onClose} />);
    fireEvent.change(screen.getByPlaceholderText("例如：我的漫剧系列"), {
      target: { value: "潮汐邮局" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建系列" }));

    rendered.unmount();
    resolveRequest({ id: "late-series" });
    await Promise.resolve();

    expect(onClose).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("");
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it("surfaces an initial series sync failure instead of rendering a false empty state", async () => {
    apiMocks.listSeries.mockRejectedValue(new Error("timeout"));
    window.location.hash = "#/";

    renderWithIntl(<Home />);

    await waitFor(() => {
      expect(useToastStore.getState().toasts).toEqual([
        expect.objectContaining({
          kind: "error",
          title: "系列同步失败",
          body: "请确认 EnMotion 已连接，然后重新同步。",
        }),
      ]);
    });
  });
});
