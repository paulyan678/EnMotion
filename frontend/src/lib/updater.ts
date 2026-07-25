import type { UnlistenFn } from "@tauri-apps/api/event";
import { api } from "@/lib/api";

export type UpdaterStatus =
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  | "ready"
  | "installing"
  | "error";

export interface UpdaterProgress {
  downloadedBytes: number;
  totalBytes?: number;
}

export interface UpdaterState {
  status: UpdaterStatus;
  currentVersion: string;
  availableVersion?: string;
  progress?: UpdaterProgress;
  releaseNotes?: string;
  error?: string;
}

export interface EnMotionUpdaterBridge {
  getState: () => Promise<UpdaterState>;
  checkForUpdates: () => Promise<UpdaterState>;
  startUpdate: () => Promise<UpdaterState>;
  installAndRestart: () => Promise<UpdaterState>;
  confirmUiReady: () => Promise<void>;
  subscribe: (listener: (state: UpdaterState) => void) => Promise<UnlistenFn>;
}

declare global {
  interface Window {
    /**
     * Stable frontend contract for the desktop updater. Tauri is adapted to
     * this interface below; tests or another trusted shell may inject the same
     * contract without exposing arbitrary native capabilities.
     */
    enmotionUpdater?: EnMotionUpdaterBridge;
  }
}

const UPDATE_STATE_EVENT = "enmotion://update-state";

function createTauriUpdaterBridge(): EnMotionUpdaterBridge {
  return {
    getState: async () => {
      const { invoke } = await import("@tauri-apps/api/core");
      return invoke<UpdaterState>("desktop_update_state");
    },
    checkForUpdates: async () => {
      const { invoke } = await import("@tauri-apps/api/core");
      return invoke<UpdaterState>("desktop_check_for_updates");
    },
    startUpdate: async () => {
      const { invoke } = await import("@tauri-apps/api/core");
      return invoke<UpdaterState>("desktop_start_update");
    },
    installAndRestart: async () => {
      const { invoke } = await import("@tauri-apps/api/core");
      return invoke<UpdaterState>("desktop_install_and_restart");
    },
    confirmUiReady: async () => {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke<void>("desktop_confirm_ui_ready");
    },
    subscribe: async (listener) => {
      const { listen } = await import("@tauri-apps/api/event");
      return listen<UpdaterState>(UPDATE_STATE_EVENT, (event) => listener(event.payload));
    },
  };
}

export function resolveUpdaterBridge(): EnMotionUpdaterBridge | null {
  if (typeof window === "undefined") return null;
  if (window.__ENMOTION_RUNTIME_CONFIG__?.updater?.enabled === false) return null;
  if (window.enmotionUpdater) return window.enmotionUpdater;
  if (!("__TAURI_INTERNALS__" in window)) return null;

  const bridge = createTauriUpdaterBridge();
  window.enmotionUpdater = bridge;
  return bridge;
}

let uiReadyConfirmation: Promise<void> | null = null;
let uiReadyConfirmedBridge: EnMotionUpdaterBridge | null = null;

/**
 * Commit a pending desktop update only after the authenticated UI has settled
 * and the local sidecar has answered a real application health probe.
 *
 * The native command independently validates the HttpOnly employee session
 * before it clears the rollback marker. Failures remain retryable on the next
 * session revalidation or login.
 */
export function confirmDesktopUiReady(): Promise<void> {
  const bridge = resolveUpdaterBridge();
  if (!bridge || uiReadyConfirmedBridge === bridge) return Promise.resolve();
  if (uiReadyConfirmation) return uiReadyConfirmation;

  const confirmation = (async () => {
    const health = await api.healthCheck();
    if (!health.ok) {
      throw new Error("EnMotion 本地服务未通过健康检查");
    }
    await bridge.confirmUiReady();
    uiReadyConfirmedBridge = bridge;
  })();

  uiReadyConfirmation = confirmation;
  void confirmation.finally(() => {
    if (uiReadyConfirmation === confirmation) uiReadyConfirmation = null;
  }).catch(() => {
    // The caller owns user-visible/error logging. This catch only prevents the
    // cleanup branch from creating an unhandled rejected promise.
  });
  return confirmation;
}

export function updateProgressPercent(progress: UpdaterProgress | undefined): number | null {
  if (
    !progress
    || !Number.isFinite(progress.downloadedBytes)
    || !Number.isFinite(progress.totalBytes)
    || !progress.totalBytes
    || progress.totalBytes <= 0
  ) {
    return null;
  }
  return Math.min(100, Math.max(0, Math.round((progress.downloadedBytes / progress.totalBytes) * 100)));
}
