import clsx from "clsx";

export const GLOBAL_PAGE_TITLE_CLASS =
  "font-display atelier-display text-[1.625rem] font-semibold leading-tight tracking-tight text-foreground md:text-[2.125rem]";

export default function GlobalPageTitle({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <h1 data-global-page-title className={clsx(GLOBAL_PAGE_TITLE_CLASS, className)}>
      {children}
    </h1>
  );
}
