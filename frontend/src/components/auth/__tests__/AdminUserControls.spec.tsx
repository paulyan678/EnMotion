import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "@/test/renderWithIntl";

const authMocks = vi.hoisted(() => ({
  adjustCredits: vi.fn(),
  setUserActive: vi.fn(),
  resetUserPassword: vi.fn(),
  revokeUserSessions: vi.fn(),
}));

vi.mock("@/lib/authApi", () => ({
  authApi: authMocks,
}));

import AdminUserControls from "@/components/auth/AdminUserControls";

const managedUser = {
  id: "user-managed",
  username: "managed",
  role: "user" as const,
  workspace_id: "workspace-managed",
  active: true,
  available_credits: 25,
  reserved_credits: 0,
};

describe("AdminUserControls", () => {
  beforeEach(() => {
    authMocks.adjustCredits.mockReset();
    authMocks.setUserActive.mockReset();
    authMocks.resetUserPassword.mockReset().mockResolvedValue(undefined);
    authMocks.revokeUserSessions.mockReset();
  });

  it("shows the reset password and submits at exactly six characters", async () => {
    renderWithIntl(
      <AdminUserControls user={managedUser} onUpdated={vi.fn()} />,
      { locale: "en" },
    );

    const input = screen.getByLabelText(/New temporary password/);
    const button = screen.getByRole("button", { name: "Reset password" });
    expect(input).toHaveAttribute("type", "text");
    expect(input).toHaveAttribute("minlength", "6");
    expect(button).toBeDisabled();

    fireEvent.change(input, { target: { value: "Ab123" } });
    expect(input).toHaveValue("Ab123");
    expect(button).toBeDisabled();

    fireEvent.change(input, { target: { value: "Abc123" } });
    expect(input).toHaveValue("Abc123");
    expect(button).toBeEnabled();
    fireEvent.click(button);

    await waitFor(() => {
      expect(authMocks.resetUserPassword).toHaveBeenCalledWith(
        managedUser.id,
        "Abc123",
      );
    });
    expect(input).toHaveValue("");
    expect(
      screen.getByText("Temporary password updated."),
    ).toBeInTheDocument();
  });
});
