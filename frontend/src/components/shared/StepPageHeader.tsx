import type { ReactNode } from "react";

export interface StepPageHeaderProps {
    /** Localized name of the current editor step. */
    title: string;
    /** Right-aligned interactive controls owned by the current step. */
    trailing?: ReactNode;
}

export default function StepPageHeader({
    title,
    trailing,
}: StepPageHeaderProps) {
    return (
        <header className="shrink-0 border-b border-border-subtle bg-surface px-4 py-3 sm:px-6">
            <div className="flex min-h-10 flex-wrap items-center justify-between gap-3">
                <h1 className="min-w-0 flex-1 font-display text-xl font-semibold leading-tight tracking-[-0.015em] text-foreground sm:text-2xl">
                    {title}
                </h1>
                {trailing ? (
                    <div className="flex max-w-full flex-wrap items-center justify-end gap-2">
                        {trailing}
                    </div>
                ) : null}
            </div>
        </header>
    );
}
