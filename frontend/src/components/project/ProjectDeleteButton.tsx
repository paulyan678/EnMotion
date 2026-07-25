"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import type { Project } from "@/store/projectStore";
import ConfirmDialog from "@/components/shared/ConfirmDialog";

interface ProjectDeleteButtonProps {
  project: Project;
  onDelete: (id: string) => void | Promise<void>;
}

/** Direct, server-backed delete action shared by gallery cards and list rows. */
export default function ProjectDeleteButton({
  project,
  onDelete,
}: ProjectDeleteButtonProps) {
  const t = useTranslations("project");
  const tc = useTranslations("common");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    setConfirmOpen(true);
  };

  const confirmDelete = async () => {
    if (deleting) return;
    setDeleting(true);
    try {
      await onDelete(project.id);
      setConfirmOpen(false);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={handleDelete}
        aria-label={t("deleteAria", { title: project.title })}
        title={tc("delete")}
        className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-lg text-status-failed-fg/70 transition-colors hover:bg-status-failed-bg hover:text-status-failed-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-fg/50 focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
      >
        <Trash2 size={15} aria-hidden="true" />
      </button>
      <ConfirmDialog
        open={confirmOpen}
        title={t("deleteDialogTitle", { title: project.title })}
        description={t("confirmDelete", { title: project.title })}
        confirmLabel={tc("delete")}
        cancelLabel={tc("cancel")}
        busy={deleting}
        destructive
        onClose={() => setConfirmOpen(false)}
        onConfirm={() => void confirmDelete()}
      />
    </>
  );
}
