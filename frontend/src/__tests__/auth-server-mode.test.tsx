// @vitest-environment jsdom

import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";

const authMocks = vi.hoisted(() => ({
  session: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  changePassword: vi.fn(),
  listUsers: vi.fn(),
  createUser: vi.fn(),
  rehydrate: vi.fn(),
  resetProjectState: vi.fn(),
}));

vi.mock("@/lib/authApi", () => ({
  authApi: {
    session: authMocks.session,
    login: authMocks.login,
    logout: authMocks.logout,
    changePassword: authMocks.changePassword,
    listUsers: authMocks.listUsers,
    listAdminUsers: authMocks.listUsers,
    createUser: authMocks.createUser,
  },
  authErrorMessage: () => "Incorrect username or password.",
}));

vi.mock("@/store/projectStore", () => ({
  rehydrateProjectWorkspace: authMocks.rehydrate,
  resetProjectWorkspaceState: authMocks.resetProjectState,
}));

vi.mock("@/components/layout/EnMotionBranding", () => ({
  default: () => <div>EnMotion</div>,
}));

import AuthGate from "@/components/auth/AuthGate";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { AUTH_REQUIRED_EVENT, getCsrfToken, setCsrfToken } from "@/lib/httpClient";
import {
  getWorkspaceStorageScope,
  setWorkspaceStorageScope,
  writeWorkspaceItem,
} from "@/lib/workspaceStorage";
import {
  resetPlaygroundWorkspaceState,
  usePlaygroundStore,
} from "@/components/modules/playground/usePlaygroundStore";

const alice = {
  id: "user-alice",
  username: "alice",
  role: "user" as const,
  workspace_id: "workspace-alice",
};

const bob = {
  id: "user-bob",
  username: "bob",
  role: "user" as const,
  workspace_id: "workspace-bob",
};

const administrator = {
  id: "user-admin",
  username: "administrator",
  role: "admin" as const,
  workspace_id: "workspace-admin",
};

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

function authSession<T extends { username: string }>(user: T) {
  return { user, csrfToken: `csrf-${user.username}` };
}

function App() {
  return (
    <AuthProvider>
      <AuthGate>
        <div>Private workspace content</div>
      </AuthGate>
    </AuthProvider>
  );
}

const render = (ui: ReactElement) => renderWithIntl(ui, { locale: "en" });

