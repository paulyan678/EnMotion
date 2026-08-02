import { useEffect } from "react";
import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithIntl } from "@/test/renderWithIntl";
import AuthenticatedViewport from "../AuthenticatedViewport";
import { useTopBarNavigation } from "@/components/layout/TopBarNavigationContext";

const authState = {
  serverMode: true,
  status: "authenticated",
  user: { username: "admin", role: "admin" },
  logout: vi.fn(),
  changePassword: vi.fn(),
};

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => authState,
}));

vi.mock("@/components/auth/UserManagementDialog", () => ({
  default: () => <div>用户管理</div>,
}));

const navigation = {
  segments: [
    { label: "EnMotion", hash: "#/" },
    { label: "穿越成后宫小厨娘", hash: "#/series/series-1" },
    { label: "第 1 集" },
  ],
  currentContent: <span className="truncate">第 1 集</span>,
  description: "这一集的系列说明很长，但不会覆盖右侧账户控件",
  actions: <button type="button" aria-label="面板设置">设置</button>,
};

function EditorWithNavigation() {
  const { registerNavigation } = useTopBarNavigation();

  useEffect(() => registerNavigation(navigation), [registerNavigation]);

  return <main>集数编辑器</main>;
}

describe("AuthenticatedViewport", () => {
  beforeEach(() => {
    authState.serverMode = true;
    authState.status = "authenticated";
    window.location.hash = "#/series/series-1/episode/episode-1";
    vi.clearAllMocks();
  });

  it("keeps editor navigation without account controls and lets it collapse upward", async () => {
    renderWithIntl(
      <AuthenticatedViewport>
        <EditorWithNavigation />
      </AuthenticatedViewport>,
    );

    const topBar = await screen.findByRole("banner");
    expect(within(topBar).getByText("EnMotion")).toBeInTheDocument();
    expect(within(topBar).getByText("穿越成后宫小厨娘")).toBeInTheDocument();
    expect(topBar.querySelector('[aria-current="page"]')).toHaveTextContent("第 1 集");
    expect(within(topBar).getByTestId("top-bar-description")).toHaveTextContent(
      "这一集的系列说明很长，但不会覆盖右侧账户控件",
    );
    expect(within(topBar).getByTestId("top-bar-description")).toHaveClass(
      "hidden",
      "truncate",
      "lg:block",
    );
    expect(within(topBar).getByRole("button", { name: "面板设置" })).toBeInTheDocument();
    expect(within(topBar).queryByText("admin")).not.toBeInTheDocument();

    fireEvent.click(within(topBar).getByText("穿越成后宫小厨娘"));
    expect(window.location.hash).toBe("#/series/series-1");

    fireEvent.click(within(topBar).getByRole("button", { name: "收起工作区导航" }));
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开工作区导航" }));
    expect(await screen.findByRole("banner")).toBeInTheDocument();
  });

  it("leaves desktop mode unchanged when no account bar exists", () => {
    authState.serverMode = false;

    renderWithIntl(
      <AuthenticatedViewport>
        <p>桌面内容</p>
      </AuthenticatedViewport>,
    );

    expect(screen.getByText("桌面内容")).toBeInTheDocument();
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  });

  it("provides the same collapsible editor navigation in desktop mode", async () => {
    authState.serverMode = false;
    authState.status = "disabled";

    renderWithIntl(
      <AuthenticatedViewport>
        <EditorWithNavigation />
      </AuthenticatedViewport>,
    );

    const topBar = await screen.findByRole("banner");
    expect(within(topBar).getByText("穿越成后宫小厨娘")).toBeInTheDocument();
    fireEvent.click(within(topBar).getByRole("button", { name: "收起工作区导航" }));
    expect(screen.getByRole("button", { name: "展开工作区导航" })).toBeInTheDocument();
  });
});
