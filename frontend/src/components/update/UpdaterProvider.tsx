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
import { useTranslations } from "next-intl";
import {
  resolveUpdaterBridge,
  type EnMotionUpdaterBridge,
  type UpdaterState,
} from "@/lib/updater";

const INITIAL_STATE: UpdaterState = {
  status: "idle",
  currentVersion: "",
};

interface UpdaterContextValue {
  supported: boolean;
  state: UpdaterState;
  checkForUpdates: () => Promise<void>;
  startUpdate: () => Promise<void>;
  installAndRestart: () => Promise<void>;
}

const DISABLED_CONTEXT: UpdaterContextValue = {
  supported: false,
  state: INITIAL_STATE,
  checkForUpdates: async () => undefined,
  startUpdate: async () => undefined,
  installAndRestart: async () => undefined,
};

const UpdaterContext = createContext<UpdaterContextValue>(DISABLED_CONTEXT);

export function UpdaterProvider({ children }: { children: React.ReactNode }) {
  const t = useTranslations("ui.update");
  const bridgeRef = useRef<EnMotionUpdaterBridge | null>(null);
  const [supported, setSupported] = useState(false);
  const [state, setState] = useState<UpdaterState>(INITIAL_STATE);

  useEffect(() => {
    let active = true;
    let unlisten: (() => void) | undefined;

    void Promise.resolve().then(async () => {
      const bridge = resolveUpdaterBridge();
      if (!active || !bridge) return;
      bridgeRef.current = bridge;
      setSupported(true);

      try {
        const [initialState, stopListening] = await Promise.all([
          bridge.getState(),
          bridge.subscribe((nextState) => {
            if (active) setState(nextState);
          }),
        ]);
        if (!active) {
          stopListening();
          return;
        }
        unlisten = stopListening;
        setState(initialState);
      } catch {
        if (active) {
          setState((current) => ({
            ...current,
            status: "error",
            error: t("failed"),
          }));
        }
      }
    });

    return () => {
      active = false;
      bridgeRef.current = null;
      unlisten?.();
    };
  }, [t]);

  const runCommand = useCallback(async (
    optimisticStatus: UpdaterState["status"],
    command: (bridge: EnMotionUpdaterBridge) => Promise<UpdaterState>,
  ) => {
    const bridge = bridgeRef.current;
    if (!bridge) return;
    setState((current) => ({ ...current, status: optimisticStatus, error: undefined }));
    try {
      setState(await command(bridge));
    } catch {
      setState((current) => ({
        ...current,
        status: "error",
        error: t("failed"),
      }));
    }
  }, [t]);

  const value = useMemo<UpdaterContextValue>(() => ({
    supported,
    state,
    checkForUpdates: () => runCommand("checking", (bridge) => bridge.checkForUpdates()),
    startUpdate: () => runCommand("downloading", (bridge) => bridge.startUpdate()),
    installAndRestart: () => runCommand("installing", (bridge) => bridge.installAndRestart()),
  }), [runCommand, state, supported]);

  return <UpdaterContext.Provider value={value}>{children}</UpdaterContext.Provider>;
}

export function useUpdater(): UpdaterContextValue {
  return useContext(UpdaterContext);
}
