import axiosLibrary from "axios";
import { API_URL } from "@/lib/apiUrl";
import { apiClient } from "@/lib/httpClient";

export type UserRole = "admin" | "user";

export interface AuthUser {
  id: string;
  username: string;
  role: UserRole;
  workspace_id: string;
}

export interface AuthSessionState {
  user: AuthUser;
  csrfToken: string | null;
}

export interface ManagedUser extends AuthUser {
  is_active?: boolean;
  active?: boolean;
  available_credits?: number;
  reserved_credits?: number;
}

export interface CreateUserRequest {
  username: string;
  password: string;
  role: UserRole;
  initial_credits: number;
}

export interface AccountBalance {
  available_credits: number;
  reserved_credits: number;
  total_credits: number;
}

export interface AccountUsageItem {
  id: string;
  operation: string;
  model: string;
  status: string;
  reserved_units: number;
  settled_units: number;
  created_at: string;
  settled_at?: string | null;
  error_code?: string | null;
}

export interface AccountUsagePage {
  items: AccountUsageItem[];
  next_cursor?: string | null;
}

interface AdminUserResponse {
  user?: ManagedUser;
  balance?: AccountBalance;
}

const SESSION_PROBE_TIMEOUT_MS = 3_000;

interface CreditLedgerResponse {
  available_after: number;
  reserved_after: number;
}

function normalizeManagedUser(user: ManagedUser): ManagedUser {
  return {
    ...user,
    workspace_id: user.workspace_id || user.id,
  };
}

function normalizeManagedUsers(data: ManagedUser[] | { items?: ManagedUser[] }): ManagedUser[] {
  const users = Array.isArray(data) ? data : data.items ?? [];
  return users.map(normalizeManagedUser);
}

function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `ui-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

interface AuthPayload extends AuthUser {
  csrf_token?: string;
  user?: AuthUser;
}

function normalizeAuthPayload(payload: AuthPayload): AuthUser {
  const candidate = payload.user || payload;
  return {
    id: candidate.id,
    username: candidate.username,
    role: candidate.role,
    workspace_id: candidate.workspace_id,
  };
}

async function readSession(request: Promise<{ data: AuthPayload }>): Promise<AuthSessionState> {
  const { data } = await request;
  return {
    user: normalizeAuthPayload(data),
    csrfToken: data.csrf_token?.trim() || null,
  };
}

export const authApi = {
  session: async (): Promise<AuthSessionState | null> => {
    try {
      return await readSession(apiClient.get(`${API_URL}/auth/session`, {
        timeout: SESSION_PROBE_TIMEOUT_MS,
      }));
    } catch (error) {
      if (axiosLibrary.isAxiosError(error) && error.response?.status === 401) return null;
      throw error;
    }
  },

  login: (username: string, password: string): Promise<AuthSessionState> =>
    readSession(apiClient.post(`${API_URL}/auth/login`, { username, password })),

  logout: async (): Promise<void> => {
    await apiClient.post(`${API_URL}/auth/logout`);
  },

  changePassword: async (currentPassword: string, newPassword: string): Promise<void> => {
    await apiClient.post(`${API_URL}/auth/change-password`, {
      current_password: currentPassword,
      new_password: newPassword,
    });
  },

  listUsers: async (): Promise<ManagedUser[]> => {
    const { data } = await apiClient.get<ManagedUser[]>(`${API_URL}/auth/users`);
    return normalizeManagedUsers(data);
  },

  createUser: async (payload: CreateUserRequest): Promise<ManagedUser> => {
    const { data } = await apiClient.post<ManagedUser>(`${API_URL}/auth/users`, payload);
    return normalizeManagedUser(data);
  },

  accountBalance: async (): Promise<AccountBalance> => {
    const { data } = await apiClient.get<AccountBalance>(`${API_URL}/account/balance`);
    return data;
  },

  accountUsage: async (limit = 30, cursor?: string): Promise<AccountUsagePage> => {
    const { data } = await apiClient.get<AccountUsagePage>(`${API_URL}/account/usage`, {
      params: { limit, ...(cursor ? { cursor } : {}) },
    });
    return data;
  },

  listAdminUsers: async (): Promise<ManagedUser[]> => {
    try {
      const { data } = await apiClient.get<ManagedUser[] | { items?: ManagedUser[] }>(`${API_URL}/admin/users`);
      return normalizeManagedUsers(data);
    } catch (error) {
      if (axiosLibrary.isAxiosError(error) && error.response?.status === 404) {
        return authApi.listUsers();
      }
      throw error;
    }
  },

  adjustCredits: async (
    userId: string,
    delta: number,
    reason: string,
  ): Promise<AdminUserResponse> => {
    const { data } = await apiClient.post<AdminUserResponse | CreditLedgerResponse>(`${API_URL}/admin/users/${encodeURIComponent(userId)}/credits`, {
      delta,
      reason,
      idempotency_key: createIdempotencyKey(),
    });
    if ("available_after" in data) {
      return {
        balance: {
          available_credits: data.available_after,
          reserved_credits: data.reserved_after,
          total_credits: data.available_after + data.reserved_after,
        },
      };
    }
    return data;
  },

  setUserActive: async (userId: string, active: boolean): Promise<ManagedUser> => {
    const { data } = await apiClient.patch<ManagedUser | AdminUserResponse>(
      `${API_URL}/admin/users/${encodeURIComponent(userId)}/status`,
      { active },
    );
    return normalizeManagedUser("user" in data && data.user ? data.user : data as ManagedUser);
  },

  resetUserPassword: async (userId: string, newPassword: string): Promise<void> => {
    await apiClient.post(`${API_URL}/admin/users/${encodeURIComponent(userId)}/password`, {
      new_password: newPassword,
    });
  },

  revokeUserSessions: async (userId: string): Promise<void> => {
    await apiClient.post(`${API_URL}/admin/users/${encodeURIComponent(userId)}/sessions/revoke`, {});
  },
};

export function authErrorMessage(error: unknown, locale: "zh" | "en" = "zh"): string {
  const messages = locale === "zh"
    ? {
        incorrectCredentials: "用户名或密码不正确。",
        serverUnreachable: "无法连接到 EnMotion 服务器。",
        signInFailed: "登录失败，请重试。",
      }
    : {
        incorrectCredentials: "Incorrect username or password.",
        serverUnreachable: "Unable to reach the EnMotion server.",
        signInFailed: "Sign-in failed. Please try again.",
      };
  if (axiosLibrary.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (locale === "en" && typeof detail === "string" && detail.trim()) return detail;
    if (error.response?.status === 401) return messages.incorrectCredentials;
    if (!error.response) return messages.serverUnreachable;
  }
  return messages.signInFailed;
}
