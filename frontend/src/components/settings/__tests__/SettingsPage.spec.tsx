import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";
import { useSettingsStore } from "@/store/settingsStore";

const apiMocks = vi.hoisted(() => ({
  getEnvConfig: vi.fn(),
  fetchPromptDefaults: vi.fn(),
  saveEnvConfig: vi.fn(),
  inspectApiKeys: vi.fn(),
}));

const authMocks = vi.hoisted(() => ({
  value: { serverMode: false, user: null as null | { role: string } },
}));

vi.mock("@/lib/api", () => ({
  api: apiMocks,
}));

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => authMocks.value,
}));

import SettingsPage from "../SettingsPage";

describe("SettingsPage categories", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.__ENMOTION_RUNTIME_CONFIG__ = undefined;
    useSettingsStore.setState({
      theme: "atelier-dark",
      animations: true,
    });
    authMocks.value = { serverMode: false, user: null };
    apiMocks.getEnvConfig.mockResolvedValue({
      NEWAPI_BASE_URL: "https://api.example.test/v1",
      NEWAPI_CHAT_MODEL: "deepseek-v4-pro",
      NEWAPI_IMAGE_MODEL: "gpt-image-2",
      NEWAPI_VIDEO_MODEL: "doubao-seedance-2-0-fast-260128",
      secrets_configured: {},
    });
    apiMocks.fetchPromptDefaults.mockResolvedValue({});
    apiMocks.saveEnvConfig.mockResolvedValue({ status: "success" });
    apiMocks.inspectApiKeys.mockImplementation(async (reveal: boolean) => ({
      revealed: reveal,
      items: [
        {
          display_name: "GPT Image 2",
          capability: "image",
          api_key_field: "NEWAPI_GPT_IMAGE_2_API_KEY",
          configured: true,
          active: true,
          in_use: true,
          value: reveal ? "full-admin-secret-1234" : "••••••••1234",
        },
      ],
    }));
  });

  it("keeps generation defaults out of Settings and retains provider administration", async () => {
    renderWithIntl(<SettingsPage />);

    await waitFor(() => expect(apiMocks.getEnvConfig).toHaveBeenCalledTimes(1));

    const title = screen.getByRole("heading", { name: "设置" });
    expect(title.previousElementSibling).toBeNull();
    expect(screen.queryByText("通用与主题")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "设置分类" })).not.toBeInTheDocument();
    expect(screen.queryByText("通用")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "主题与动效" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "模型访问权限" })).toBeInTheDocument();
    expect(screen.queryByText("选择界面配色方案")).not.toBeInTheDocument();
    expect(screen.queryByText("暖石墨 · 青绿色")).not.toBeInTheDocument();
    expect(screen.queryByText("关闭后将降低过渡动画，并尊重系统的减少动态偏好。")).not.toBeInTheDocument();
    expect(screen.queryByText("包含面板进出、签名动效等")).not.toBeInTheDocument();
    expect(screen.queryByText("配置服务商地址，以及每个可用模型对应的密钥。")).not.toBeInTheDocument();

    expect(screen.queryByRole("tab", { name: "模型" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "默认提示词" })).not.toBeInTheDocument();

    expect(screen.queryByText("当前聊天模型")).not.toBeInTheDocument();
    expect(screen.queryByText("当前图像模型")).not.toBeInTheDocument();
    expect(screen.queryByText("当前视频模型")).not.toBeInTheDocument();
    expect(screen.queryByText("供应商模型标识符")).not.toBeInTheDocument();
    expect(screen.queryByText("gpt-image-2")).not.toBeInTheDocument();
  });

  it("lets an Admin inspect masked keys and explicitly reveal or hide them", async () => {
    authMocks.value = { serverMode: true, user: { role: "admin" } };
    renderWithIntl(<SettingsPage />);

    await waitFor(() => expect(apiMocks.getEnvConfig).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "查看已保存的密钥" }));
    await waitFor(() => expect(screen.getByText("••••••••1234")).toBeInTheDocument());
    expect(screen.getByText("正在使用")).toBeInTheDocument();
    expect(screen.queryByText("full-admin-secret-1234")).not.toBeInTheDocument();
    expect(apiMocks.inspectApiKeys).toHaveBeenLastCalledWith(false);

    fireEvent.click(screen.getByRole("button", { name: "显示完整密钥" }));
    await waitFor(() => expect(screen.getByText("full-admin-secret-1234")).toBeInTheDocument());
    expect(apiMocks.inspectApiKeys).toHaveBeenLastCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: "隐藏完整密钥" }));
    expect(screen.queryByText("full-admin-secret-1234")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("••••••••1234")).toBeInTheDocument());
    expect(apiMocks.inspectApiKeys).toHaveBeenLastCalledWith(false);
  });

  it("does not expose API key controls to a non-Admin server account", async () => {
    authMocks.value = { serverMode: true, user: { role: "user" } };
    renderWithIntl(<SettingsPage />);

    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看已保存的密钥" })).not.toBeInTheDocument();
    expect(apiMocks.getEnvConfig).not.toHaveBeenCalled();
    expect(apiMocks.inspectApiKeys).not.toHaveBeenCalled();
  });

  it("never exposes provider-key controls inside the hybrid desktop app", () => {
    authMocks.value = { serverMode: true, user: { role: "admin" } };
    window.__ENMOTION_RUNTIME_CONFIG__ = { hybridMode: true };
    renderWithIntl(<SettingsPage />);

    expect(screen.queryByRole("tab", { name: "接口密钥" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看已保存的密钥" })).not.toBeInTheDocument();
    expect(apiMocks.getEnvConfig).not.toHaveBeenCalled();
    expect(apiMocks.inspectApiKeys).not.toHaveBeenCalled();
  });
});
