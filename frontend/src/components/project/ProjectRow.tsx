"use client";

import {
  Clock,
  FileText,
  Film,
} from "lucide-react";
import { useTranslations } from "next-intl";

import type { Project } from "@/store/projectStore";
import { deriveCover, deriveStatus } from "./ProjectCard";
import ProjectDeleteButton from "./ProjectDeleteButton";

interface ProjectRowProps {
  project: Project;
  crumb: string;
  onDelete: (id: string) => void | Promise<void>;
}

/** Compact list-view project card with the same actions as the gallery card. */
export default function ProjectRow({ project, crumb, onDelete }: ProjectRowProps) {
  const t = useTranslations("project");

  const cover = deriveCover(project);
  const status = deriveStatus(project);
  const frameCount = project.frames?.length || 0;
  const sceneCount = project.scenes?.length || 0;

  const open = () => {
    window.location.hash = project.series_id
      ? `#/series/${project.series_id}/episode/${project.id}`
      : `#/project/${project.id}`;
  };

  const badge = {
    completed: {
      label: t("statusCompleted"),
      cls: "text-status-completed-fg bg-status-completed-bg border-status-completed-border",
    },
    processing: {
      label: t("statusProcessing"),
      cls: "text-status-processing-fg bg-status-processing-bg border-status-processing-border",
    },
    pending: {
      label: t("statusDraft"),
      cls: "text-status-pending-fg bg-status-pending-bg border-status-pending-border",
    },
  }[status];

  return (
    <div
      onClick={open}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        // Nested controls own their keyboard events and must not open the row.
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          if (event.key === " ") event.preventDefault();
          open();
        }
      }}
      className="group glass-panel flex items-center gap-4 rounded-xl border border-glass-border px-3 py-2.5 cursor-pointer hover:bg-hover-bg transition-colors"
    >
      <div className="relative w-[68px] aspect-[16/10] flex-shrink-0 rounded-lg overflow-hidden bg-surface-inset">
        {cover ? (
          <img src={cover} alt="" className="w-full h-full object-cover" />
        ) : (
          <div className="w-full h-full grid place-items-center text-text-muted">
            <FileText size={16} />
          </div>
        )}
      </div>

      <div className="flex-1 min-w-0">
        <h3 className="font-display atelier-display text-[1rem] font-semibold leading-tight tracking-tight text-foreground truncate">
          {project.title}
        </h3>
        {crumb ? (
          <div className="font-mono text-[0.59375rem] uppercase tracking-wider text-text-muted mt-0.5 truncate">
            {crumb}
          </div>
        ) : null}
      </div>

      <span
        className={`atelier-badge hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[0.59375rem] font-mono font-semibold uppercase tracking-wider flex-shrink-0 ${badge.cls}`}
      >
        <span className="w-[5px] h-[5px] rounded-full bg-current" />
        {badge.label}
      </span>

      <div className="hidden md:flex items-center gap-3 font-mono text-[0.625rem] text-text-secondary flex-shrink-0">
        <span className="inline-flex items-center gap-1">
          <Film size={11} className="text-text-muted" />
          {t("shotCount", { count: frameCount })}
        </span>
        <span className="inline-flex items-center gap-1">
          <Clock size={11} className="text-text-muted" />
          {sceneCount}
        </span>
      </div>

      <ProjectDeleteButton project={project} onDelete={onDelete} />
    </div>
  );
}
