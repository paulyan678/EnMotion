import type { StateStorage } from "zustand/middleware";
import { isServerModeEnabled } from "@/lib/serverMode";

const WORKSPACE_PREFIX = "enmotion:workspace:";
let activeScope: string | null = null;

function encodedScope(scope: string): string {
  return encodeURIComponent(scope);
}

export function setWorkspaceStorageScope(scope: string | null): void {
  activeScope = scope?.trim() || null;
}

export function getWorkspaceStorageScope(): string | null {
  return activeScope;
}

export function workspaceStorageKey(key: string, scope = activeScope): string | null {
  if (!isServerModeEnabled()) return key;
  if (!scope) return null;
  return `${WORKSPACE_PREFIX}${encodedScope(scope)}:${key}`;
}

/**
 * Storage adapter for anything that can contain workspace data. Before the
 * authenticated workspace is known, reads return empty and writes are ignored;
 * this prevents hydration from briefly exposing the last desktop user's data.
 */
export const workspaceStateStorage: StateStorage = {
  getItem(name) {
    if (typeof window === "undefined") return null;
    const key = workspaceStorageKey(name);
    return key ? window.localStorage.getItem(key) : null;
  },
  setItem(name, value) {
    if (typeof window === "undefined") return;
    const key = workspaceStorageKey(name);
    if (key) window.localStorage.setItem(key, value);
  },
  removeItem(name) {
    if (typeof window === "undefined") return;
    const key = workspaceStorageKey(name);
    if (key) window.localStorage.removeItem(key);
  },
};

export function readWorkspaceItem(key: string): string | null {
  return workspaceStateStorage.getItem(key) as string | null;
}

export function writeWorkspaceItem(key: string, value: string): void {
  workspaceStateStorage.setItem(key, value);
}

export function removeWorkspaceItem(key: string): void {
  workspaceStateStorage.removeItem?.(key);
}

/** Clear only one authenticated workspace's browser cache. */
export function clearWorkspaceStorage(scope = activeScope): void {
  if (typeof window === "undefined" || !scope || !isServerModeEnabled()) return;
  const prefix = `${WORKSPACE_PREFIX}${encodedScope(scope)}:`;
  const matches: string[] = [];
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index);
    if (key?.startsWith(prefix)) matches.push(key);
  }
  for (const key of matches) window.localStorage.removeItem(key);
}
