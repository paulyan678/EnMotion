"use client";

import { LoaderCircle } from "lucide-react";
import { createPortal } from "react-dom";
import { useModalFocusTrap } from "@/components/common/useModalFocusTrap";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel: string;
  busy?: boolean;
  destructive?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel,
  busy = false,
  destructive = false,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const requestClose = () => {
    if (!busy) onClose();
  };
  const dialogRef = useModalFocusTrap<HTMLDivElement>(requestClose, open);

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[120] grid place-items-center bg-overlay p-4 backdrop-blur-sm"
      onClick={(event) => {
        event.stopPropagation();
        if (!busy && event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="shared-confirm-title"
        aria-describedby="shared-confirm-description"
        tabIndex={-1}
        className="w-full max-w-md rounded-2xl border border-glass-border bg-elevated p-5 shadow-2xl outline-none sm:p-6"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="shared-confirm-title" className="font-display text-xl font-semibold text-foreground">{title}</h2>
        <p id="shared-confirm-description" className="mt-2 text-sm leading-relaxed text-text-secondary">{description}</p>
        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="inline-flex min-h-10 items-center justify-center rounded-lg border border-glass-border px-4 text-sm font-semibold text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring disabled:opacity-50 ${destructive ? "bg-status-failed-fg text-on-accent hover:opacity-90" : "bg-primary text-primary-foreground hover:bg-primary-hover"}`}
          >
            {busy && <LoaderCircle size={15} className="animate-spin" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
