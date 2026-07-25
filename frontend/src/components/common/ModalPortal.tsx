"use client";

import { useSyncExternalStore, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useModalFocusTrap } from "@/components/common/useModalFocusTrap";

interface ModalPortalProps {
  isOpen: boolean;
  onClose: () => void;
  children: (dialogRef: RefObject<HTMLDivElement | null>) => ReactNode;
}

/**
 * Keeps modal content out of clipped application containers while centralizing
 * focus containment, Escape handling, body scroll locking, and focus restore.
 */
export default function ModalPortal({ isOpen, onClose, children }: ModalPortalProps) {
  const portalRoot = useSyncExternalStore(
    () => () => undefined,
    () => document.body,
    () => null,
  );
  const dialogRef = useModalFocusTrap<HTMLDivElement>(onClose, isOpen && portalRoot !== null);

  if (!portalRoot) return null;
  return createPortal(children(dialogRef), portalRoot);
}
