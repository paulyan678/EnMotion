"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { authApi, type AuthSessionState, type AuthUser } from "@/lib/authApi";
import {
  AUTH_REQUIRED_EVENT,
  setCsrfToken,
} from "@/lib/httpClient";
import { isServerModeEnabled } from "@/lib/serverMode";
import {
  clearWorkspaceStorage,
  getWorkspaceStorageScope,
  setWorkspaceStorageScope,
} from "@/lib/workspaceStorage";
import {
  rehydrateProjectWorkspace,
  resetProjectWorkspaceState,
} from "@/store/projectStore";
import { resetPlaygroundWorkspaceState } from "@/components/modules/playground/usePlaygroundStore";
import { useToastStore } from "@/store/toastStore";
import { clearAssetLibraryQueryCache } from "@/lib/assetLibraryQuery";
import { confirmDesktopUiReady } from "@/lib/updater";

type AuthStatus = "disabled" | "checking" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  serverMode: boolean;
  status: AuthStatus;
  user: AuthUser | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);
const AUTH_STORAGE_EVENT_KEY = "enmotion:auth-event";

function workspaceScopeFor(user: AuthUser): string {
  return user.workspace_id || user.id;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const serverMode = isServerModeEnabled();
  const [status, setStatus] = useState<AuthStatus>(serverMode ? "checking" : "disabled");
  const [user, setUser] = useState<AuthUser | null>(null);
  const transitionRef = useRef(0);
  const authenticatedRef = useRef(false);
  const sessionProbeRef = useRef<Promise<void> | null>(null);

  const confirmUiReady = useCallback(() => {
    void confirmDesktopUiReady().catch(() => {
      console.warn("Desktop update health confirmation is temporarily unavailable");
    });
  }, []);

  const beginTransition = useCallback((): number => {
    transitionRef.current += 1;
    return transitionRef.current;
  }, []);

  const activateUser = useCallback(async (session: AuthSessionState, transition: number): Promise<boolean> => {
    if (transition !== transitionRef.current) return false;
    const { user: nextUser, csrfToken } = session;
    setCsrfToken(csrfToken);
    const nextScope = workspaceScopeFor(nextUser);
    const previousScope = getWorkspaceStorageScope();
    const workspaceChanged = previousScope !== nextScope;
    if (workspaceChanged) {
      if (previousScope) clearAssetLibraryQueryCache(previousScope);
      resetProjectWorkspaceState();
      setWorkspaceStorageScope(nextScope);
      resetPlaygroundWorkspaceState();
      await rehydrateProjectWorkspace();
    }
    if (transition !== transitionRef.current) return false;
    authenticatedRef.current = true;
    setUser(nextUser);
    setStatus("authenticated");
    return true;
  }, []);

  const leaveWorkspace = useCallback((clearCache: boolean, transition?: number): boolean => {
    const activeTransition = transition ?? beginTransition();
    if (activeTransition !== transitionRef.current) return false;
    const previousScope = getWorkspaceStorageScope();
    if (clearCache) clearWorkspaceStorage(previousScope);
    if (previousScope) clearAssetLibraryQueryCache(previousScope);
    setWorkspaceStorageScope(null);
    resetProjectWorkspaceState();
    resetPlaygroundWorkspaceState();
    useToastStore.getState().clear();
    setCsrfToken(null);
    authenticatedRef.current = false;
    setUser(null);
    setStatus(serverMode ? "unauthenticated" : "disabled");
    if (typeof window !== "undefined" && window.location.hash !== "#/") {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#/`);
    }
    return true;
  }, [beginTransition, serverMode]);

  const revalidateSession = useCallback((): Promise<void> => {
    if (sessionProbeRef.current) return sessionProbeRef.current;
    const probe = (async () => {
      const transition = beginTransition();
      let lastError: unknown;
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          const sessionUser = await authApi.session();
          if (transition !== transitionRef.current) return;
          if (!sessionUser) {
            // Only a confirmed 401/null session revokes the local workspace.
            if (leaveWorkspace(true, transition)) confirmUiReady();
            return;
          }
          if (await activateUser(sessionUser, transition)) confirmUiReady();
          return;
        } catch (error) {
          lastError = error;
          if (attempt < 2) {
            await new Promise((resolve) => window.setTimeout(resolve, 250 * 2 ** attempt));
          }
        }
      }
      // A timeout, offline browser, proxy error, or transient 5xx must never
      // sign out an already authenticated user or clear their workspace.
      if (transition === transitionRef.current && !authenticatedRef.current) {
        setStatus(serverMode ? "unauthenticated" : "disabled");
      }
      if (lastError) {
        console.warn("Session revalidation is temporarily unavailable");
      }
    })();
    sessionProbeRef.current = probe;
    void probe.finally(() => {
      if (sessionProbeRef.current === probe) sessionProbeRef.current = null;
    });
    return probe;
  }, [activateUser, beginTransition, confirmUiReady, leaveWorkspace, serverMode]);

  const announceAuthChange = useCallback(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(AUTH_STORAGE_EVENT_KEY, `${Date.now()}:${Math.random()}`);
    } catch {
      // Storage can be unavailable in hardened/private browser modes.
    }
  }, []);

  useEffect(() => {
    if (!serverMode) return;
    void revalidateSession();
  }, [revalidateSession, serverMode]);

  useEffect(() => {
    if (!serverMode) return;
    const onAuthRequired = () => leaveWorkspace(true);
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
  }, [leaveWorkspace, serverMode]);

  useEffect(() => {
    if (!serverMode) return;
    const onStorage = (event: StorageEvent) => {
      if (event.key === AUTH_STORAGE_EVENT_KEY) void revalidateSession();
    };
    const onFocus = () => void revalidateSession();
    const onVisibility = () => {
      if (document.visibilityState === "visible") void revalidateSession();
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [revalidateSession, serverMode]);

  const login = useCallback(async (username: string, password: string) => {
    const transition = beginTransition();
    try {
      const nextUser = await authApi.login(username, password);
      if (await activateUser(nextUser, transition)) {
        confirmUiReady();
        announceAuthChange();
      }
    } catch (error) {
      // A newer auth transition owns the UI; do not surface a stale failure.
      if (transition === transitionRef.current) throw error;
    }
  }, [activateUser, announceAuthChange, beginTransition, confirmUiReady]);

  const logout = useCallback(async () => {
    const transition = beginTransition();
    try {
      await authApi.logout();
    } finally {
      // Local state must be revoked even if the network disappears mid-logout.
      if (leaveWorkspace(true, transition)) announceAuthChange();
    }
  }, [announceAuthChange, beginTransition, leaveWorkspace]);

  const changePassword = useCallback(async (
    currentPassword: string,
    newPassword: string,
  ) => {
    const transition = beginTransition();
    await authApi.changePassword(currentPassword, newPassword);
    // The control plane revokes every session after a password change. Keep
    // the account's local files, but require a fresh login with the new secret.
    if (leaveWorkspace(false, transition)) announceAuthChange();
  }, [announceAuthChange, beginTransition, leaveWorkspace]);

  const value = useMemo<AuthContextValue>(() => ({
    serverMode,
    status,
    user,
    login,
    logout,
    changePassword,
  }), [changePassword, login, logout, serverMode, status, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
