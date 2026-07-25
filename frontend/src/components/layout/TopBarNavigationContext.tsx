"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { BreadcrumbSegment } from "./BreadcrumbBar";

export interface TopBarNavigation {
  segments: BreadcrumbSegment[];
  currentContent?: ReactNode;
  description?: string;
  actions?: ReactNode;
}

interface TopBarNavigationContextValue {
  navigation: TopBarNavigation | null;
  registerNavigation: (navigation: TopBarNavigation) => () => void;
}

const TopBarNavigationContext = createContext<TopBarNavigationContextValue>({
  navigation: null,
  registerNavigation: () => () => undefined,
});

export function TopBarNavigationProvider({ children }: { children: ReactNode }) {
  const [navigation, setNavigation] = useState<TopBarNavigation | null>(null);
  const activeOwner = useRef<symbol | null>(null);

  const registerNavigation = useCallback((nextNavigation: TopBarNavigation) => {
    const owner = Symbol("top-bar-navigation");
    activeOwner.current = owner;
    setNavigation(nextNavigation);

    return () => {
      if (activeOwner.current !== owner) return;
      activeOwner.current = null;
      setNavigation(null);
    };
  }, []);

  const value = useMemo(
    () => ({ navigation, registerNavigation }),
    [navigation, registerNavigation],
  );

  return (
    <TopBarNavigationContext.Provider value={value}>
      {children}
    </TopBarNavigationContext.Provider>
  );
}

export function useTopBarNavigation() {
  return useContext(TopBarNavigationContext);
}