describe("server-mode authentication", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_SERVER_MODE", "true");
    window.localStorage.clear();
    window.location.hash = "#/";
    setCsrfToken(null);
    setWorkspaceStorageScope(null);
    resetPlaygroundWorkspaceState();
    authMocks.session.mockReset();
    authMocks.login.mockReset();
    authMocks.logout.mockReset();
    authMocks.changePassword.mockReset();
    authMocks.listUsers.mockReset();
    authMocks.createUser.mockReset();
    authMocks.rehydrate.mockReset().mockResolvedValue(undefined);
    authMocks.resetProjectState.mockReset();
  });

  it("gates workspace content until a session is authenticated", async () => {
    authMocks.session.mockResolvedValue(null);
    render(<App />);

    expect(screen.queryByText("Private workspace content")).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Sign in to EnMotion" })).toBeInTheDocument();
  });

  it("logs in without persisting the submitted password", async () => {
    authMocks.session.mockResolvedValue(null);
    authMocks.login.mockResolvedValue(authSession(alice));
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery staple" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Private workspace content")).toBeInTheDocument();
    expect(authMocks.login).toHaveBeenCalledWith("alice", "correct horse battery staple");
    expect(getWorkspaceStorageScope()).toBe("workspace-alice");
    expect(Object.values(window.localStorage)).not.toContain("correct horse battery staple");
  });

  it("renders a stable Safari-safe loading state while login is pending", async () => {
    const pendingLogin = deferred<ReturnType<typeof authSession<typeof alice>>>();
    authMocks.session.mockResolvedValue(null);
    authMocks.login.mockReturnValue(pendingLogin.promise);
    render(<App />);

    fireEvent.change(await screen.findByLabelText("Username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery staple" } });
    const submitButton = screen.getByRole("button", { name: "Sign in" });
    fireEvent.click(submitButton);

    await waitFor(() => expect(submitButton).toHaveAttribute("aria-busy", "true"));
    expect(submitButton.querySelector(".animate-spin")).not.toBeInTheDocument();
    expect(submitButton.querySelector('[data-loading-indicator="static"]')).toBeInTheDocument();

    await act(async () => pendingLogin.resolve(authSession(alice)));
    expect(await screen.findByText("Private workspace content")).toBeInTheDocument();
  });

  it("logs out, clears the active workspace cache, and restores the gate", async () => {
    authMocks.session.mockResolvedValue(authSession(alice));
    authMocks.logout.mockResolvedValue(undefined);
    render(<App />);

    expect(await screen.findByText("Private workspace content")).toBeInTheDocument();
    writeWorkspaceItem("project-storage", JSON.stringify({ secretProject: true }));
    expect(Array.from({ length: window.localStorage.length }, (_, index) => window.localStorage.key(index)))
      .toContain("enmotion:workspace:workspace-alice:project-storage");

    fireEvent.click(screen.getByRole("button", { name: /alice/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "Sign in to EnMotion" })).toBeInTheDocument();
    expect(authMocks.logout).toHaveBeenCalledOnce();
    expect(getWorkspaceStorageScope()).toBeNull();
    expect(window.localStorage.getItem("enmotion:workspace:workspace-alice:project-storage")).toBeNull();
  });

  it("renders the change-password dialog on an opaque elevated surface", async () => {
    authMocks.session.mockResolvedValue(authSession(alice));
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /alice/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Change password" }));

    const dialog = screen.getByRole("dialog", { name: "Change password" });
    expect(dialog).toHaveClass("bg-elevated");
    expect(dialog).not.toHaveClass("glass-panel");
  });

  it("requires a fresh login after changing the password without deleting workspace data", async () => {
    authMocks.session.mockResolvedValue(authSession(alice));
    authMocks.changePassword.mockResolvedValue(undefined);
    render(<App />);

    expect(await screen.findByText("Private workspace content")).toBeInTheDocument();
    writeWorkspaceItem("project-storage", JSON.stringify({ preserved: true }));
    fireEvent.click(screen.getByRole("button", { name: /alice/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Change password" }));
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "old secret phrase" } });
    fireEvent.change(screen.getByLabelText(/New password/), { target: { value: "new secret phrase 123" } });
    fireEvent.click(screen.getByRole("button", { name: "Update password" }));

    expect(await screen.findByRole("heading", { name: "Sign in to EnMotion" })).toBeInTheDocument();
    expect(authMocks.changePassword).toHaveBeenCalledWith(
      "old secret phrase",
      "new secret phrase 123",
    );
    expect(window.localStorage.getItem("enmotion:workspace:workspace-alice:project-storage"))
      .toContain("\"preserved\":true");
  });

  it("automatically revokes the visible workspace after a protected request returns 401", async () => {
    authMocks.session.mockResolvedValue(authSession(alice));
    render(<App />);
    expect(await screen.findByText("Private workspace content")).toBeInTheDocument();

    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));

    expect(await screen.findByRole("heading", { name: "Sign in to EnMotion" })).toBeInTheDocument();
    await waitFor(() => expect(authMocks.resetProjectState).toHaveBeenCalled());
  });

  it("revalidates and revokes the workspace after another tab logs out", async () => {
    authMocks.session
      .mockResolvedValueOnce(authSession(alice))
      .mockResolvedValueOnce(null);
    render(<App />);
    expect(await screen.findByText("Private workspace content")).toBeInTheDocument();

    window.dispatchEvent(new StorageEvent("storage", {
      key: "enmotion:auth-event",
      newValue: "cross-tab-logout",
    }));

    expect(await screen.findByRole("heading", { name: "Sign in to EnMotion" })).toBeInTheDocument();
    expect(authMocks.session).toHaveBeenCalledTimes(2);
    expect(getWorkspaceStorageScope()).toBeNull();
  });

  it("preserves Playground results when the same workspace session revalidates", async () => {
    authMocks.session.mockResolvedValue(authSession(alice));
    render(<App />);
    expect(await screen.findByText("Private workspace content")).toBeInTheDocument();

    act(() => {
      usePlaygroundStore.setState({
        prompt: "A luminous mountain city",
        history: [{ id: "finished-generation", status: "completed", outputs: [] } as never],
      });
    });

    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(authMocks.session).toHaveBeenCalledTimes(2));

    expect(usePlaygroundStore.getState().prompt).toBe("A luminous mountain city");
    expect(usePlaygroundStore.getState().history).toHaveLength(1);
    expect(authMocks.rehydrate).toHaveBeenCalledTimes(1);
  });

  it("does not let an older session probe restore a user after logout", async () => {
    const staleProbe = deferred<ReturnType<typeof authSession<typeof alice>>>();
    authMocks.session
      .mockResolvedValueOnce(authSession(alice))
      .mockReturnValueOnce(staleProbe.promise);
    authMocks.logout.mockResolvedValue(undefined);
    render(<App />);
    expect(await screen.findByText("Private workspace content")).toBeInTheDocument();

    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(authMocks.session).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: /alice/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to EnMotion" })).toBeInTheDocument();

    await act(async () => staleProbe.resolve(authSession(alice)));
    expect(screen.getByRole("heading", { name: "Sign in to EnMotion" })).toBeInTheDocument();
    expect(getWorkspaceStorageScope()).toBeNull();
    expect(getCsrfToken()).toBeNull();
  });

  it("does not let an older session probe replace a newer explicit login", async () => {
    const staleProbe = deferred<ReturnType<typeof authSession<typeof bob>>>();
    authMocks.session
      .mockResolvedValueOnce(null)
      .mockReturnValueOnce(staleProbe.promise);
    authMocks.login.mockResolvedValue(authSession(alice));
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Sign in to EnMotion" })).toBeInTheDocument();

    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(authMocks.session).toHaveBeenCalledTimes(2));
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery staple" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Private workspace content")).toBeInTheDocument();

    await act(async () => staleProbe.resolve(authSession(bob)));
    expect(screen.getByRole("button", { name: /alice/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /bob/i })).not.toBeInTheDocument();
    expect(getWorkspaceStorageScope()).toBe("workspace-alice");
    expect(getCsrfToken()).toBe("csrf-alice");
  });

  it("single-flights overlapping cross-tab session probes", async () => {
    const sharedProbe = deferred<ReturnType<typeof authSession<typeof bob>>>();
    authMocks.session
      .mockResolvedValueOnce(authSession(alice))
      .mockReturnValueOnce(sharedProbe.promise);
    render(<App />);
    expect(await screen.findByRole("button", { name: /alice/i })).toBeInTheDocument();

    window.dispatchEvent(new StorageEvent("storage", { key: "enmotion:auth-event", newValue: "older" }));
    window.dispatchEvent(new StorageEvent("storage", { key: "enmotion:auth-event", newValue: "newest" }));
    await waitFor(() => expect(authMocks.session).toHaveBeenCalledTimes(2));

    await act(async () => sharedProbe.resolve(authSession(bob)));
    expect(await screen.findByRole("button", { name: /bob/i })).toBeInTheDocument();
    expect(getWorkspaceStorageScope()).toBe("workspace-bob");
  });

  it("preserves an authenticated workspace through a transient session failure", async () => {
    authMocks.session
      .mockResolvedValueOnce(authSession(alice))
      .mockRejectedValueOnce(new Error("temporary proxy failure"))
      .mockResolvedValueOnce(authSession(alice));
    render(<App />);
    expect(await screen.findByRole("button", { name: /alice/i })).toBeInTheDocument();

    window.dispatchEvent(new Event("focus"));
    expect(screen.getByText("Private workspace content")).toBeInTheDocument();
    await waitFor(() => expect(authMocks.session).toHaveBeenCalledTimes(3));
    expect(screen.getByText("Private workspace content")).toBeInTheDocument();
    expect(getWorkspaceStorageScope()).toBe("workspace-alice");
  });

  it("lets administrators list and create isolated user accounts", async () => {
    const submittedPassword = "new user secret phrase";
    authMocks.session.mockResolvedValue(authSession(administrator));
    authMocks.listUsers.mockResolvedValue([
      { ...administrator, is_active: true },
      { ...alice, is_active: true },
    ]);
    authMocks.createUser.mockResolvedValue({ ...bob, role: "admin", is_active: true });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /administrator/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Manage users" }));
    expect(await screen.findByRole("dialog", { name: "Manage user accounts" })).toBeInTheDocument();
    expect(await screen.findByText("alice")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText("Temporary password"), { target: { value: submittedPassword } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("Initial credits"), { target: { value: "250" } });
    fireEvent.click(screen.getByRole("button", { name: "Create an account" }));

    await waitFor(() => expect(authMocks.createUser).toHaveBeenCalledWith({
      username: "bob",
      password: submittedPassword,
      role: "admin",
      initial_credits: 250,
    }));
    expect(await screen.findByText("Created account “bob”.")).toBeInTheDocument();
    expect(screen.getByLabelText("Temporary password")).toHaveValue("");
    expect(screen.queryByDisplayValue(submittedPassword)).not.toBeInTheDocument();
  });

  it("does not expose account management to ordinary users", async () => {
    authMocks.session.mockResolvedValue(authSession(alice));
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /alice/i }));
    expect(screen.queryByRole("menuitem", { name: "Manage users" })).not.toBeInTheDocument();
    expect(authMocks.listUsers).not.toHaveBeenCalled();
  });

  it("preserves the existing no-login desktop behavior", async () => {
    vi.stubEnv("NEXT_PUBLIC_SERVER_MODE", "false");
    render(<App />);

    expect(screen.getByText("Private workspace content")).toBeInTheDocument();
    expect(authMocks.session).not.toHaveBeenCalled();
  });
});
